"""Dependency resolution and version management for marketplace assets.

This module provides npm-style version constraint parsing, dependency resolution
with cycle detection, lockfile management, and update checking.

Version Constraint Examples:
    ^1.2.3  ->  >=1.2.3 <2.0.0 (caret, compatible with major)
    ~1.2.3  ->  >=1.2.3 <1.3.0 (tilde, patch updates only)
    >=1.0.0 <2.0.0  ->  explicit range
    1.2.3   ->  exact version match
    latest  ->  always use latest stable version
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from repotoire.logging_config import get_logger

if TYPE_CHECKING:
    from repotoire.cli.marketplace_client import MarketplaceAPIClient

logger = get_logger(__name__)

# Lockfile name
LOCKFILE_NAME = "repotoire.lock"

# Semver regex pattern
SEMVER_PATTERN = re.compile(
    r"^(?P<major>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)\.(?P<patch>0|[1-9]\d*)"
    r"(?:-(?P<prerelease>(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)"
    r"(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?"
    r"(?:\+(?P<buildmetadata>[0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*))?$"
)


# =============================================================================
# Exceptions
# =============================================================================


class DependencyError(Exception):
    """Base exception for dependency resolution errors."""

    pass


class DependencyCycleError(DependencyError):
    """Circular dependency detected."""

    def __init__(self, cycle: list[str]):
        self.cycle = cycle
        super().__init__(f"Circular dependency: {' -> '.join(cycle)}")


class NoMatchingVersionError(DependencyError):
    """No version satisfies constraint."""

    def __init__(self, slug: str, constraint: str, available: list[str]):
        self.slug = slug
        self.constraint = constraint
        self.available = available
        super().__init__(
            f"No version of {slug} satisfies {constraint}. "
            f"Available: {', '.join(available[:5])}"
        )


class ConflictingVersionsError(DependencyError):
    """Multiple dependencies require incompatible versions."""

    def __init__(self, slug: str, requirements: list[tuple[str, str]]):
        self.slug = slug
        self.requirements = requirements
        reqs_str = ", ".join(f"{parent} requires {ver}" for parent, ver in requirements)
        super().__init__(f"Version conflict for {slug}: {reqs_str}")


# =============================================================================
# Version Parsing and Comparison
# =============================================================================


def parse_version(version: str) -> tuple[int, int, int, str | None]:
    """Parse a semantic version string.

    Args:
        version: Version string (e.g., "1.2.3", "1.2.3-beta.1").

    Returns:
        Tuple of (major, minor, patch, prerelease).

    Raises:
        ValueError: If version string is invalid.
    """
    match = SEMVER_PATTERN.match(version)
    if not match:
        raise ValueError(f"Invalid version: {version}")

    return (
        int(match.group("major")),
        int(match.group("minor")),
        int(match.group("patch")),
        match.group("prerelease"),
    )


def compare_versions(v1: str, v2: str) -> int:
    """Compare two semantic versions.

    Args:
        v1: First version string.
        v2: Second version string.

    Returns:
        -1 if v1 < v2, 0 if v1 == v2, 1 if v1 > v2.
    """
    try:
        major1, minor1, patch1, pre1 = parse_version(v1)
        major2, minor2, patch2, pre2 = parse_version(v2)
    except ValueError:
        # Fallback to string comparison for invalid versions
        return -1 if v1 < v2 else (1 if v1 > v2 else 0)

    # Compare major.minor.patch
    if major1 != major2:
        return -1 if major1 < major2 else 1
    if minor1 != minor2:
        return -1 if minor1 < minor2 else 1
    if patch1 != patch2:
        return -1 if patch1 < patch2 else 1

    # Compare prerelease (None = stable > any prerelease)
    if pre1 is None and pre2 is not None:
        return 1  # stable > prerelease
    if pre1 is not None and pre2 is None:
        return -1  # prerelease < stable
    if pre1 is not None and pre2 is not None:
        return -1 if pre1 < pre2 else (1 if pre1 > pre2 else 0)

    return 0


def version_gte(v1: str, v2: str) -> bool:
    """Check if v1 >= v2."""
    return compare_versions(v1, v2) >= 0


def version_lt(v1: str, v2: str) -> bool:
    """Check if v1 < v2."""
    return compare_versions(v1, v2) < 0


# =============================================================================
# Version Constraints
# =============================================================================


class ConstraintType(Enum):
    """Type of version constraint."""

    EXACT = "exact"  # 1.2.3
    CARET = "caret"  # ^1.2.3 -> >=1.2.3 <2.0.0
    TILDE = "tilde"  # ~1.2.3 -> >=1.2.3 <1.3.0
    RANGE = "range"  # >=1.0.0 <2.0.0
    LATEST = "latest"  # Always latest stable


@dataclass
class VersionConstraint:
    """Represents a version constraint.

    Attributes:
        constraint_type: Type of constraint (caret, tilde, range, exact, latest).
        min_version: Minimum version (inclusive), or None for latest.
        max_version: Maximum version (exclusive), or None for no upper bound.
        original: Original constraint string.
    """

    constraint_type: ConstraintType
    min_version: str | None = None
    max_version: str | None = None
    original: str = ""

    @classmethod
    def parse(cls, spec: str) -> "VersionConstraint":
        """Parse a version constraint string.

        Args:
            spec: Version constraint (e.g., "^1.2.3", "~1.2.3", ">=1.0.0 <2.0.0").

        Returns:
            Parsed VersionConstraint.

        Examples:
            >>> VersionConstraint.parse("^1.2.3")  # >=1.2.3 <2.0.0
            >>> VersionConstraint.parse("~1.2.3")  # >=1.2.3 <1.3.0
            >>> VersionConstraint.parse("1.2.3")   # exact match
            >>> VersionConstraint.parse("latest")  # latest stable
        """
        spec = spec.strip()

        # Latest
        if spec.lower() == "latest" or spec == "*":
            return cls(
                constraint_type=ConstraintType.LATEST,
                original=spec,
            )

        # Caret constraint: ^1.2.3
        if spec.startswith("^"):
            version = spec[1:]
            major, minor, patch, _ = parse_version(version)

            # Calculate max version
            if major == 0:
                # ^0.x.y allows changes that do not modify left-most non-zero
                if minor == 0:
                    max_version = f"0.0.{patch + 1}"
                else:
                    max_version = f"0.{minor + 1}.0"
            else:
                max_version = f"{major + 1}.0.0"

            return cls(
                constraint_type=ConstraintType.CARET,
                min_version=version,
                max_version=max_version,
                original=spec,
            )

        # Tilde constraint: ~1.2.3
        if spec.startswith("~"):
            version = spec[1:]
            major, minor, patch, _ = parse_version(version)
            max_version = f"{major}.{minor + 1}.0"

            return cls(
                constraint_type=ConstraintType.TILDE,
                min_version=version,
                max_version=max_version,
                original=spec,
            )

        # Range constraint: >=1.0.0 <2.0.0 or >=1.0.0
        if ">=" in spec or "<" in spec or ">" in spec:
            min_ver = None
            max_ver = None

            # Parse >= or >
            gte_match = re.search(r">=\s*(\d+\.\d+\.\d+(?:-[a-zA-Z0-9.-]+)?)", spec)
            gt_match = re.search(r">\s*(\d+\.\d+\.\d+(?:-[a-zA-Z0-9.-]+)?)", spec)

            if gte_match:
                min_ver = gte_match.group(1)
            elif gt_match:
                # > is exclusive, so we need to find next version
                # For simplicity, we'll use the version as-is and adjust in satisfies()
                min_ver = gt_match.group(1)

            # Parse < or <=
            lt_match = re.search(r"<\s*(\d+\.\d+\.\d+(?:-[a-zA-Z0-9.-]+)?)", spec)
            lte_match = re.search(r"<=\s*(\d+\.\d+\.\d+(?:-[a-zA-Z0-9.-]+)?)", spec)

            if lt_match:
                max_ver = lt_match.group(1)
            elif lte_match:
                # <= is inclusive, we handle this in satisfies()
                max_ver = lte_match.group(1)

            return cls(
                constraint_type=ConstraintType.RANGE,
                min_version=min_ver,
                max_version=max_ver,
                original=spec,
            )

        # Exact version: 1.2.3
        try:
            parse_version(spec)  # Validate it's a valid version
            return cls(
                constraint_type=ConstraintType.EXACT,
                min_version=spec,
                max_version=None,
                original=spec,
            )
        except ValueError:
            raise ValueError(f"Invalid version constraint: {spec}")

    def satisfies(self, version: str) -> bool:
        """Check if a version satisfies this constraint.

        Args:
            version: Version string to check.

        Returns:
            True if the version satisfies the constraint.

        Examples:
            >>> c = VersionConstraint.parse("^1.2.3")
            >>> c.satisfies("1.2.3")  # True
            >>> c.satisfies("1.9.9")  # True
            >>> c.satisfies("2.0.0")  # False
            >>> c.satisfies("1.2.2")  # False
        """
        if self.constraint_type == ConstraintType.LATEST:
            return True  # Resolver will pick latest

        try:
            parse_version(version)
        except ValueError:
            return False

        if self.constraint_type == ConstraintType.EXACT:
            return version == self.min_version

        # For range, caret, tilde: check min <= version < max
        if self.min_version is not None:
            if not version_gte(version, self.min_version):
                return False

        if self.max_version is not None:
            # Check for <= in original constraint
            if "<=" in self.original:
                if not version_gte(self.max_version, version):
                    return False
            else:
                if not version_lt(version, self.max_version):
                    return False

        return True

    def __str__(self) -> str:
        """Return the original constraint string."""
        return self.original or f"{self.min_version}"


# =============================================================================
# Resolved Dependencies
# =============================================================================


@dataclass
class ResolvedDependency:
    """A resolved dependency with version and download URL.

    Attributes:
        slug: Asset slug (e.g., "@publisher/name").
        version: Resolved version string.
        download_url: URL to download the asset.
        integrity: SHA256 hash of the asset content.
        dependencies: Transitive dependencies.
    """

    slug: str
    version: str
    download_url: str
    integrity: str = ""
    dependencies: list["ResolvedDependency"] = field(default_factory=list)


# =============================================================================
# Lockfile
# =============================================================================


@dataclass
class LockfileEntry:
    """Entry in the lockfile.

    Attributes:
        slug: Asset slug.
        version: Locked version.
        resolved_url: URL the asset was downloaded from.
        integrity: SHA256 hash for verification.
        dependencies: List of dependency slugs.
    """

    slug: str
    version: str
    resolved_url: str
    integrity: str
    dependencies: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for YAML serialization."""
        return {
            "version": self.version,
            "resolved": self.resolved_url,
            "integrity": self.integrity,
            "dependencies": self.dependencies if self.dependencies else None,
        }

    @classmethod
    def from_dict(cls, slug: str, data: dict[str, Any]) -> "LockfileEntry":
        """Create from dictionary."""
        return cls(
            slug=slug,
            version=data.get("version", ""),
            resolved_url=data.get("resolved", ""),
            integrity=data.get("integrity", ""),
            dependencies=data.get("dependencies") or [],
        )


