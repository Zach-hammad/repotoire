"""Data models for monorepo analysis.

Models for representing packages, package metadata, and package-level health metrics.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set
from datetime import datetime

from repotoire.models import CodebaseHealth, MetricsBreakdown, FindingsSummary


@dataclass
class PackageMetadata:
    """Metadata extracted from package configuration files.

    Contains information from package.json, pyproject.toml, BUILD files, etc.

    Attributes:
        name: Package name
        version: Package version
        description: Package description
        package_type: Type (npm, poetry, bazel, nx, turborepo)
        config_file: Path to configuration file
        dependencies: List of package dependencies
        dev_dependencies: List of development dependencies
        scripts: Build/test scripts available
        entry_points: Entry points (main files)
        language: Primary language (python, typescript, etc.)
        framework: Framework if detected (react, fastapi, etc.)

    Example:
        >>> metadata = PackageMetadata(
        ...     name="@myapp/auth",
        ...     version="1.2.3",
        ...     description="Authentication package",
        ...     package_type="npm",
        ...     config_file="packages/auth/package.json",
        ...     dependencies=["express", "jsonwebtoken"],
        ...     scripts={"test": "jest", "build": "tsc"}
        ... )
    """
    name: str
    version: Optional[str] = None
    description: Optional[str] = None
    package_type: str = "unknown"  # npm, poetry, bazel, nx, turborepo
    config_file: str = ""
    dependencies: List[str] = field(default_factory=list)
    dev_dependencies: List[str] = field(default_factory=list)
    scripts: Dict[str, str] = field(default_factory=dict)
    entry_points: List[str] = field(default_factory=list)
    language: Optional[str] = None
    framework: Optional[str] = None

    def to_dict(self) -> Dict:
        """Convert to dictionary for serialization."""
        return {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "package_type": self.package_type,
            "config_file": self.config_file,
            "dependencies": self.dependencies,
            "dev_dependencies": self.dev_dependencies,
            "scripts": self.scripts,
            "entry_points": self.entry_points,
            "language": self.language,
            "framework": self.framework,
        }


@dataclass
class Package:
    """Represents a package in a monorepo.

    A package is a cohesive unit with its own dependencies, tests, and build.
    Can be detected from package.json, pyproject.toml, BUILD files, etc.

    Attributes:
        path: Relative path to package directory
        metadata: Package metadata from config files
        files: List of file paths in this package
        imports_packages: Packages this package imports from
        imported_by_packages: Packages that import this package
        has_tests: Whether package has tests
        test_count: Number of test files
        loc: Total lines of code

    Example:
        >>> package = Package(
        ...     path="packages/auth",
        ...     metadata=PackageMetadata(name="@myapp/auth", ...),
        ...     files=["packages/auth/src/index.ts", ...],
        ...     imports_packages=["packages/shared"],
        ...     has_tests=True,
        ...     test_count=15,
        ...     loc=2500
        ... )
    """
    path: str
    metadata: PackageMetadata
    files: List[str] = field(default_factory=list)
    imports_packages: Set[str] = field(default_factory=set)  # Package paths
    imported_by_packages: Set[str] = field(default_factory=set)  # Package paths
    has_tests: bool = False
    test_count: int = 0
    loc: int = 0

    @property
    def name(self) -> str:
        """Get package name."""
        return self.metadata.name

    @property
    def relative_path(self) -> Path:
        """Get package path as Path object."""
        return Path(self.path)

    def to_dict(self) -> Dict:
        """Convert to dictionary for serialization."""
        return {
            "path": self.path,
            "metadata": self.metadata.to_dict(),
            "files": self.files,
            "imports_packages": list(self.imports_packages),
            "imported_by_packages": list(self.imported_by_packages),
            "has_tests": self.has_tests,
            "test_count": self.test_count,
            "loc": self.loc,
        }


@dataclass
class PackageHealth:
    """Health score and metrics for a single package.

    Similar to CodebaseHealth but scoped to a single package in a monorepo.

    Attributes:
        package_path: Path to the package
        package_name: Package name
        health: Overall codebase health for this package
        coupling_score: Coupling with other packages (0-100, higher is better)
        independence_score: How independent the package is (0-100)
        test_coverage: Test coverage percentage (0-100)
        build_time_estimate: Estimated build time in seconds
        affected_by_changes: Packages that would be affected by changes here

    Example:
        >>> package_health = PackageHealth(
        ...     package_path="packages/auth",
        ...     package_name="@myapp/auth",
        ...     health=CodebaseHealth(grade="A", overall_score=92, ...),
        ...     coupling_score=85.0,
        ...     independence_score=90.0,
        ...     test_coverage=88.0,
        ...     build_time_estimate=45.0,
        ...     affected_by_changes=["packages/api", "packages/frontend"]
        ... )
    """
    package_path: str
    package_name: str
    health: CodebaseHealth
    coupling_score: float = 0.0  # 0-100, higher is better (less coupled)
    independence_score: float = 0.0  # 0-100, higher is better
    test_coverage: float = 0.0  # 0-100
    build_time_estimate: float = 0.0  # Seconds
    affected_by_changes: List[str] = field(default_factory=list)  # Package paths

    @property
    def overall_score(self) -> float:
        """Get overall health score."""
        return self.health.overall_score

    @property
    def grade(self) -> str:
        """Get health grade."""
        return self.health.grade

    def to_dict(self) -> Dict:
        """Convert to dictionary for serialization."""
        return {
            "package_path": self.package_path,
            "package_name": self.package_name,
            "overall_score": self.overall_score,
            "grade": self.grade,
            "coupling_score": self.coupling_score,
            "independence_score": self.independence_score,
            "test_coverage": self.test_coverage,
            "build_time_estimate": self.build_time_estimate,
            "affected_by_changes": self.affected_by_changes,
            "health": self.health.to_dict(),
        }


@dataclass
class MonorepoHealth:
    """Overall health metrics for entire monorepo.

    Aggregates package-level health and adds monorepo-specific metrics.

    Attributes:
        repository_path: Path to monorepo root
        overall_health: Aggregated codebase health
        package_count: Total number of packages
        package_health_scores: Health scores for each package
        cross_package_issues: Issues spanning multiple packages
        circular_package_dependencies: Circular dependencies between packages
        duplicate_code_across_packages: Code duplication percentage
        build_impact_graph: Graph of build dependencies

    Example:
        >>> monorepo_health = MonorepoHealth(
        ...     repository_path="/path/to/monorepo",
        ...     overall_health=CodebaseHealth(...),
        ...     package_count=25,
        ...     package_health_scores=[PackageHealth(...), ...],
        ...     cross_package_issues=5,
        ...     circular_package_dependencies=1,
        ...     duplicate_code_across_packages=3.2
        ... )
    """
    repository_path: str
    overall_health: CodebaseHealth
    package_count: int
    package_health_scores: List[PackageHealth] = field(default_factory=list)
    cross_package_issues: int = 0
    circular_package_dependencies: int = 0
    duplicate_code_across_packages: float = 0.0  # Percentage
    build_impact_graph: Dict = field(default_factory=dict)

    @property
    def overall_score(self) -> float:
        """Get overall health score."""
        return self.overall_health.overall_score

    @property
    def grade(self) -> str:
        """Get overall health grade."""
        return self.overall_health.grade

    @property
    def avg_package_score(self) -> float:
        """Calculate average package health score."""
        if not self.package_health_scores:
            return 0.0
        return sum(p.overall_score for p in self.package_health_scores) / len(self.package_health_scores)

    def get_package_health(self, package_path: str) -> Optional[PackageHealth]:
        """Get health score for specific package.

        Args:
            package_path: Path to package directory

        Returns:
            PackageHealth if found, None otherwise
        """
        for ph in self.package_health_scores:
            if ph.package_path == package_path:
                return ph
        return None

    def to_dict(self) -> Dict:
        """Convert to dictionary for serialization."""
        return {
            "repository_path": self.repository_path,
            "overall_score": self.overall_score,
            "grade": self.grade,
            "package_count": self.package_count,
            "avg_package_score": self.avg_package_score,
            "cross_package_issues": self.cross_package_issues,
            "circular_package_dependencies": self.circular_package_dependencies,
            "duplicate_code_across_packages": self.duplicate_code_across_packages,
            "package_health_scores": [p.to_dict() for p in self.package_health_scores],
            "overall_health": self.overall_health.to_dict(),
        }
