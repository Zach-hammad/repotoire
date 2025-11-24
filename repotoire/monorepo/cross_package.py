"""Cross-package analysis for monorepos.

Detects issues that span multiple packages:
- Circular dependencies between packages
- Code duplication across packages
- Inconsistent patterns
- Package coupling metrics
"""

from typing import List, Dict, Set, Tuple, Optional
from collections import defaultdict

from repotoire.monorepo.models import Package
from repotoire.models import Finding, Severity
from repotoire.logging_config import get_logger

logger = get_logger(__name__)


class CrossPackageAnalyzer:
    """Analyzes issues across package boundaries.

    Detects problems that span multiple packages in a monorepo,
    which are often indicators of poor architecture or coupling.

    Example:
        >>> analyzer = CrossPackageAnalyzer(packages)
        >>> issues = analyzer.detect_cross_package_issues()
        >>> print(f"Found {len(issues)} cross-package issues")
        Found 5 cross-package issues
    """

    def __init__(self, packages: List[Package]):
        """Initialize cross-package analyzer.

        Args:
            packages: List of all packages in the monorepo
        """
        self.packages = packages

        # Build package lookup
        self.package_by_path: Dict[str, Package] = {pkg.path: pkg for pkg in packages}

    def detect_cross_package_issues(self) -> List[Finding]:
        """Detect all cross-package issues.

        Returns:
            List of findings for cross-package problems
        """
        logger.info("Detecting cross-package issues")

        findings: List[Finding] = []

        # Detect circular package dependencies
        circular_findings = self._detect_circular_dependencies()
        findings.extend(circular_findings)

        # Detect excessive package coupling
        coupling_findings = self._detect_excessive_coupling()
        findings.extend(coupling_findings)

        # Detect package boundary violations
        boundary_findings = self._detect_boundary_violations()
        findings.extend(boundary_findings)

        # Detect shared dependencies inconsistencies
        dependency_findings = self._detect_inconsistent_dependencies()
        findings.extend(dependency_findings)

        logger.info(f"Found {len(findings)} cross-package issues")

        return findings

    def _detect_circular_dependencies(self) -> List[Finding]:
        """Detect circular dependencies between packages.

        Returns:
            List of findings for circular package dependencies
        """
        findings: List[Finding] = []

        # Use DFS to find cycles
        visited: Set[str] = set()
        rec_stack: Set[str] = set()
        cycles: List[List[str]] = []

        def dfs(pkg_path: str, path: List[str]):
            """DFS to detect cycles."""
            visited.add(pkg_path)
            rec_stack.add(pkg_path)
            path.append(pkg_path)

            package = self.package_by_path.get(pkg_path)
            if not package:
                return

            for dep_path in package.imports_packages:
                if dep_path not in visited:
                    dfs(dep_path, path.copy())
                elif dep_path in rec_stack:
                    # Found a cycle
                    cycle_start = path.index(dep_path)
                    cycle = path[cycle_start:] + [dep_path]
                    cycles.append(cycle)

            rec_stack.remove(pkg_path)

        # Run DFS from each node
        for package in self.packages:
            if package.path not in visited:
                dfs(package.path, [])

        # Create findings for each unique cycle
        seen_cycles: Set[Tuple[str, ...]] = set()

        for cycle in cycles:
            # Normalize cycle (rotate to start with lexicographically smallest)
            normalized = tuple(sorted(cycle))

            if normalized not in seen_cycles:
                seen_cycles.add(normalized)

                package_names = [self.package_by_path[p].name for p in cycle[:-1]]

                finding = Finding(
                    id=f"cross-pkg-circular-{'-'.join(cycle[:2])}",
                    detector="CrossPackageAnalyzer",
                    severity=Severity.HIGH,
                    title=f"Circular dependency between {len(cycle) - 1} packages",
                    description=f"Found circular dependency chain: {' → '.join(package_names)} → {package_names[0]}",
                    affected_nodes=[],
                    affected_files=[],
                    graph_context={
                        "cycle_length": len(cycle) - 1,
                        "package_paths": cycle[:-1],
                        "package_names": package_names,
                    },
                    suggested_fix="Refactor to remove circular dependency. Consider extracting shared interfaces or using dependency injection.",
                )

                findings.append(finding)

        return findings

    def _detect_excessive_coupling(self) -> List[Finding]:
        """Detect packages with excessive coupling to other packages.

        Returns:
            List of findings for overly coupled packages
        """
        findings: List[Finding] = []

        # Threshold for excessive coupling (arbitrary, can be configured)
        MAX_DEPENDENCIES = 8

        for package in self.packages:
            import_count = len(package.imports_packages)
            dependent_count = len(package.imported_by_packages)
            total_coupling = import_count + dependent_count

            if total_coupling > MAX_DEPENDENCIES:
                severity = Severity.HIGH if total_coupling > 12 else Severity.MEDIUM

                finding = Finding(
                    id=f"cross-pkg-coupling-{package.path}",
                    detector="CrossPackageAnalyzer",
                    severity=severity,
                    title=f"Excessive package coupling: {package.name}",
                    description=(
                        f"Package {package.name} has {total_coupling} package dependencies "
                        f"({import_count} imports, {dependent_count} dependents). "
                        f"Highly coupled packages are harder to change and test."
                    ),
                    affected_nodes=[],
                    affected_files=package.files[:10],  # Sample files
                    graph_context={
                        "package_path": package.path,
                        "package_name": package.name,
                        "import_count": import_count,
                        "dependent_count": dependent_count,
                        "total_coupling": total_coupling,
                        "imports": list(package.imports_packages),
                        "imported_by": list(package.imported_by_packages),
                    },
                    suggested_fix=(
                        "Reduce coupling by:\n"
                        "1. Extracting shared code to a separate package\n"
                        "2. Using dependency injection or interfaces\n"
                        "3. Applying the Dependency Inversion Principle"
                    ),
                )

                findings.append(finding)

        return findings

    def _detect_boundary_violations(self) -> List[Finding]:
        """Detect package boundary violations.

        Detects when low-level packages depend on high-level packages,
        violating dependency direction.

        Returns:
            List of findings for boundary violations
        """
        findings: List[Finding] = []

        # Heuristic: packages in certain directories should not depend on others
        # e.g., "shared" shouldn't depend on "features", "core" shouldn't depend on "ui"

        boundary_rules = [
            ("shared", ["features", "pages", "app"]),
            ("core", ["ui", "components", "features"]),
            ("lib", ["app", "pages", "features"]),
            ("utils", ["features", "pages", "app"]),
        ]

        for package in self.packages:
            for restricted_dir, forbidden_deps in boundary_rules:
                # Check if this package is in restricted directory
                if restricted_dir in package.path.lower():
                    # Check if it imports from forbidden directories
                    violations = []

                    for dep_path in package.imports_packages:
                        for forbidden in forbidden_deps:
                            if forbidden in dep_path.lower():
                                dep_package = self.package_by_path.get(dep_path)
                                if dep_package:
                                    violations.append(dep_package.name)

                    if violations:
                        finding = Finding(
                            id=f"cross-pkg-boundary-{package.path}",
                            detector="CrossPackageAnalyzer",
                            severity=Severity.MEDIUM,
                            title=f"Package boundary violation: {package.name}",
                            description=(
                                f"Package {package.name} (in {restricted_dir}/) depends on "
                                f"higher-level packages: {', '.join(violations)}. "
                                f"This violates architectural layering principles."
                            ),
                            affected_nodes=[],
                            affected_files=package.files[:5],
                            graph_context={
                                "package_path": package.path,
                                "package_name": package.name,
                                "restricted_layer": restricted_dir,
                                "violations": violations,
                            },
                            suggested_fix=(
                                "Refactor to follow dependency direction:\n"
                                "1. Move shared code to appropriate layer\n"
                                "2. Use dependency injection to invert dependencies\n"
                                "3. Consider extracting an interface package"
                            ),
                        )

                        findings.append(finding)

        return findings

    def _detect_inconsistent_dependencies(self) -> List[Finding]:
        """Detect inconsistent dependency versions across packages.

        Returns:
            List of findings for version inconsistencies
        """
        findings: List[Finding] = []

        # Group packages by dependency
        dep_versions: Dict[str, Dict[str, List[Package]]] = defaultdict(lambda: defaultdict(list))

        for package in self.packages:
            for dep in package.metadata.dependencies:
                # Extract dependency name (ignoring version for now)
                dep_name = dep.split("@")[0].split(">=")[0].split("==")[0].strip()

                # Track which packages use this dependency
                dep_versions[dep_name][dep].append(package)

        # Find dependencies used with multiple versions
        for dep_name, versions in dep_versions.items():
            if len(versions) > 1:
                # Multiple versions detected
                version_info = []
                affected_packages = []

                for version, packages in versions.items():
                    package_names = [p.name for p in packages]
                    version_info.append(f"{version}: {', '.join(package_names)}")
                    affected_packages.extend(packages)

                # Skip if only 1-2 packages use this dependency
                if len(affected_packages) < 3:
                    continue

                finding = Finding(
                    id=f"cross-pkg-dep-inconsistent-{dep_name}",
                    detector="CrossPackageAnalyzer",
                    severity=Severity.LOW,
                    title=f"Inconsistent dependency versions: {dep_name}",
                    description=(
                        f"Dependency '{dep_name}' is used with {len(versions)} different versions "
                        f"across {len(affected_packages)} packages. This can cause subtle bugs and "
                        f"increases bundle size.\n\n"
                        + "\n".join(version_info)
                    ),
                    affected_nodes=[],
                    affected_files=[p.metadata.config_file for p in affected_packages],
                    graph_context={
                        "dependency_name": dep_name,
                        "versions": list(versions.keys()),
                        "affected_packages": [p.name for p in affected_packages],
                    },
                    suggested_fix=(
                        f"Standardize on a single version of {dep_name}:\n"
                        "1. Choose the latest compatible version\n"
                        "2. Update all package.json/pyproject.toml files\n"
                        "3. Test affected packages thoroughly"
                    ),
                )

                findings.append(finding)

        return findings

    def calculate_package_coupling_matrix(self) -> Dict[str, Dict[str, int]]:
        """Calculate coupling matrix between all packages.

        Returns:
            Dict mapping package paths to their coupling with other packages
                Format: {pkg_a: {pkg_b: coupling_score, ...}, ...}

        Example:
            >>> matrix = analyzer.calculate_package_coupling_matrix()
            >>> coupling = matrix["packages/auth"]["packages/api"]
            >>> print(f"auth->api coupling: {coupling}")
        """
        matrix: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))

        for package in self.packages:
            for dep_path in package.imports_packages:
                # Count as 1 for direct dependency
                matrix[package.path][dep_path] = 1

                # Count transitive dependencies as weaker coupling (0.5)
                dep_package = self.package_by_path.get(dep_path)
                if dep_package:
                    for transitive_dep in dep_package.imports_packages:
                        if transitive_dep != package.path:
                            # Don't overwrite direct dependencies
                            if matrix[package.path][transitive_dep] < 1:
                                matrix[package.path][transitive_dep] = 0.5

        return dict(matrix)

    def get_package_dependency_layers(self) -> List[List[str]]:
        """Calculate package layers using topological sort.

        Packages in the same layer have no dependencies on each other.
        Lower layers are more foundational.

        Returns:
            List of layers, where each layer is a list of package paths

        Example:
            >>> layers = analyzer.get_package_dependency_layers()
            >>> for i, layer in enumerate(layers):
            ...     print(f"Layer {i}: {layer}")
            Layer 0: ['packages/shared', 'packages/utils']
            Layer 1: ['packages/auth', 'packages/database']
            Layer 2: ['packages/api']
            Layer 3: ['packages/frontend']
        """
        # Kahn's algorithm for topological sort
        in_degree = {pkg.path: 0 for pkg in self.packages}
        adjacency = {pkg.path: list(pkg.imports_packages) for pkg in self.packages}

        # Calculate in-degrees (how many packages depend on this one)
        for package in self.packages:
            for dep_path in package.imports_packages:
                if dep_path in in_degree:
                    in_degree[dep_path] += 1

        layers: List[List[str]] = []

        while in_degree:
            # Find all packages with no dependencies (in-degree == 0)
            current_layer = [pkg_path for pkg_path, degree in in_degree.items() if degree == 0]

            if not current_layer:
                # Circular dependency - break arbitrarily
                current_layer = [next(iter(in_degree.keys()))]

            layers.append(current_layer)

            # Remove current layer from graph
            for pkg_path in current_layer:
                del in_degree[pkg_path]

                # Decrease in-degree for dependents
                for other_pkg in list(in_degree.keys()):
                    if pkg_path in adjacency.get(other_pkg, []):
                        in_degree[other_pkg] -= 1

        return layers
