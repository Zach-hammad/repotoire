"""Style enforcer for generating LLM instructions from detected conventions."""

from typing import List, Optional

from .models import StyleProfile, StyleRule


class StyleEnforcer:
    """Generates style instructions for LLM-based code generation."""

    def __init__(self, profile: StyleProfile, confidence_threshold: float = 0.6):
        """Initialize style enforcer.

        Args:
            profile: The detected style profile
            confidence_threshold: Minimum confidence for including a rule (0.0-1.0)
        """
        self.profile = profile
        self.confidence_threshold = confidence_threshold

    def get_style_instructions(self) -> str:
        """Generate markdown instructions for LLM code generation.

        Only includes rules with confidence above the threshold.

        Returns:
            Markdown formatted style instructions
        """
        lines = ["## Code Style Requirements (from codebase analysis)", ""]

        # Naming conventions
        naming_rules = self._get_naming_instructions()
        if naming_rules:
            lines.extend(naming_rules)

        # Docstring style
        docstring_instruction = self._get_docstring_instruction()
        if docstring_instruction:
            lines.append(docstring_instruction)

        # Line length
        line_length_instruction = self._get_line_length_instruction()
        if line_length_instruction:
            lines.append(line_length_instruction)

        # Type hints
        type_hint_instruction = self._get_type_hint_instruction()
        if type_hint_instruction:
            lines.append(type_hint_instruction)

        # Import organization
        import_instruction = self._get_import_instruction()
        if import_instruction:
            lines.append(import_instruction)

        # If no rules meet threshold, return minimal guidance
        if len(lines) <= 2:
            return "## Code Style Requirements\n\nFollow Python PEP 8 conventions."

        return "\n".join(lines)

    def get_rule_summary(self) -> List[dict]:
        """Get a summary of all detected rules with their confidence.

        Returns:
            List of rule dictionaries for display
        """
        rules = []

        # Core rules
        core_rules = [
            ("Function naming", self.profile.function_naming),
            ("Class naming", self.profile.class_naming),
            ("Variable naming", self.profile.variable_naming),
            ("Docstring style", self.profile.docstring_style),
            ("Max line length", self.profile.max_line_length),
        ]

        for name, rule in core_rules:
            rules.append({
                "name": name,
                "value": rule.value,
                "confidence": rule.confidence,
                "sample_count": rule.sample_count,
                "included": rule.is_high_confidence(self.confidence_threshold),
            })

        # Optional rules
        if self.profile.constant_naming:
            rules.append({
                "name": "Constant naming",
                "value": self.profile.constant_naming.value,
                "confidence": self.profile.constant_naming.confidence,
                "sample_count": self.profile.constant_naming.sample_count,
                "included": self.profile.constant_naming.is_high_confidence(
                    self.confidence_threshold
                ),
            })

        if self.profile.import_style:
            rules.append({
                "name": "Import style",
                "value": self.profile.import_style.value,
                "confidence": self.profile.import_style.confidence,
                "sample_count": self.profile.import_style.sample_count,
                "included": self.profile.import_style.is_high_confidence(
                    self.confidence_threshold
                ),
            })

        # Type hint coverage (special case - not a StyleRule)
        rules.append({
            "name": "Type hint coverage",
            "value": f"{self.profile.type_hint_coverage:.0%}",
            "confidence": None,  # N/A for coverage metrics
            "sample_count": None,
            "included": True,  # Always included as guidance
        })

        return rules

    def _get_naming_instructions(self) -> List[str]:
        """Get naming convention instructions."""
        instructions = []

        # Function naming
        if self.profile.function_naming.is_high_confidence(self.confidence_threshold):
            rule = self.profile.function_naming
            instruction = self._format_naming_instruction(
                "function", rule.value, rule.confidence
            )
            if instruction:
                instructions.append(instruction)

        # Class naming
        if self.profile.class_naming.is_high_confidence(self.confidence_threshold):
            rule = self.profile.class_naming
            instruction = self._format_naming_instruction(
                "class", rule.value, rule.confidence
            )
            if instruction:
                instructions.append(instruction)

        # Variable naming
        if self.profile.variable_naming.is_high_confidence(self.confidence_threshold):
            rule = self.profile.variable_naming
            instruction = self._format_naming_instruction(
                "variable", rule.value, rule.confidence
            )
            if instruction:
                instructions.append(instruction)

        # Constant naming
        if (
            self.profile.constant_naming
            and self.profile.constant_naming.is_high_confidence(self.confidence_threshold)
        ):
            rule = self.profile.constant_naming
            instruction = self._format_naming_instruction(
                "constant", rule.value, rule.confidence
            )
            if instruction:
                instructions.append(instruction)

        return instructions

    def _format_naming_instruction(
        self, kind: str, convention: str, confidence: float
    ) -> Optional[str]:
        """Format a naming convention instruction.

        Args:
            kind: Type of identifier (function, class, variable, constant)
            convention: Naming convention
            confidence: Confidence percentage

        Returns:
            Formatted instruction string or None
        """
        convention_descriptions = {
            "snake_case": "snake_case",
            "PascalCase": "PascalCase",
            "camelCase": "camelCase",
            "SCREAMING_SNAKE_CASE": "SCREAMING_SNAKE_CASE",
        }

        if convention not in convention_descriptions:
            return None

        pct = f"{confidence:.0%}"
        return f"- Use {convention_descriptions[convention]} for {kind} names (detected in {pct} of codebase)"

    def _get_docstring_instruction(self) -> Optional[str]:
        """Get docstring style instruction."""
        if not self.profile.docstring_style.is_high_confidence(self.confidence_threshold):
            return None

        style = self.profile.docstring_style.value
        style_descriptions = {
            "google": "Google-style docstrings (Args:, Returns:, Raises:)",
            "numpy": "NumPy-style docstrings (Parameters, Returns with dashes)",
            "sphinx": "Sphinx-style docstrings (:param, :returns:, :raises:)",
            "simple": "Simple one-line docstrings",
        }

        if style in style_descriptions:
            return f"- Use {style_descriptions[style]}"

        return None

    def _get_line_length_instruction(self) -> Optional[str]:
        """Get line length instruction."""
        if not self.profile.max_line_length.is_high_confidence(self.confidence_threshold):
            return None

        length = self.profile.max_line_length.value
        return f"- Keep lines under {length} characters"

    def _get_type_hint_instruction(self) -> Optional[str]:
        """Get type hint instruction based on coverage."""
        coverage = self.profile.type_hint_coverage

        if coverage >= 0.8:
            return f"- Add type hints (codebase has {coverage:.0%} coverage)"
        elif coverage >= 0.5:
            return f"- Consider adding type hints (codebase has {coverage:.0%} coverage)"
        elif coverage >= 0.2:
            return f"- Type hints optional (codebase has {coverage:.0%} coverage)"
        # Low coverage - don't mention type hints
        return None

    def _get_import_instruction(self) -> Optional[str]:
        """Get import organization instruction."""
        if not self.profile.import_style:
            return None

        if not self.profile.import_style.is_high_confidence(self.confidence_threshold):
            return None

        style = self.profile.import_style.value
        if style == "grouped":
            return "- Organize imports into groups (stdlib, third-party, local)"
        elif style == "ungrouped":
            return "- Keep imports together without blank line separators"

        return None
