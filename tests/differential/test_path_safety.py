"""
Differential tests for path safety and containment.

Validates Python implementation matches Lean specification:
- lean/Repotoire/PathSafety.lean

Properties verified:
- Path prefix reflexivity (every path is prefix of itself)
- Path prefix transitivity
- Subpath containment (subpaths are within parent)
- Traversal attack prevention (.. and . components)
- Different root rejection
"""

from pathlib import Path
from hypothesis import given, strategies as st, assume
import os


def lean_is_prefix(parent: list, child: list) -> bool:
    """
    Mirror Lean's is_prefix function.

    Lean:
        is_prefix parent child =
            match parent, child with
            | [], _ => true
            | _, [] => false
            | p :: ps, c :: cs => p == c && is_prefix ps cs
    """
    if len(parent) == 0:
        return True
    if len(child) == 0:
        return False
    if parent[0] != child[0]:
        return False
    return lean_is_prefix(parent[1:], child[1:])


def lean_is_within_repo(file_path: list, repo_path: list) -> bool:
    """
    Mirror Lean's is_within_repo function.

    Lean: is_within_repo file repo = (relative_to file repo).isSome
    """
    return lean_is_prefix(repo_path, file_path)


def lean_is_traversal_component(s: str) -> bool:
    """
    Mirror Lean's is_traversal_component predicate.

    Lean: is_traversal_component s = s == ".." || s == "."
    """
    return s == ".." or s == "."


def lean_has_no_traversal(path: list) -> bool:
    """
    Mirror Lean's has_no_traversal predicate.

    Lean: has_no_traversal p = p.all (fun c => !is_traversal_component c)
    """
    return all(not lean_is_traversal_component(c) for c in path)


def path_to_components(p: Path) -> list:
    """Convert a Path to a list of components."""
    return list(p.parts[1:]) if p.parts and p.parts[0] == "/" else list(p.parts)


# Strategy for path components (safe names only)
safe_component = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyz0123456789_-",
    min_size=1,
    max_size=20,
)

# Strategy for path component lists
path_components_strategy = st.lists(safe_component, min_size=0, max_size=10)


class TestPathPrefixProperties:
    """Property-based tests for path prefix operations."""

    @given(path=path_components_strategy)
    def test_prefix_reflexive(self, path: list):
        """
        Lean theorem: is_prefix_refl
        Proves: is_prefix p p = true
        """
        assert lean_is_prefix(path, path), f"Reflexivity failed for {path}"

    @given(
        a=path_components_strategy,
        b=path_components_strategy,
        c=path_components_strategy,
    )
    def test_prefix_transitive(self, a: list, b: list, c: list):
        """
        Lean theorem: is_prefix_trans
        Proves: is_prefix a b && is_prefix b c -> is_prefix a c
        """
        if lean_is_prefix(a, b) and lean_is_prefix(b, c):
            assert lean_is_prefix(a, c), \
                f"Transitivity failed: {a} prefix of {b}, {b} prefix of {c}, but {a} not prefix of {c}"

    @given(
        parent=path_components_strategy,
        suffix=path_components_strategy,
    )
    def test_prefix_append(self, parent: list, suffix: list):
        """
        Lean theorem: is_prefix_append
        Proves: is_prefix parent (parent ++ suffix) = true
        """
        child = parent + suffix
        assert lean_is_prefix(parent, child), \
            f"Prefix append failed: {parent} not prefix of {child}"


class TestPathContainmentProperties:
    """Property-based tests for path containment."""

    @given(path=path_components_strategy)
    def test_path_within_self(self, path: list):
        """
        Lean theorem: path_within_self
        Proves: is_within_repo p p = true
        """
        assert lean_is_within_repo(path, path), f"Path not within self: {path}"

    @given(
        parent=path_components_strategy,
        suffix=path_components_strategy,
    )
    def test_subpath_within_parent(self, parent: list, suffix: list):
        """
        Lean theorem: subpath_within_parent
        Proves: is_within_repo (parent ++ suffix) parent = true
        """
        assume(len(parent) > 0)  # Need non-empty parent
        child = parent + suffix
        assert lean_is_within_repo(child, parent), \
            f"Subpath {child} not within parent {parent}"

    @given(file=path_components_strategy)
    def test_root_contains_all(self, file: list):
        """
        Lean theorem: root_contains_all
        Proves: is_within_repo file [] = true
        """
        assert lean_is_within_repo(file, []), f"Root should contain {file}"

    @given(repo=path_components_strategy)
    def test_shorter_path_rejected(self, repo: list):
        """
        Lean theorem: shorter_path_rejected
        Proves: repo != [] -> is_within_repo [] repo = false
        """
        assume(len(repo) > 0)
        assert not lean_is_within_repo([], repo), \
            f"Empty path should not be within {repo}"