@dataclass
class Lockfile:
    """Lockfile for reproducible dependency resolution.

    Format (repotoire.lock):
        lockfileVersion: 1
        packages:
          "@repotoire/security-scanner":
            version: "1.3.0"
            resolved: "https://r2.repotoire.com/assets/..."
            integrity: "sha256-abc123..."
            dependencies:
              - "@repotoire/base-prompts"
    """

    version: int = 1
    entries: dict[str, LockfileEntry] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path | None = None) -> "Lockfile | None":
        """Load lockfile from disk.

        Args:
            path: Path to lockfile. Defaults to current directory.

        Returns:
            Loaded lockfile or None if not found.
        """
        if path is None:
            path = Path.cwd() / LOCKFILE_NAME
        elif path.is_dir():
            path = path / LOCKFILE_NAME

        if not path.exists():
            return None

        try:
            with open(path, "r") as f:
                data = yaml.safe_load(f)

            if data is None:
                return None

            entries = {}
            for slug, entry_data in data.get("packages", {}).items():
                entries[slug] = LockfileEntry.from_dict(slug, entry_data)

            return cls(
                version=data.get("lockfileVersion", 1),
                entries=entries,
            )

        except Exception as e:
            logger.warning(f"Failed to load lockfile: {e}")
            return None

    def save(self, path: Path | None = None) -> None:
        """Save lockfile to disk.

        Args:
            path: Path to save lockfile. Defaults to current directory.
        """
        if path is None:
            path = Path.cwd() / LOCKFILE_NAME
        elif path.is_dir():
            path = path / LOCKFILE_NAME

        data: dict[str, Any] = {
            "lockfileVersion": self.version,
            "packages": {},
        }

        for slug, entry in sorted(self.entries.items()):
            entry_dict = entry.to_dict()
            # Remove None values
            data["packages"][slug] = {k: v for k, v in entry_dict.items() if v is not None}

        with open(path, "w") as f:
            yaml.dump(data, f, default_flow_style=False, sort_keys=False)

    def get(self, slug: str) -> LockfileEntry | None:
        """Get a locked entry by slug."""
        return self.entries.get(slug)

    def is_satisfied(self, slug: str, constraint: str) -> bool:
        """Check if a locked version satisfies a constraint.

        Args:
            slug: Asset slug.
            constraint: Version constraint string.

        Returns:
            True if the locked version satisfies the constraint.
        """
        entry = self.entries.get(slug)
        if entry is None:
            return False

        try:
            vc = VersionConstraint.parse(constraint)
            return vc.satisfies(entry.version)
        except ValueError:
            return False

    def add(self, dep: ResolvedDependency) -> None:
        """Add a resolved dependency to the lockfile."""
        self.entries[dep.slug] = LockfileEntry(
            slug=dep.slug,
            version=dep.version,
            resolved_url=dep.download_url,
            integrity=dep.integrity,
            dependencies=[d.slug for d in dep.dependencies],
        )


