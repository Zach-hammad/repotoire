"""Package detection for monorepos.

Automatically detects packages by scanning for configuration files:
- package.json (npm/yarn/pnpm workspaces, Nx, Turborepo)
- pyproject.toml (Poetry, Hatch, PDM)
- BUILD / BUILD.bazel (Bazel)
- Cargo.toml (Rust)
- go.mod (Go)
"""

import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Set

# Python 3.11+ has tomllib built-in, older versions need tomli
if sys.version_info >= (3, 11):
    import tomllib
else:
    try:
        import tomli as tomllib
    except ImportError:
        tomllib = None  # Will handle gracefully if TOML parsing is needed

from repotoire.monorepo.models import Package, PackageMetadata
from repotoire.logging_config import get_logger

logger = get_logger(__name__)


class PackageDetector:
    """Detects packages in a monorepo by scanning configuration files.

    Supports multiple package formats:
    - npm/yarn/pnpm workspaces (package.json)
    - Nx monorepo (nx.json + package.json)
    - Turborepo (turbo.json + package.json)
    - Poetry (pyproject.toml with [tool.poetry])
    - Bazel (BUILD / BUILD.bazel files)
    - Rust (Cargo.toml workspaces)
    - Go (go.mod modules)

    Example:
        >>> detector = PackageDetector("/path/to/monorepo")
        >>> packages = detector.detect_packages()
        >>> print(f"Found {len(packages)} packages")
        Found 25 packages
        >>> for pkg in packages:
        ...     print(f"  {pkg.name}: {pkg.path}")
    """

    # Configuration file names to search for
    CONFIG_FILES = {
        "package.json",
        "pyproject.toml",
        "BUILD",
        "BUILD.bazel",
        "Cargo.toml",
        "go.mod",
    }

    # Files that indicate root-level configuration
    ROOT_CONFIG_FILES = {
        "nx.json",
        "turbo.json",
        "lerna.json",
        "pnpm-workspace.yaml",
        "rush.json",
    }

    def __init__(self, repository_path: Path):
        """Initialize package detector.

        Args:
            repository_path: Path to monorepo root directory
        """
        self.repository_path = Path(repository_path)
        if not self.repository_path.exists():
            raise ValueError(f"Repository path does not exist: {repository_path}")

        self.packages: List[Package] = []
        self.package_by_path: Dict[str, Package] = {}

    def detect_packages(self) -> List[Package]:
        """Detect all packages in the monorepo.

        Scans for configuration files and extracts package metadata.

        Returns:
            List of Package objects found in the repository
        """
        logger.info(f"Detecting packages in {self.repository_path}")

        # First, check for workspace configuration at root
        workspace_config = self._detect_workspace_config()

        # Scan for package configuration files
        for config_file in self.CONFIG_FILES:
            self._scan_for_config_files(config_file, workspace_config)

        # Build dependency relationships
        self._build_package_dependencies()

        logger.info(f"Detected {len(self.packages)} packages")
        return self.packages

    def _detect_workspace_config(self) -> Optional[Dict]:
        """Detect workspace configuration at repository root.

        Looks for nx.json, turbo.json, lerna.json, pnpm-workspace.yaml, etc.

        Returns:
            Workspace configuration dictionary if found, None otherwise
        """
        # Check for Nx workspace
        nx_config = self.repository_path / "nx.json"
        if nx_config.exists():
            try:
                with open(nx_config) as f:
                    config = json.load(f)
                logger.info("Detected Nx workspace")
                return {"type": "nx", "config": config}
            except Exception as e:
                logger.warning(f"Failed to parse nx.json: {e}")

        # Check for Turborepo
        turbo_config = self.repository_path / "turbo.json"
        if turbo_config.exists():
            try:
                with open(turbo_config) as f:
                    config = json.load(f)
                logger.info("Detected Turborepo workspace")
                return {"type": "turborepo", "config": config}
            except Exception as e:
                logger.warning(f"Failed to parse turbo.json: {e}")

        # Check for Lerna
        lerna_config = self.repository_path / "lerna.json"
        if lerna_config.exists():
            try:
                with open(lerna_config) as f:
                    config = json.load(f)
                logger.info("Detected Lerna workspace")
                return {"type": "lerna", "config": config}
            except Exception as e:
                logger.warning(f"Failed to parse lerna.json: {e}")

        # Check for pnpm workspace
        pnpm_config = self.repository_path / "pnpm-workspace.yaml"
        if pnpm_config.exists():
            logger.info("Detected pnpm workspace")
            return {"type": "pnpm", "config": {}}

        # Check root package.json for yarn/npm workspaces
        root_package = self.repository_path / "package.json"
        if root_package.exists():
            try:
                with open(root_package) as f:
                    config = json.load(f)
                if "workspaces" in config:
                    workspace_type = "yarn" if (self.repository_path / "yarn.lock").exists() else "npm"
                    logger.info(f"Detected {workspace_type} workspaces")
                    return {"type": workspace_type, "config": config}
            except Exception as e:
                logger.warning(f"Failed to parse root package.json: {e}")

        return None

    def _scan_for_config_files(self, config_file: str, workspace_config: Optional[Dict]):
        """Scan repository for specific configuration file type.

        Args:
            config_file: Name of configuration file to search for
            workspace_config: Workspace configuration if detected
        """
        # Find all instances of this config file
        matches = list(self.repository_path.rglob(config_file))

        # Filter out node_modules, .git, and other common exclusions
        matches = [
            m for m in matches
            if not any(part.startswith(('.', 'node_modules', '__pycache__', 'dist', 'build', 'target'))
                      for part in m.parts)
        ]

        logger.debug(f"Found {len(matches)} {config_file} files")

        for config_path in matches:
            try:
                package = self._parse_package_config(config_path, workspace_config)
                if package:
                    self.packages.append(package)
                    self.package_by_path[package.path] = package
            except Exception as e:
                logger.warning(f"Failed to parse {config_path}: {e}")

    def _parse_package_config(
        self, config_path: Path, workspace_config: Optional[Dict]
    ) -> Optional[Package]:
        """Parse package configuration file and create Package object.

        Args:
            config_path: Path to configuration file
            workspace_config: Workspace configuration if detected

        Returns:
            Package object if valid package, None otherwise
        """
        if config_path.name == "package.json":
            return self._parse_package_json(config_path, workspace_config)
        elif config_path.name == "pyproject.toml":
            return self._parse_pyproject_toml(config_path)
        elif config_path.name in ("BUILD", "BUILD.bazel"):
            return self._parse_bazel_build(config_path)
        elif config_path.name == "Cargo.toml":
            return self._parse_cargo_toml(config_path)
        elif config_path.name == "go.mod":
            return self._parse_go_mod(config_path)
        return None

    def _parse_package_json(
        self, config_path: Path, workspace_config: Optional[Dict]
    ) -> Optional[Package]:
        """Parse package.json and create Package.

        Args:
            config_path: Path to package.json
            workspace_config: Workspace configuration

        Returns:
            Package object or None
        """
        try:
            with open(config_path) as f:
                data = json.load(f)

            # Skip if this is the root workspace config
            package_dir = config_path.parent
            if package_dir == self.repository_path and "workspaces" in data:
                return None

            name = data.get("name", package_dir.name)
            package_path = str(package_dir.relative_to(self.repository_path))

            # Determine package type
            package_type = "npm"
            if workspace_config:
                package_type = workspace_config["type"]

            metadata = PackageMetadata(
                name=name,
                version=data.get("version"),
                description=data.get("description"),
                package_type=package_type,
                config_file=str(config_path.relative_to(self.repository_path)),
                dependencies=list(data.get("dependencies", {}).keys()),
                dev_dependencies=list(data.get("devDependencies", {}).keys()),
                scripts=data.get("scripts", {}),
                language="typescript" if "typescript" in data.get("devDependencies", {}) else "javascript",
            )

            # Detect framework
            deps = set(metadata.dependencies + metadata.dev_dependencies)
            if "react" in deps:
                metadata.framework = "react"
            elif "vue" in deps:
                metadata.framework = "vue"
            elif "angular" in deps or "@angular/core" in deps:
                metadata.framework = "angular"
            elif "next" in deps:
                metadata.framework = "next"
            elif "express" in deps:
                metadata.framework = "express"

            # Count files
            files = list(package_dir.rglob("*.ts")) + list(package_dir.rglob("*.js")) + list(package_dir.rglob("*.tsx")) + list(package_dir.rglob("*.jsx"))
            files = [str(f.relative_to(self.repository_path)) for f in files]

            # Detect tests
            test_files = [f for f in files if any(test in f.lower() for test in ["test", "spec", "__tests__"])]

            package = Package(
                path=package_path,
                metadata=metadata,
                files=files,
                has_tests=len(test_files) > 0,
                test_count=len(test_files),
                loc=self._count_loc(files),
            )

            logger.debug(f"Detected npm package: {name} at {package_path}")
            return package

        except Exception as e:
            logger.warning(f"Failed to parse package.json at {config_path}: {e}")
            return None

    def _parse_pyproject_toml(self, config_path: Path) -> Optional[Package]:
        """Parse pyproject.toml and create Package.

        Args:
            config_path: Path to pyproject.toml

        Returns:
            Package object or None
        """
        if tomllib is None:
            logger.warning("TOML parsing not available (install 'tomli' package)")
            return None

        try:
            with open(config_path, "rb") as f:
                data = tomllib.load(f)

            # Check if this is a Poetry/Hatch/PDM package
            if "tool" not in data:
                return None

            package_dir = config_path.parent
            package_path = str(package_dir.relative_to(self.repository_path))

            # Extract metadata
            name = None
            version = None
            description = None
            dependencies = []
            dev_dependencies = []
            package_type = "python"

            if "poetry" in data.get("tool", {}):
                poetry = data["tool"]["poetry"]
                name = poetry.get("name")
                version = poetry.get("version")
                description = poetry.get("description")
                dependencies = list(poetry.get("dependencies", {}).keys())
                dev_dependencies = list(poetry.get("dev-dependencies", {}).keys())
                package_type = "poetry"
            elif "hatch" in data.get("tool", {}):
                hatch = data["tool"]["hatch"]
                name = data.get("project", {}).get("name")
                version = data.get("project", {}).get("version")
                description = data.get("project", {}).get("description")
                package_type = "hatch"
            elif "pdm" in data.get("tool", {}):
                name = data.get("project", {}).get("name")
                version = data.get("project", {}).get("version")
                description = data.get("project", {}).get("description")
                package_type = "pdm"
            elif "project" in data:
                # PEP 621 standard
                project = data["project"]
                name = project.get("name")
                version = project.get("version")
                description = project.get("description")
                dependencies = [dep.split()[0] for dep in project.get("dependencies", [])]

            if not name:
                name = package_dir.name

            metadata = PackageMetadata(
                name=name,
                version=version,
                description=description,
                package_type=package_type,
                config_file=str(config_path.relative_to(self.repository_path)),
                dependencies=dependencies,
                dev_dependencies=dev_dependencies,
                language="python",
            )

            # Detect framework
            deps_set = set(dependencies + dev_dependencies)
            if "fastapi" in deps_set:
                metadata.framework = "fastapi"
            elif "flask" in deps_set:
                metadata.framework = "flask"
            elif "django" in deps_set:
                metadata.framework = "django"

            # Count files
            files = list(package_dir.rglob("*.py"))
            files = [str(f.relative_to(self.repository_path)) for f in files if not any(part.startswith('.') for part in f.parts)]

            # Detect tests
            test_files = [f for f in files if "test" in f.lower()]

            package = Package(
                path=package_path,
                metadata=metadata,
                files=files,
                has_tests=len(test_files) > 0,
                test_count=len(test_files),
                loc=self._count_loc(files),
            )

            logger.debug(f"Detected Python package: {name} at {package_path}")
            return package

        except Exception as e:
            logger.warning(f"Failed to parse pyproject.toml at {config_path}: {e}")
            return None

    def _parse_bazel_build(self, config_path: Path) -> Optional[Package]:
        """Parse BUILD/BUILD.bazel and create Package.

        Args:
            config_path: Path to BUILD file

        Returns:
            Package object or None
        """
        try:
            package_dir = config_path.parent
            package_path = str(package_dir.relative_to(self.repository_path))

            # Read BUILD file and try to extract target names
            with open(config_path) as f:
                content = f.read()

            # Simple regex to find target names (not a full Bazel parser)
            targets = re.findall(r'name\s*=\s*["\']([^"\']+)["\']', content)
            name = targets[0] if targets else package_dir.name

            metadata = PackageMetadata(
                name=name,
                package_type="bazel",
                config_file=str(config_path.relative_to(self.repository_path)),
            )

            # Count files
            files = list(package_dir.rglob("*"))
            files = [str(f.relative_to(self.repository_path)) for f in files if f.is_file() and not any(part.startswith('.') for part in f.parts)]

            package = Package(
                path=package_path,
                metadata=metadata,
                files=files[:100],  # Limit for performance
                loc=self._count_loc(files[:100]),
            )

            logger.debug(f"Detected Bazel package: {name} at {package_path}")
            return package

        except Exception as e:
            logger.warning(f"Failed to parse BUILD file at {config_path}: {e}")
            return None

    def _parse_cargo_toml(self, config_path: Path) -> Optional[Package]:
        """Parse Cargo.toml and create Package.

        Args:
            config_path: Path to Cargo.toml

        Returns:
            Package object or None
        """
        if tomllib is None:
            logger.warning("TOML parsing not available (install 'tomli' package)")
            return None

        try:
            with open(config_path, "rb") as f:
                data = tomllib.load(f)

            # Skip workspace root
            if "workspace" in data and "package" not in data:
                return None

            package_dir = config_path.parent
            package_path = str(package_dir.relative_to(self.repository_path))

            package_data = data.get("package", {})
            name = package_data.get("name", package_dir.name)

            metadata = PackageMetadata(
                name=name,
                version=package_data.get("version"),
                description=package_data.get("description"),
                package_type="cargo",
                config_file=str(config_path.relative_to(self.repository_path)),
                dependencies=list(data.get("dependencies", {}).keys()),
                language="rust",
            )

            # Count files
            files = list(package_dir.rglob("*.rs"))
            files = [str(f.relative_to(self.repository_path)) for f in files]

            # Detect tests
            test_files = [f for f in files if "test" in f.lower() or "/tests/" in f]

            package = Package(
                path=package_path,
                metadata=metadata,
                files=files,
                has_tests=len(test_files) > 0,
                test_count=len(test_files),
                loc=self._count_loc(files),
            )

            logger.debug(f"Detected Rust package: {name} at {package_path}")
            return package

        except Exception as e:
            logger.warning(f"Failed to parse Cargo.toml at {config_path}: {e}")
            return None

    def _parse_go_mod(self, config_path: Path) -> Optional[Package]:
        """Parse go.mod and create Package.

        Args:
            config_path: Path to go.mod

        Returns:
            Package object or None
        """
        try:
            package_dir = config_path.parent
            package_path = str(package_dir.relative_to(self.repository_path))

            # Read go.mod
            with open(config_path) as f:
                content = f.read()

            # Extract module name
            match = re.search(r'module\s+(\S+)', content)
            name = match.group(1) if match else package_dir.name

            # Extract dependencies
            dependencies = re.findall(r'require\s+(\S+)', content)

            metadata = PackageMetadata(
                name=name,
                package_type="go",
                config_file=str(config_path.relative_to(self.repository_path)),
                dependencies=dependencies,
                language="go",
            )

            # Count files
            files = list(package_dir.rglob("*.go"))
            files = [str(f.relative_to(self.repository_path)) for f in files if not any(part.startswith('.') for part in f.parts)]

            # Detect tests
            test_files = [f for f in files if f.endswith("_test.go")]

            package = Package(
                path=package_path,
                metadata=metadata,
                files=files,
                has_tests=len(test_files) > 0,
                test_count=len(test_files),
                loc=self._count_loc(files),
            )

            logger.debug(f"Detected Go package: {name} at {package_path}")
            return package

        except Exception as e:
            logger.warning(f"Failed to parse go.mod at {config_path}: {e}")
            return None

    def _count_loc(self, files: List[str]) -> int:
        """Count total lines of code in files.

        Args:
            files: List of file paths relative to repository root

        Returns:
            Total line count
        """
        total_loc = 0
        for file_path in files:
            try:
                full_path = self.repository_path / file_path
                if full_path.is_file():
                    with open(full_path) as f:
                        total_loc += sum(1 for line in f if line.strip())
            except Exception:
                pass  # Skip files that can't be read
        return total_loc

    def _build_package_dependencies(self):
        """Build dependency relationships between packages.

        Analyzes import/require statements to determine which packages
        depend on each other.
        """
        for package in self.packages:
            # For JS/TS packages, check package.json dependencies
            if package.metadata.package_type in ("npm", "yarn", "nx", "turborepo", "pnpm"):
                self._build_js_dependencies(package)
            # For Python packages, check imports
            elif package.metadata.language == "python":
                self._build_python_dependencies(package)

    def _build_js_dependencies(self, package: Package):
        """Build dependencies for JavaScript/TypeScript package.

        Args:
            package: Package to analyze
        """
        # Check if dependencies reference other packages in monorepo
        for dep in package.metadata.dependencies:
            # Find package with matching name
            for other_package in self.packages:
                if other_package.metadata.name == dep:
                    package.imports_packages.add(other_package.path)
                    other_package.imported_by_packages.add(package.path)

    def _build_python_dependencies(self, package: Package):
        """Build dependencies for Python package.

        Args:
            package: Package to analyze
        """
        # Check if dependencies reference other packages in monorepo
        for dep in package.metadata.dependencies:
            # Find package with matching name
            for other_package in self.packages:
                if other_package.metadata.name == dep:
                    package.imports_packages.add(other_package.path)
                    other_package.imported_by_packages.add(package.path)