class TestPathTraversalPrevention:
    """Property-based tests for path traversal attack prevention."""

    @given(
        prefix=path_components_strategy,
        suffix=path_components_strategy,
    )
    def test_dotdot_component_unsafe(self, prefix: list, suffix: list):
        """
        Lean theorem: dotdot_unsafe
        Proves: has_no_traversal path_with_dotdot = false
        """
        path_with_dotdot = prefix + [".."] + suffix
        assert not lean_has_no_traversal(path_with_dotdot), \
            f"Path with '..' should be unsafe: {path_with_dotdot}"

    @given(
        prefix=path_components_strategy,
        suffix=path_components_strategy,
    )
    def test_dot_component_unsafe(self, prefix: list, suffix: list):
        """
        Lean theorem: dot_unsafe
        Proves: has_no_traversal path_with_dot = false
        """
        path_with_dot = prefix + ["."] + suffix
        assert not lean_has_no_traversal(path_with_dot), \
            f"Path with '.' should be unsafe: {path_with_dot}"

    @given(path=path_components_strategy)
    def test_safe_path_no_traversal(self, path: list):
        """
        Safe paths generated without traversal components should pass.
        """
        # Our strategy generates safe components only
        assert lean_has_no_traversal(path), f"Safe path should have no traversal: {path}"


class TestPathContainmentWithPython:
    """Differential tests comparing Lean spec to Python Path.is_relative_to()."""

    @given(
        repo_parts=path_components_strategy,
        suffix_parts=path_components_strategy,
    )
    def test_python_matches_lean_for_subpaths(self, repo_parts: list, suffix_parts: list):
        """
        Differential test: Python is_relative_to matches Lean is_within_repo for subpaths.
        """
        assume(len(repo_parts) > 0)

        repo_path = Path("/") / Path(*repo_parts) if repo_parts else Path("/")
        file_path = repo_path / Path(*suffix_parts) if suffix_parts else repo_path

        # Lean says subpath is within parent
        lean_result = lean_is_within_repo(
            path_to_components(file_path),
            path_to_components(repo_path)
        )

        # Python should agree
        python_result = file_path.is_relative_to(repo_path)

        assert python_result == lean_result, \
            f"Mismatch: repo={repo_path}, file={file_path}, Python={python_result}, Lean={lean_result}"


class TestPathContainmentExamples:
    """Explicit examples matching Lean theorems."""

    def test_valid_file_contained(self):
        """
        Lean theorem: valid_file_contained
        """
        repo = ["home", "user", "myrepo"]
        valid = ["home", "user", "myrepo", "src", "main.py"]
        assert lean_is_within_repo(valid, repo)

    def test_attack_file_blocked(self):
        """
        Lean theorem: attack_file_blocked
        """
        repo = ["home", "user", "myrepo"]
        attack = ["etc", "passwd"]
        assert not lean_is_within_repo(attack, repo)

    def test_sibling_attack_blocked(self):
        """
        Lean theorem: sibling_attack_blocked
        """
        repo = ["home", "user", "myrepo"]
        sibling = ["home", "user", "otherrepo", "secrets.txt"]
        assert not lean_is_within_repo(sibling, repo)

    def test_different_root_not_contained(self):
        """
        Lean theorem: different_root_not_contained
        """
        repo = ["home", "user", "repo"]
        different = ["var", "data", "file.txt"]
        assert not lean_is_within_repo(different, repo)

    def test_empty_path_safe(self):
        """
        Lean theorem: empty_path_safe
        """
        assert lean_has_no_traversal([])

    def test_safe_path_example(self):
        """
        Lean theorem: safe_path_example
        """
        path = ["home", "user", "repo", "src"]
        assert lean_has_no_traversal(path)