# =============================================================================
# Dependency Resolver
# =============================================================================


class DependencyResolver:
    """Resolves marketplace dependencies with version constraints.

    This resolver:
    1. Parses version constraints (^, ~, ranges, exact, latest)
    2. Fetches available versions from the API
    3. Finds the highest version satisfying each constraint
    4. Recursively resolves sub-dependencies
    5. Detects circular dependencies
    6. Respects lockfile for reproducible builds

    Usage:
        resolver = DependencyResolver(api_client, cache, lockfile)
        resolved = await resolver.resolve({"@pub/asset": "^1.0.0"})
    """

    def __init__(
        self,
        api_client: MarketplaceAPIClient,
        cache: Any = None,  # LocalCache placeholder
        lockfile: Lockfile | None = None,
    ):
        """Initialize the resolver.

        Args:
            api_client: Marketplace API client for fetching versions.
            cache: Local cache for caching version lookups.
            lockfile: Lockfile for reproducible resolution.
        """
        self.api = api_client
        self.cache = cache
        self.lockfile = lockfile
        self._resolution_stack: set[str] = set()
        self._resolved_cache: dict[str, ResolvedDependency] = {}

    async def resolve(
        self,
        dependencies: dict[str, str],  # {"@pub/name": "^1.0.0"}
        include_dev: bool = False,
    ) -> list[ResolvedDependency]:
        """Resolve all dependencies.

        Args:
            dependencies: Map of slug to version constraint.
            include_dev: Whether to include dev dependencies.

        Returns:
            Flat list of resolved dependencies.

        Raises:
            DependencyCycleError: If circular dependency detected.
            NoMatchingVersionError: If no version satisfies constraint.
        """
        self._resolution_stack.clear()
        self._resolved_cache.clear()

        resolved = []
        for slug, constraint_spec in dependencies.items():
            dep = await self._resolve_single(slug, constraint_spec)
            resolved.append(dep)

        # Flatten and dedupe
        return self._flatten_and_dedupe(resolved)

    async def _resolve_single(
        self,
        slug: str,
        constraint_spec: str,
    ) -> ResolvedDependency:
        """Resolve a single dependency.

        Args:
            slug: Asset slug (e.g., "@publisher/name").
            constraint_spec: Version constraint string.

        Returns:
            Resolved dependency with transitive deps.
        """
        # Check for cycles
        if slug in self._resolution_stack:
            cycle = list(self._resolution_stack) + [slug]
            raise DependencyCycleError(cycle)

        # Check cache
        cache_key = f"{slug}@{constraint_spec}"
        if cache_key in self._resolved_cache:
            return self._resolved_cache[cache_key]

        # Add to resolution stack
        self._resolution_stack.add(slug)

        try:
            # Check lockfile first
            if self.lockfile:
                locked = self.lockfile.get(slug)
                if locked and self.lockfile.is_satisfied(slug, constraint_spec):
                    logger.debug(f"Using locked version {locked.version} for {slug}")
                    return ResolvedDependency(
                        slug=slug,
                        version=locked.version,
                        download_url=locked.resolved_url,
                        integrity=locked.integrity,
                        dependencies=[],  # We'll resolve these from the lockfile
                    )

            # Parse constraint
            constraint = VersionConstraint.parse(constraint_spec)

            # Fetch available versions from API
            versions = await self._fetch_versions(slug)

            if not versions:
                raise NoMatchingVersionError(slug, constraint_spec, [])

            # Find highest matching version
            matching = [v for v in versions if constraint.satisfies(v["version"])]

            if not matching:
                available = [v["version"] for v in versions]
                raise NoMatchingVersionError(slug, constraint_spec, available)

            # Sort by version (descending) and pick highest
            matching.sort(key=lambda v: v["version"], reverse=True)
            best = matching[0]

            # Recursively resolve sub-dependencies
            sub_deps: list[ResolvedDependency] = []
            for sub_slug, sub_constraint in best.get("dependencies", {}).items():
                sub_dep = await self._resolve_single(sub_slug, sub_constraint)
                sub_deps.append(sub_dep)

            # Build resolved dependency
            resolved = ResolvedDependency(
                slug=slug,
                version=best["version"],
                download_url=best.get("download_url", ""),
                integrity=best.get("integrity", best.get("checksum", "")),
                dependencies=sub_deps,
            )

            # Cache it
            self._resolved_cache[cache_key] = resolved
            return resolved

        finally:
            self._resolution_stack.discard(slug)

    async def _fetch_versions(self, slug: str) -> list[dict[str, Any]]:
        """Fetch available versions for an asset.

        Args:
            slug: Asset slug.

        Returns:
            List of version dictionaries with version, dependencies, download_url, etc.
        """
        # Parse slug: @publisher/name
        if slug.startswith("@"):
            slug = slug[1:]

        parts = slug.split("/", 1)
        if len(parts) != 2:
            return []

        publisher, name = parts

        try:
            versions = self.api.get_asset_versions(publisher, name, limit=50)
            return [
                {
                    "version": v.version,
                    "download_url": "",  # Will be set during install
                    "checksum": v.checksum,
                    "dependencies": {},  # API should return this
                }
                for v in versions
            ]
        except Exception as e:
            logger.warning(f"Failed to fetch versions for {slug}: {e}")
            return []

    def _flatten_and_dedupe(
        self,
        deps: list[ResolvedDependency],
    ) -> list[ResolvedDependency]:
        """Flatten dependency tree and deduplicate.

        Higher versions win on conflict.

        Args:
            deps: List of resolved dependencies (possibly nested).

        Returns:
            Flat list with duplicates resolved.
        """
        seen: dict[str, ResolvedDependency] = {}

        def _flatten(dep: ResolvedDependency) -> None:
            existing = seen.get(dep.slug)

            if existing is None:
                seen[dep.slug] = dep
            else:
                # Higher version wins
                if compare_versions(dep.version, existing.version) > 0:
                    seen[dep.slug] = dep

            for sub_dep in dep.dependencies:
                _flatten(sub_dep)

        for dep in deps:
            _flatten(dep)

        return list(seen.values())


