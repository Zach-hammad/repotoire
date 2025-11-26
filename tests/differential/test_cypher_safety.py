"""
Differential tests for Cypher injection prevention.

Validates Python implementation matches Lean specification:
- lean/Repotoire/CypherSafety.lean

Properties verified:
- Safe character whitelist [a-zA-Z0-9_-]
- Injection character blocking
- Length bounds (0 < len <= 100)
- No special characters pass validation
"""

import string
from hypothesis import given, strategies as st, assume
from repotoire.validation import validate_identifier, ValidationError


# Lean-equivalent constants
MAX_LENGTH = 100
SAFE_CHARS = set(string.ascii_letters + string.digits + "_-")
INJECTION_CHARS = {"'", '"', ";", "{", "}", "/", "\\", "\n", "\r", " ", "\t"}


def lean_is_safe_char(c: str) -> bool:
    """
    Mirror Lean's is_safe_char predicate.

    Lean:
        is_safe_char c = c.isAlpha || c.isDigit || c == '_' || c == '-'
    """
    return c in SAFE_CHARS


def lean_is_injection_char(c: str) -> bool:
    """
    Mirror Lean's is_injection_char predicate.

    Lean: Set of characters that could enable injection attacks.
    """
    return c in INJECTION_CHARS


def lean_is_safe_identifier(s: str) -> bool:
    """
    Mirror Lean's is_safe_identifier predicate.

    Lean:
        is_safe_identifier s =
            s.all is_safe_char &&
            s.length <= MAX_LENGTH &&
            s.length > 0
    """
    if len(s) == 0 or len(s) > MAX_LENGTH:
        return False
    return all(lean_is_safe_char(c) for c in s)


# Strategy for generating safe identifiers
safe_char_strategy = st.sampled_from(list(SAFE_CHARS))
safe_identifier_strategy = st.text(
    alphabet=list(SAFE_CHARS),
    min_size=1,
    max_size=MAX_LENGTH,
)

# Strategy for generating potentially unsafe strings
any_printable_strategy = st.text(
    alphabet=st.characters(blacklist_categories=("Cs",)),
    min_size=0,
    max_size=MAX_LENGTH + 10,
)


class TestCypherSafetyProperties:
    """Property-based tests for Cypher injection prevention."""

    @given(identifier=safe_identifier_strategy)
    def test_safe_identifiers_always_valid(self, identifier: str):
        """
        Lean theorem: safe_identifiers_valid
        Proves: is_safe_identifier s -> validate_identifier s = Ok s
        """
        assume(len(identifier) > 0)

        # Lean spec says this should be safe
        assert lean_is_safe_identifier(identifier), f"Expected safe: {identifier}"

        # Python should accept it
        result = validate_identifier(identifier)
        assert result == identifier, f"Safe identifier rejected: {identifier}"

    @given(identifier=any_printable_strategy)
    def test_validation_matches_lean_spec(self, identifier: str):
        """
        Differential test: Python validation matches Lean is_safe_identifier.
        """
        lean_safe = lean_is_safe_identifier(identifier)

        try:
            result = validate_identifier(identifier)
            python_safe = (result == identifier)
        except ValidationError:
            python_safe = False

        assert python_safe == lean_safe, \
            f"Mismatch for '{identifier}': Python={python_safe}, Lean={lean_safe}"

    @given(identifier=st.text(min_size=1, max_size=50))
    def test_injection_chars_blocked(self, identifier: str):
        """
        Lean theorem: no_injection_chars
        Proves: Identifiers with injection chars are rejected.
        """
        has_injection = any(lean_is_injection_char(c) for c in identifier)

        if has_injection:
            try:
                validate_identifier(identifier)
                assert False, f"Injection char not blocked: {identifier!r}"
            except ValidationError:
                pass  # Expected

    @given(char=st.sampled_from(list(INJECTION_CHARS)))
    def test_each_injection_char_blocked(self, char: str):
        """
        Test each injection character individually.
        """
        test_id = f"test{char}test"
        try:
            validate_identifier(test_id)
            assert False, f"Injection char '{char!r}' not blocked"
        except ValidationError:
            pass  # Expected

    @given(length=st.integers(min_value=101, max_value=200))
    def test_length_bounded(self, length: int):
        """
        Lean theorem: length_bounded
        Proves: Valid identifiers have length <= 100.
        """
        long_id = "a" * length
        assert not lean_is_safe_identifier(long_id), "Lean should reject long identifier"

        try:
            validate_identifier(long_id)
            assert False, f"Length {length} not rejected"
        except ValidationError:
            pass  # Expected

    def test_empty_rejected(self):
        """
        Lean theorem: empty_not_safe
        Proves: Empty string is not a valid identifier.
        """
        assert not lean_is_safe_identifier(""), "Lean should reject empty"

        try:
            validate_identifier("")
            assert False, "Empty string not rejected"
        except ValidationError:
            pass  # Expected

    def test_whitespace_only_rejected(self):
        """
        Lean theorem: whitespace_not_safe
        Proves: Whitespace-only strings are not valid identifiers.
        """
        for ws in [" ", "  ", "\t", "\n", "   "]:
            assert not lean_is_safe_identifier(ws), f"Lean should reject '{ws!r}'"

            try:
                validate_identifier(ws)
                assert False, f"Whitespace '{ws!r}' not rejected"
            except ValidationError:
                pass  # Expected


class TestCypherSafetyInjectionPayloads:
    """Test known injection payloads are blocked."""

    INJECTION_PAYLOADS = [
        "'; DROP DATABASE",
        "foo} RETURN *",
        "x//comment",
        "a\nb",
        "test' OR '1'='1",
        "user; MATCH (n) DETACH DELETE n",
        "${injection}",
        "{{template}}",
        "admin'--",
        "test\x00null",
    ]

    def test_known_payloads_blocked(self):
        """
        Lean examples: All injection payloads must be rejected.
        """
        for payload in self.INJECTION_PAYLOADS:
            assert not lean_is_safe_identifier(payload), \
                f"Lean should reject payload: {payload!r}"

            try:
                validate_identifier(payload)
                assert False, f"Payload not blocked: {payload!r}"
            except ValidationError:
                pass  # Expected


class TestCypherSafetySafeExamples:
    """Test known safe identifiers are accepted."""

    SAFE_IDENTIFIERS = [
        "myProjection",
        "test123",
        "my_graph",
        "my-projection",
        "test123_data-v2",
        "a",
        "A",
        "0",
        "_",
        "-",
        "a" * 100,  # Max length
        "CamelCaseIdentifier",
        "snake_case_identifier",
        "kebab-case-identifier",
        "MixedCase_with-all123",
    ]

    def test_known_safe_accepted(self):
        """
        Lean examples: All safe identifiers must be accepted.
        """
        for identifier in self.SAFE_IDENTIFIERS:
            assert lean_is_safe_identifier(identifier), \
                f"Lean should accept: {identifier}"

            result = validate_identifier(identifier)
            assert result == identifier, f"Safe identifier rejected: {identifier}"
