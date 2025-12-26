"""Affected packages detection for monorepos.

Determines which packages are affected by code changes using dependency graph traversal.
Critical for optimizing CI/CD in monorepos - only test/build affected packages.
"""

import subprocess
from collections import deque
from pathlib import Path
from typing import List, Set, Dict, Optional

from repotoire.monorepo.models import Package
from repotoire.logging_config import get_logger

logger = get_logger(__name__)


class AffectedPackagesDetector:
    """Detects packages affected by code changes.

    Uses git to find changed files and dependency graph to find affected packages.
    This enables selective testing and building in monorepos.

    Example:
        >>> detector = AffectedPackagesDetector("/path/to/monorepo", packages)
        >>> affected = detector.detect_affected_since("origin/main")
        >>> print(f"Changed: {affected['changed']}")
        >>> print(f"Affected: {affected['affected']}")
        Changed: ['packages/auth']
        Affected: ['packages/api', 'packages/frontend']
    """

    def __init__(self, repository_path: Path, packages: List[Package]):
        """Initialize affected packages detector.

        Args:
            repository_path: Path to monorepo root
            packages: List of all packages in the monorepo
        """
        self.repository_path = Path(repository_path)
        self.packages = packages

        # Build file-to-package mapping for fast lookup
        self.file_to_package: Dict[str, Package] = {}
        for package in packages:
            for file_path in package.files:
                self.file_to_package[file_path] = package

        # Build package dependency graph
        self.package_graph: Dict[str, Package] = {pkg.path: pkg for pkg in packages}

    def detect_affected_since(
        self, base_ref: str, max_depth: int = 10
    ) -> Dict[str, List[str]]:
        """Detect packages affected by changes since a git reference.

        Args:
            base_ref: Git reference (branch, commit, tag) to compare against
            max_depth: Maximum dependency traversal depth (default: 10)

        Returns:
            Dictionary with:
                - 'changed': List of package paths with direct changes
                - 'affected': List of package paths affected by changes (dependents)
                - 'all': Combined list of all packages to rebuild/retest
                - 'changed_files': List of changed file paths
                - 'stats': Statistics about the changes

        Example:
            >>> result = detector.detect_affected_since("origin/main")
            >>> print(f"Need to test {len(result['all'])} packages")
        """
        logger.info(f"Detecting affected packages since {base_ref}")

        # Get changed files from git
        changed_files = self._get_changed_files(base_ref)

        if not changed_files:
            logger.info("No changed files detected")
            return {
                "changed": [],
                "affected": [],
                "all": [],
                "changed_files": [],
                "stats": {"changed_packages": 0, "affected_packages": 0, "total_packages": 0},
            }

        logger.debug(f"Found {len(changed_files)} changed files")

        # Find packages with direct changes
        changed_packages = self._find_changed_packages(changed_files)
        logger.info(f"Found {len(changed_packages)} packages with direct changes")

        # Find packages affected by changes (traverse dependency graph)
        affected_packages = self._find_affected_packages(changed_packages, max_depth)
        logger.info(f"Found {len(affected_packages)} packages affected by changes")

        # Combine all packages that need to be tested/built
        all_packages = sorted(set(changed_packages) | set(affected_packages))

        result = {
            "changed": sorted(changed_packages),
            "affected": sorted(affected_packages),
            "all": all_packages,
            "changed_files": changed_files,
            "stats": {
                "changed_packages": len(changed_packages),
                "affected_packages": len(affected_packages),
                "total_packages": len(all_packages),
                "changed_files": len(changed_files),
            },
        }

        logger.info(
            "Affected packages detection complete",
            extra=result["stats"],
        )

        return result

    def detect_affected_by_files(
        self, file_paths: List[str], max_depth: int = 10
    ) -> Dict[str, List[str]]:
        """Detect packages affected by specific file changes.

        Useful for pre-commit hooks or real-time analysis.

        Args:
            file_paths: List of file paths that changed
            max_depth: Maximum dependency traversal depth

        Returns:
            Dictionary with changed and affected packages

        Example:
            >>> result = detector.detect_affected_by_files(["packages/auth/src/auth.ts"])
            >>> print(result['all'])
            ['packages/auth', 'packages/api', 'packages/frontend']
        """
        logger.info(f"Detecting affected packages for {len(file_paths)} files")

        # Normalize file paths to be relative to repository root
        normalized_files = []
        for file_path in file_paths:
            try:
                path = Path(file_path)
                if path.is_absolute():
                    path = path.relative_to(self.repository_path)
                normalized_files.append(str(path))
            except Exception as e:
                logger.warning(f"Failed to normalize file path {file_path}: {e}")
                continue

        # Find packages with direct changes
        changed_packages = self._find_changed_packages(normalized_files)

        # Find packages affected by changes
        affected_packages = self._find_affected_packages(changed_packages, max_depth)

        # Combine all
        all_packages = sorted(set(changed_packages) | set(affected_packages))

        return {
            "changed": sorted(changed_packages),
            "affected": sorted(affected_packages),
            "all": all_packages,
            "changed_files": normalized_files,
            "stats": {
                "changed_packages": len(changed_packages),
                "affected_packages": len(affected_packages),
                "total_packages": len(all_packages),
            },
        }

    def get_dependency_graph(self) -> Dict[str, Dict]:
        """Get the full package dependency graph.

        Returns:
            Dictionary mapping package paths to their dependency info:
                - 'imports': Packages this package imports
                - 'imported_by': Packages that import this package

        Example:
            >>> graph = detector.get_dependency_graph()
            >>> for pkg_path, deps in graph.items():
            ...     print(f"{pkg_path}: imports {deps['imports']}")
        """
        graph = {}

        for package in self.packages:
            graph[package.path] = {
                "name": package.name,
                "imports": sorted(package.imports_packages),
                "imported_by": sorted(package.imported_by_packages),
            }

        return graph

    def _get_changed_files(self, base_ref: str) -> List[str]:
        """Get list of changed files since a git reference.

        Args:
            base_ref: Git reference to compare against

        Returns:
            List of changed file paths (relative to repo root)
        """
        try:
            # Use git diff to find changed files
            cmd = ["git", "diff", "--name-only", base_ref, "HEAD"]

            result = subprocess.run(
                cmd,
                cwd=self.repository_path,
                capture_output=True,
                text=True,
                timeout=30,
            )

            if result.returncode != 0:
                logger.error(f"Git diff failed: {result.stderr}")
                return []

            # Parse file paths
            changed_files = [
                line.strip()
                for line in result.stdout.strip().split("\n")
                if line.strip()
            ]

            return changed_files

        except subprocess.TimeoutExpired:
            logger.error("Git diff timed out")
            return []
        except Exception as e:
            logger.error(f"Failed to get changed files: {e}")
            return []

    def _find_changed_packages(self, changed_files: List[str]) -> List[str]:
        """Find packages that have direct file changes.

        Args:
            changed_files: List of changed file paths

        Returns:
            List of package paths with changes
        """
        changed_packages: Set[str] = set()

        for file_path in changed_files:
            # Find which package this file belongs to
            if file_path in self.file_to_package:
                package = self.file_to_package[file_path]
                changed_packages.add(package.path)
            else:
                # File might not be in any package (root-level files)
                logger.debug(f"File not in any package: {file_path}")

        return list(changed_packages)

    def _find_affected_packages(
        self, changed_packages: List[str], max_depth: int
    ) -> List[str]:
        """Find packages affected by changes using BFS traversal.

        Traverses the dependency graph to find all packages that depend
        (directly or transitively) on the changed packages.

        Args:
            changed_packages: List of package paths with direct changes
            max_depth: Maximum dependency traversal depth

        Returns:
            List of affected package paths (excluding changed packages)
        """
        affected: Set[str] = set()
        queue: deque[tuple[str, int]] = deque((pkg, 0) for pkg in changed_packages)
        visited: Set[str] = set(changed_packages)

        while queue:
            current_pkg, depth = queue.popleft()  # O(1) vs O(n) for list.pop(0)

            # Stop if we've reached max depth
            if depth >= max_depth:
                continue

            # Get package object
            if current_pkg not in self.package_graph:
                continue

            package = self.package_graph[current_pkg]

            # Find packages that import this package (dependents)
            for dependent_pkg in package.imported_by_packages:
                if dependent_pkg not in visited:
                    visited.add(dependent_pkg)
                    affected.add(dependent_pkg)
                    queue.append((dependent_pkg, depth + 1))

        # Remove changed packages from affected (they're already in "changed")
        affected -= set(changed_packages)

        return list(affected)

    def generate_build_commands(
        self, affected_result: Dict[str, List[str]], tool: str = "auto"
    ) -> List[str]:
        """Generate build/test commands for affected packages.

        Args:
            affected_result: Result from detect_affected_since()
            tool: Monorepo tool to use (nx, turborepo, auto)

        Returns:
            List of command strings to run

        Example:
            >>> result = detector.detect_affected_since("main")
            >>> commands = detector.generate_build_commands(result, tool="nx")
            >>> print(commands)
            ['nx run-many --target=test --projects=auth,api,frontend']
        """
        all_packages = affected_result["all"]

        if not all_packages:
            return []

        # Detect tool if auto
        if tool == "auto":
            tool = self._detect_monorepo_tool()

        commands = []

        if tool == "nx":
            # Generate Nx commands
            package_names = [self.package_graph[p].name for p in all_packages]
            projects = ",".join(package_names)
            commands.append(f"nx run-many --target=test --projects={projects}")
            commands.append(f"nx run-many --target=build --projects={projects}")

        elif tool == "turborepo":
            # Generate Turborepo commands
            package_names = [self.package_graph[p].name for p in all_packages]
            filter_args = " ".join([f"--filter={name}" for name in package_names])
            commands.append(f"turbo run test {filter_args}")
            commands.append(f"turbo run build {filter_args}")

        elif tool == "lerna":
            # Generate Lerna commands
            scope_args = " ".join([f"--scope={self.package_graph[p].name}" for p in all_packages])
            commands.append(f"lerna run test {scope_args}")
            commands.append(f"lerna run build {scope_args}")

        else:
            # Generic commands - just list package paths
            for pkg_path in all_packages:
                package = self.package_graph[pkg_path]
                commands.append(f"# Package: {package.name} ({pkg_path})")

        return commands

    def _detect_monorepo_tool(self) -> str:
        """Detect which monorepo tool is being used.

        Returns:
            Tool name: nx, turborepo, lerna, or generic
        """
        if (self.repository_path / "nx.json").exists():
            return "nx"
        elif (self.repository_path / "turbo.json").exists():
            return "turborepo"
        elif (self.repository_path / "lerna.json").exists():
            return "lerna"
        else:
            return "generic"