# =============================================================================
# Update Checking
# =============================================================================


class UpdateType(Enum):
    """Type of version update."""

    MAJOR = "major"  # Breaking changes, requires manual approval
    MINOR = "minor"  # New features, auto-apply
    PATCH = "patch"  # Bug fixes, auto-apply


@dataclass
class UpdateAvailable:
    """Information about an available update.

    Attributes:
        slug: Asset slug.
        current: Currently installed version.
        latest: Latest available version.
        update_type: Type of update (major/minor/patch).
        changelog: Changelog for the update.
    """

    slug: str
    current: str
    latest: str
    update_type: UpdateType
    changelog: str | None = None


@dataclass
class UpdateResult:
    """Result of applying an update.

    Attributes:
        slug: Asset slug.
        old_version: Previous version.
        new_version: New version.
        success: Whether update succeeded.
        error: Error message if failed.
    """

    slug: str
    old_version: str
    new_version: str
    success: bool
    error: str | None = None


class AssetUpdater:
    """Checks for and applies asset updates.

    Update Policy:
    - Major updates (1.x -> 2.x): Require manual approval unless auto_approve=True
    - Minor updates (1.1 -> 1.2): Auto-apply
    - Patch updates (1.1.1 -> 1.1.2): Auto-apply
    """

    def __init__(
        self,
        api_client: MarketplaceAPIClient,
        lockfile: Lockfile | None = None,
    ):
        """Initialize the updater.

        Args:
            api_client: Marketplace API client.
            lockfile: Current lockfile (will be updated).
        """
        self.api = api_client
        self.lockfile = lockfile

    async def check_updates(
        self,
        installed: dict[str, str],  # slug -> version
    ) -> list[UpdateAvailable]:
        """Check for available updates.

        Args:
            installed: Map of installed assets to versions.

        Returns:
            List of available updates.
        """
        updates = []

        for slug, current_version in installed.items():
            try:
                # Parse slug
                if slug.startswith("@"):
                    slug_clean = slug[1:]
                else:
                    slug_clean = slug

                parts = slug_clean.split("/", 1)
                if len(parts) != 2:
                    continue

                publisher, name = parts

                # Get asset info
                asset = self.api.get_asset(publisher, name)

                if not asset.latest_version:
                    continue

                # Compare versions
                if compare_versions(asset.latest_version, current_version) > 0:
                    update_type = self._classify_update(current_version, asset.latest_version)

                    # Get changelog
                    changelog = None
                    try:
                        versions = self.api.get_asset_versions(publisher, name, limit=5)
                        for v in versions:
                            if v.version == asset.latest_version:
                                changelog = v.changelog
                                break
                    except Exception:
                        pass

                    updates.append(
                        UpdateAvailable(
                            slug=slug,
                            current=current_version,
                            latest=asset.latest_version,
                            update_type=update_type,
                            changelog=changelog,
                        )
                    )

            except Exception as e:
                logger.warning(f"Failed to check updates for {slug}: {e}")

        return updates

    async def apply_updates(
        self,
        updates: list[UpdateAvailable],
        auto_approve: bool = False,
    ) -> list[UpdateResult]:
        """Apply available updates.

        Args:
            updates: List of updates to apply.
            auto_approve: If True, apply major updates without confirmation.

        Returns:
            List of update results.
        """
        results = []

        for update in updates:
            # Major updates require approval
            if update.update_type == UpdateType.MAJOR and not auto_approve:
                results.append(
                    UpdateResult(
                        slug=update.slug,
                        old_version=update.current,
                        new_version=update.latest,
                        success=False,
                        error="Major update requires manual approval (use --auto-approve)",
                    )
                )
                continue

            try:
                # Parse slug
                if update.slug.startswith("@"):
                    slug_clean = update.slug[1:]
                else:
                    slug_clean = update.slug

                parts = slug_clean.split("/", 1)
                if len(parts) != 2:
                    raise ValueError(f"Invalid slug: {update.slug}")

                publisher, name = parts

                # Install new version
                install_result = self.api.install(publisher, name, update.latest)

                # Update lockfile
                if self.lockfile:
                    self.lockfile.entries[update.slug] = LockfileEntry(
                        slug=update.slug,
                        version=update.latest,
                        resolved_url=install_result.download_url,
                        integrity=install_result.checksum,
                        dependencies=[],
                    )

                results.append(
                    UpdateResult(
                        slug=update.slug,
                        old_version=update.current,
                        new_version=update.latest,
                        success=True,
                    )
                )

            except Exception as e:
                results.append(
                    UpdateResult(
                        slug=update.slug,
                        old_version=update.current,
                        new_version=update.latest,
                        success=False,
                        error=str(e),
                    )
                )

        return results

    def _classify_update(self, current: str, latest: str) -> UpdateType:
        """Classify the type of update.

        Args:
            current: Current version.
            latest: Latest version.

        Returns:
            Update type (major/minor/patch).
        """
        try:
            curr_major, curr_minor, curr_patch, _ = parse_version(current)
            new_major, new_minor, new_patch, _ = parse_version(latest)

            if new_major > curr_major:
                return UpdateType.MAJOR
            elif new_minor > curr_minor:
                return UpdateType.MINOR
            else:
                return UpdateType.PATCH

        except ValueError:
            # Fallback to major if we can't parse
            return UpdateType.MAJOR


# =============================================================================
# Utility Functions
# =============================================================================


def compute_integrity(content: bytes) -> str:
    """Compute SHA256 integrity hash.

    Args:
        content: Raw content bytes.

    Returns:
        Integrity string (e.g., "sha256-abc123...").
    """
    digest = hashlib.sha256(content).hexdigest()
    return f"sha256-{digest}"
