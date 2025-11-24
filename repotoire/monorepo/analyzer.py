"""Package-level analysis for monorepos.

Analyzes individual packages and calculates per-package health scores.
"""

from pathlib import Path
from typing import Dict, List, Optional

from repotoire.graph import Neo4jClient
from repotoire.detectors import AnalysisEngine
from repotoire.models import CodebaseHealth, Finding
from repotoire.monorepo.models import Package, PackageHealth, MonorepoHealth
from repotoire.logging_config import get_logger

logger = get_logger(__name__)


class PackageAnalyzer:
    """Analyzes individual packages in a monorepo.

    Uses AnalysisEngine but scopes it to package-level files and
    calculates additional package-specific metrics like coupling
    and independence scores.

    Example:
        >>> analyzer = PackageAnalyzer(neo4j_client, "/path/to/monorepo")
        >>> package_health = analyzer.analyze_package(package)
        >>> print(f"Package {package.name}: {package_health.grade}")
        Package @myapp/auth: A (92/100)
    """

    def __init__(self, neo4j_client: Neo4jClient, repository_path: str):
        """Initialize package analyzer.

        Args:
            neo4j_client: Neo4j database client
            repository_path: Path to monorepo root
        """
        self.db = neo4j_client
        self.repository_path = Path(repository_path)

    def analyze_package(
        self, package: Package, detector_config: Optional[Dict] = None
    ) -> PackageHealth:
        """Analyze a single package and calculate health score.

        Args:
            package: Package to analyze
            detector_config: Optional detector configuration

        Returns:
            PackageHealth with score and metrics
        """
        logger.info(f"Analyzing package: {package.name} at {package.path}")

        # Run full analysis on this package's files
        engine = AnalysisEngine(
            self.db,
            detector_config=detector_config,
            repository_path=str(self.repository_path / package.path),
        )

        # Get codebase health for this package
        health = engine.analyze()

        # Filter findings to only those in this package
        package_findings = self._filter_findings_for_package(health.findings, package)
        health.findings = package_findings

        # Calculate package-specific metrics
        coupling_score = self._calculate_coupling_score(package)
        independence_score = self._calculate_independence_score(package)
        test_coverage = self._estimate_test_coverage(package)
        build_time = self._estimate_build_time(package)

        # Find affected packages
        affected_packages = list(package.imported_by_packages)

        package_health = PackageHealth(
            package_path=package.path,
            package_name=package.name,
            health=health,
            coupling_score=coupling_score,
            independence_score=independence_score,
            test_coverage=test_coverage,
            build_time_estimate=build_time,
            affected_by_changes=affected_packages,
        )

        logger.info(
            f"Package analysis complete: {package.name}",
            extra={
                "grade": package_health.grade,
                "overall_score": package_health.overall_score,
                "coupling_score": coupling_score,
                "independence_score": independence_score,
            },
        )

        return package_health

    def analyze_monorepo(
        self, packages: List[Package], detector_config: Optional[Dict] = None
    ) -> MonorepoHealth:
        """Analyze all packages in a monorepo.

        Args:
            packages: List of packages to analyze
            detector_config: Optional detector configuration

        Returns:
            MonorepoHealth with overall and per-package metrics
        """
        logger.info(f"Analyzing monorepo with {len(packages)} packages")

        # Analyze each package
        package_health_scores = []
        for package in packages:
            try:
                package_health = self.analyze_package(package, detector_config)
                package_health_scores.append(package_health)
            except Exception as e:
                logger.error(f"Failed to analyze package {package.name}: {e}")
                continue

        # Run overall analysis for entire monorepo
        engine = AnalysisEngine(
            self.db,
            detector_config=detector_config,
            repository_path=str(self.repository_path),
        )
        overall_health = engine.analyze()

        # Calculate cross-package metrics
        cross_package_issues = self._count_cross_package_issues(packages, overall_health.findings)
        circular_deps = self._count_circular_package_dependencies(packages)
        duplicate_code = self._calculate_cross_package_duplication(packages, overall_health.findings)

        monorepo_health = MonorepoHealth(
            repository_path=str(self.repository_path),
            overall_health=overall_health,
            package_count=len(packages),
            package_health_scores=package_health_scores,
            cross_package_issues=cross_package_issues,
            circular_package_dependencies=circular_deps,
            duplicate_code_across_packages=duplicate_code,
        )

        logger.info(
            "Monorepo analysis complete",
            extra={
                "overall_grade": monorepo_health.grade,
                "overall_score": monorepo_health.overall_score,
                "avg_package_score": monorepo_health.avg_package_score,
                "cross_package_issues": cross_package_issues,
            },
        )

        return monorepo_health

    def _filter_findings_for_package(
        self, findings: List[Finding], package: Package
    ) -> List[Finding]:
        """Filter findings to only those relevant to this package.

        Args:
            findings: All findings from analysis
            package: Package to filter for

        Returns:
            List of findings affecting this package
        """
        package_files = set(package.files)
        package_findings = []

        for finding in findings:
            # Check if any affected files are in this package
            if any(f in package_files for f in finding.affected_files):
                package_findings.append(finding)

        return package_findings

    def _calculate_coupling_score(self, package: Package) -> float:
        """Calculate coupling score for package.

        Lower coupling is better. Score 0-100 (higher is better).

        Args:
            package: Package to analyze

        Returns:
            Coupling score (0-100, higher means less coupled)
        """
        if not package.files:
            return 100.0

        # Count outgoing dependencies (imports)
        import_count = len(package.imports_packages)

        # Normalize to 0-100 scale (assuming 0-10 imports is reasonable)
        # More than 10 imports = heavily coupled
        max_reasonable_imports = 10
        if import_count == 0:
            return 100.0
        elif import_count >= max_reasonable_imports:
            return 0.0
        else:
            return (1.0 - (import_count / max_reasonable_imports)) * 100.0

    def _calculate_independence_score(self, package: Package) -> float:
        """Calculate independence score for package.

        Measures how standalone the package is (few dependencies).
        Score 0-100 (higher is better).

        Args:
            package: Package to analyze

        Returns:
            Independence score (0-100)
        """
        if not package.files:
            return 100.0

        # Consider both imports and dependents
        import_count = len(package.imports_packages)
        dependent_count = len(package.imported_by_packages)

        total_connections = import_count + dependent_count

        # Normalize to 0-100 scale (assuming 0-15 total connections is reasonable)
        max_reasonable_connections = 15
        if total_connections == 0:
            return 100.0
        elif total_connections >= max_reasonable_connections:
            return 0.0
        else:
            return (1.0 - (total_connections / max_reasonable_connections)) * 100.0

    def _estimate_test_coverage(self, package: Package) -> float:
        """Estimate test coverage for package.

        Uses test file count as heuristic (not actual coverage).

        Args:
            package: Package to analyze

        Returns:
            Estimated test coverage percentage (0-100)
        """
        if not package.files:
            return 0.0

        # Simple heuristic: 1 test file per 5 source files = 100% coverage
        source_files = len([f for f in package.files if not any(test in f.lower() for test in ["test", "spec"])])
        test_files = package.test_count

        if source_files == 0:
            return 100.0

        ideal_ratio = 0.2  # 1 test file per 5 source files
        actual_ratio = test_files / source_files if source_files > 0 else 0

        # Cap at 100%
        return min(100.0, (actual_ratio / ideal_ratio) * 100.0)

    def _estimate_build_time(self, package: Package) -> float:
        """Estimate build time for package in seconds.

        Simple heuristic based on file count and language.

        Args:
            package: Package to analyze

        Returns:
            Estimated build time in seconds
        """
        if not package.files:
            return 0.0

        # Base time per file (seconds)
        time_per_file = {
            "typescript": 0.1,  # Fast with incremental compilation
            "javascript": 0.05,  # Very fast
            "python": 0.2,  # Slower (mypy, tests)
            "rust": 0.5,  # Slow (compilation)
            "go": 0.15,  # Fast compilation
        }

        language = package.metadata.language or "python"
        base_time = time_per_file.get(language, 0.2)

        # Estimate: base_time * file_count + overhead
        build_time = (len(package.files) * base_time) + 5.0  # 5s overhead

        return round(build_time, 1)

    def _count_cross_package_issues(
        self, packages: List[Package], findings: List[Finding]
    ) -> int:
        """Count issues that span multiple packages.

        Args:
            packages: List of all packages
            findings: All findings from analysis

        Returns:
            Count of cross-package issues
        """
        # Build package file mapping
        file_to_package: Dict[str, str] = {}
        for package in packages:
            for file_path in package.files:
                file_to_package[file_path] = package.path

        cross_package_count = 0

        for finding in findings:
            # Get unique packages affected by this finding
            affected_packages = set()
            for file_path in finding.affected_files:
                if file_path in file_to_package:
                    affected_packages.add(file_to_package[file_path])

            # If finding affects multiple packages, count it
            if len(affected_packages) > 1:
                cross_package_count += 1

        return cross_package_count

    def _count_circular_package_dependencies(self, packages: List[Package]) -> int:
        """Count circular dependencies between packages.

        Uses simple DFS to detect cycles in package dependency graph.

        Args:
            packages: List of all packages

        Returns:
            Number of circular package dependencies
        """
        # Build adjacency list
        graph: Dict[str, List[str]] = {}
        for package in packages:
            graph[package.path] = list(package.imports_packages)

        # DFS to find cycles
        visited = set()
        rec_stack = set()
        cycles = 0

        def has_cycle(node: str) -> bool:
            nonlocal cycles

            visited.add(node)
            rec_stack.add(node)

            # Check neighbors
            for neighbor in graph.get(node, []):
                if neighbor not in visited:
                    if has_cycle(neighbor):
                        return True
                elif neighbor in rec_stack:
                    cycles += 1
                    return True

            rec_stack.remove(node)
            return False

        # Check each node
        for package in packages:
            if package.path not in visited:
                has_cycle(package.path)

        return cycles

    def _calculate_cross_package_duplication(
        self, packages: List[Package], findings: List[Finding]
    ) -> float:
        """Calculate code duplication percentage across packages.

        Looks for duplicate code findings that span multiple packages.

        Args:
            packages: List of all packages
            findings: All findings from analysis

        Returns:
            Duplication percentage (0-100)
        """
        # Build package file mapping
        file_to_package: Dict[str, str] = {}
        for package in packages:
            for file_path in package.files:
                file_to_package[file_path] = package.path

        # Find duplicate code findings
        duplicate_findings = [
            f
            for f in findings
            if "duplicate" in f.detector.lower() or "duplication" in f.title.lower()
        ]

        if not duplicate_findings:
            return 0.0

        # Count cross-package duplicates
        cross_package_duplicates = 0
        for finding in duplicate_findings:
            affected_packages = set()
            for file_path in finding.affected_files:
                if file_path in file_to_package:
                    affected_packages.add(file_to_package[file_path])

            if len(affected_packages) > 1:
                cross_package_duplicates += 1

        # Calculate percentage
        if duplicate_findings:
            return (cross_package_duplicates / len(duplicate_findings)) * 100.0
        return 0.0
