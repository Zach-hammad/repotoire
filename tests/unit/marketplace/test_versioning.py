"""Unit tests for marketplace versioning and dependency resolution."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from repotoire.marketplace.versioning import (
    ConstraintType,
    DependencyCycleError,
    Lockfile,
    LockfileEntry,
    NoMatchingVersionError,
    UpdateType,
    VersionConstraint,
    compare_versions,
    compute_integrity,
    parse_version,
    version_gte,
    version_lt,
)


class TestVersionParsing:
    """Tests for version string parsing."""

    def test_parse_valid_version(self):
        """Test parsing a valid semantic version."""
        major, minor, patch, pre = parse_version("1.2.3")
        assert major == 1
        assert minor == 2
        assert patch == 3
        assert pre is None

    def test_parse_version_with_prerelease(self):
        """Test parsing a version with prerelease tag."""
        major, minor, patch, pre = parse_version("1.2.3-beta.1")
        assert major == 1
        assert minor == 2
        assert patch == 3
        assert pre == "beta.1"

    def test_parse_version_with_build_metadata(self):
        """Test parsing a version with build metadata."""
        major, minor, patch, pre = parse_version("1.2.3+build.123")
        assert major == 1
        assert minor == 2
        assert patch == 3
        assert pre is None

    def test_parse_invalid_version_raises(self):
        """Test that invalid version strings raise ValueError."""
        with pytest.raises(ValueError, match="Invalid version"):
            parse_version("not-a-version")

    def test_parse_incomplete_version_raises(self):
        """Test that incomplete versions raise ValueError."""
        with pytest.raises(ValueError, match="Invalid version"):
            parse_version("1.2")


class TestVersionComparison:
    """Tests for version comparison functions."""

    def test_compare_versions_equal(self):
        """Test comparing equal versions."""
        assert compare_versions("1.2.3", "1.2.3") == 0

    def test_compare_versions_major_diff(self):
        """Test comparing versions with different major."""
        assert compare_versions("2.0.0", "1.0.0") == 1
        assert compare_versions("1.0.0", "2.0.0") == -1

    def test_compare_versions_minor_diff(self):
        """Test comparing versions with different minor."""
        assert compare_versions("1.2.0", "1.1.0") == 1
        assert compare_versions("1.1.0", "1.2.0") == -1

    def test_compare_versions_patch_diff(self):
        """Test comparing versions with different patch."""
        assert compare_versions("1.2.4", "1.2.3") == 1
        assert compare_versions("1.2.3", "1.2.4") == -1

    def test_compare_versions_prerelease_vs_stable(self):
        """Test that stable versions are greater than prereleases."""
        assert compare_versions("1.2.3", "1.2.3-beta.1") == 1
        assert compare_versions("1.2.3-beta.1", "1.2.3") == -1

    def test_version_gte(self):
        """Test version_gte function."""
        assert version_gte("1.2.3", "1.2.3")
        assert version_gte("1.2.4", "1.2.3")
        assert not version_gte("1.2.2", "1.2.3")

    def test_version_lt(self):
        """Test version_lt function."""
        assert version_lt("1.2.2", "1.2.3")
        assert not version_lt("1.2.3", "1.2.3")
        assert not version_lt("1.2.4", "1.2.3")


class TestCaretConstraint:
    """Tests for caret (^) version constraints."""

    def test_caret_constraint_parse(self):
        """Test parsing a caret constraint."""
        c = VersionConstraint.parse("^1.2.3")
        assert c.constraint_type == ConstraintType.CARET
        assert c.min_version == "1.2.3"
        assert c.max_version == "2.0.0"

    def test_caret_constraint_satisfies_same_version(self):
        """Test that exact version satisfies caret constraint."""
        c = VersionConstraint.parse("^1.2.3")
        assert c.satisfies("1.2.3")

    def test_caret_constraint_satisfies_higher_patch(self):
        """Test that higher patch version satisfies."""
        c = VersionConstraint.parse("^1.2.3")
        assert c.satisfies("1.2.4")
        assert c.satisfies("1.2.99")

    def test_caret_constraint_satisfies_higher_minor(self):
        """Test that higher minor version satisfies."""
        c = VersionConstraint.parse("^1.2.3")
        assert c.satisfies("1.3.0")
        assert c.satisfies("1.9.9")

    def test_caret_constraint_rejects_lower_version(self):
        """Test that lower versions are rejected."""
        c = VersionConstraint.parse("^1.2.3")
        assert not c.satisfies("1.2.2")
        assert not c.satisfies("1.0.0")

    def test_caret_constraint_rejects_next_major(self):
        """Test that next major version is rejected."""
        c = VersionConstraint.parse("^1.2.3")
        assert not c.satisfies("2.0.0")
        assert not c.satisfies("3.0.0")

    def test_caret_constraint_zero_major(self):
        """Test caret with 0.x.y versions."""
        # ^0.2.3 allows 0.2.3 <= v < 0.3.0
        c = VersionConstraint.parse("^0.2.3")
        assert c.satisfies("0.2.3")
        assert c.satisfies("0.2.9")
        assert not c.satisfies("0.3.0")

    def test_caret_constraint_zero_minor(self):
        """Test caret with 0.0.y versions."""
        # ^0.0.3 allows only 0.0.3
        c = VersionConstraint.parse("^0.0.3")
        assert c.satisfies("0.0.3")
        assert not c.satisfies("0.0.4")


class TestTildeConstraint:
    """Tests for tilde (~) version constraints."""

    def test_tilde_constraint_parse(self):
        """Test parsing a tilde constraint."""
        c = VersionConstraint.parse("~1.2.3")
        assert c.constraint_type == ConstraintType.TILDE
        assert c.min_version == "1.2.3"
        assert c.max_version == "1.3.0"

    def test_tilde_constraint_satisfies_same_version(self):
        """Test that exact version satisfies tilde constraint."""
        c = VersionConstraint.parse("~1.2.3")
        assert c.satisfies("1.2.3")

    def test_tilde_constraint_satisfies_higher_patch(self):
        """Test that higher patch version satisfies."""
        c = VersionConstraint.parse("~1.2.3")
        assert c.satisfies("1.2.4")
        assert c.satisfies("1.2.99")

    def test_tilde_constraint_rejects_lower_version(self):
        """Test that lower versions are rejected."""
        c = VersionConstraint.parse("~1.2.3")
        assert not c.satisfies("1.2.2")

    def test_tilde_constraint_rejects_higher_minor(self):
        """Test that higher minor version is rejected."""
        c = VersionConstraint.parse("~1.2.3")
        assert not c.satisfies("1.3.0")
        assert not c.satisfies("1.4.0")


class TestExactConstraint:
    """Tests for exact version constraints."""

    def test_exact_constraint_parse(self):
        """Test parsing an exact version."""
        c = VersionConstraint.parse("1.2.3")
        assert c.constraint_type == ConstraintType.EXACT
        assert c.min_version == "1.2.3"
        assert c.max_version is None

    def test_exact_constraint_satisfies_only_exact(self):
        """Test that only exact version matches."""
        c = VersionConstraint.parse("1.2.3")
        assert c.satisfies("1.2.3")
        assert not c.satisfies("1.2.4")
        assert not c.satisfies("1.2.2")
        assert not c.satisfies("1.3.0")


class TestRangeConstraint:
    """Tests for range version constraints."""

    def test_range_constraint_parse(self):
        """Test parsing a range constraint."""
        c = VersionConstraint.parse(">=1.0.0 <2.0.0")
        assert c.constraint_type == ConstraintType.RANGE
        assert c.min_version == "1.0.0"
        assert c.max_version == "2.0.0"

    def test_range_constraint_satisfies_min(self):
        """Test that minimum version satisfies."""
        c = VersionConstraint.parse(">=1.0.0 <2.0.0")
        assert c.satisfies("1.0.0")

    def test_range_constraint_satisfies_middle(self):
        """Test that middle versions satisfy."""
        c = VersionConstraint.parse(">=1.0.0 <2.0.0")
        assert c.satisfies("1.5.0")
        assert c.satisfies("1.9.9")

    def test_range_constraint_rejects_max(self):
        """Test that max version is rejected (exclusive)."""
        c = VersionConstraint.parse(">=1.0.0 <2.0.0")
        assert not c.satisfies("2.0.0")

    def test_range_constraint_rejects_below_min(self):
        """Test that versions below min are rejected."""
        c = VersionConstraint.parse(">=1.0.0 <2.0.0")
        assert not c.satisfies("0.9.9")


class TestLatestConstraint:
    """Tests for 'latest' version constraints."""

    def test_latest_constraint_parse(self):
        """Test parsing 'latest' constraint."""
        c = VersionConstraint.parse("latest")
        assert c.constraint_type == ConstraintType.LATEST

    def test_latest_constraint_satisfies_any(self):
        """Test that latest satisfies any version."""
        c = VersionConstraint.parse("latest")
        assert c.satisfies("1.0.0")
        assert c.satisfies("99.99.99")

    def test_star_constraint_parse(self):
        """Test parsing '*' as latest."""
        c = VersionConstraint.parse("*")
        assert c.constraint_type == ConstraintType.LATEST


class TestLockfile:
    """Tests for lockfile management."""

    def test_lockfile_save_and_load(self, tmp_path: Path):
        """Test saving and loading a lockfile."""
        lockfile = Lockfile()
        lockfile.entries["@test/pkg"] = LockfileEntry(
            slug="@test/pkg",
            version="1.0.0",
            resolved_url="https://example.com/pkg-1.0.0.tar.gz",
            integrity="sha256-abc123",
            dependencies=["@test/dep"],
        )

        lockfile.save(tmp_path)

        loaded = Lockfile.load(tmp_path)
        assert loaded is not None
        assert "@test/pkg" in loaded.entries
        assert loaded.entries["@test/pkg"].version == "1.0.0"
        assert loaded.entries["@test/pkg"].dependencies == ["@test/dep"]

    def test_lockfile_is_satisfied(self):
        """Test checking if locked version satisfies constraint."""
        lockfile = Lockfile()
        lockfile.entries["@test/pkg"] = LockfileEntry(
            slug="@test/pkg",
            version="1.5.0",
            resolved_url="",
            integrity="",
        )

        assert lockfile.is_satisfied("@test/pkg", "^1.0.0")
        assert lockfile.is_satisfied("@test/pkg", "~1.5.0")
        assert not lockfile.is_satisfied("@test/pkg", "^2.0.0")

    def test_lockfile_load_nonexistent_returns_none(self, tmp_path: Path):
        """Test that loading nonexistent lockfile returns None."""
        result = Lockfile.load(tmp_path / "nonexistent")
        assert result is None


class TestUpdateType:
    """Tests for update type classification."""

    def test_major_update(self):
        """Test major version update classification."""
        from repotoire.marketplace.versioning import AssetUpdater

        updater = AssetUpdater.__new__(AssetUpdater)
        result = updater._classify_update("1.0.0", "2.0.0")
        assert result == UpdateType.MAJOR

    def test_minor_update(self):
        """Test minor version update classification."""
        from repotoire.marketplace.versioning import AssetUpdater

        updater = AssetUpdater.__new__(AssetUpdater)
        result = updater._classify_update("1.0.0", "1.1.0")
        assert result == UpdateType.MINOR

    def test_patch_update(self):
        """Test patch version update classification."""
        from repotoire.marketplace.versioning import AssetUpdater

        updater = AssetUpdater.__new__(AssetUpdater)
        result = updater._classify_update("1.0.0", "1.0.1")
        assert result == UpdateType.PATCH


class TestIntegrity:
    """Tests for integrity hash computation."""

    def test_compute_integrity(self):
        """Test computing SHA256 integrity hash."""
        content = b"hello world"
        integrity = compute_integrity(content)
        assert integrity.startswith("sha256-")
        # SHA256 of "hello world" is known
        expected = "sha256-b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9"
        assert integrity == expected

    def test_compute_integrity_different_content(self):
        """Test that different content produces different hash."""
        hash1 = compute_integrity(b"content1")
        hash2 = compute_integrity(b"content2")
        assert hash1 != hash2


class TestEdgeCases:
    """Tests for edge cases and error handling."""

    def test_invalid_constraint_raises(self):
        """Test that invalid constraint string raises ValueError."""
        with pytest.raises(ValueError, match="Invalid version"):
            VersionConstraint.parse("not-valid")

    def test_constraint_str(self):
        """Test string representation of constraint."""
        c = VersionConstraint.parse("^1.2.3")
        assert str(c) == "^1.2.3"

    def test_constraint_with_spaces(self):
        """Test that constraint with spaces is handled."""
        c = VersionConstraint.parse("  ^1.2.3  ")
        assert c.constraint_type == ConstraintType.CARET
        assert c.min_version == "1.2.3"
