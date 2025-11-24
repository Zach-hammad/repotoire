"""Tests for package detection in monorepos."""

import json
import pytest
from pathlib import Path
from unittest.mock import Mock, patch, mock_open

from repotoire.monorepo.detector import PackageDetector
from repotoire.monorepo.models import Package, PackageMetadata


@pytest.fixture
def temp_monorepo(tmp_path):
    """Create a temporary monorepo structure for testing."""
    # Create root
    root = tmp_path / "monorepo"
    root.mkdir()

    # Create npm package
    npm_pkg = root / "packages" / "auth"
    npm_pkg.mkdir(parents=True)
    (npm_pkg / "package.json").write_text(json.dumps({
        "name": "@myapp/auth",
        "version": "1.0.0",
        "description": "Authentication package",
        "dependencies": {
            "express": "^4.18.0",
            "jsonwebtoken": "^9.0.0"
        },
        "devDependencies": {
            "typescript": "^5.0.0"
        }
    }))
    (npm_pkg / "src").mkdir()
    (npm_pkg / "src" / "index.ts").write_text("export const auth = () => {};")

    # Create Python package
    py_pkg = root / "packages" / "backend"
    py_pkg.mkdir(parents=True)
    (py_pkg / "pyproject.toml").write_text("""
[tool.poetry]
name = "backend"
version = "0.1.0"
description = "Backend package"

[tool.poetry.dependencies]
python = "^3.10"
fastapi = "^0.100.0"
""")
    (py_pkg / "src").mkdir()
    (py_pkg / "src" / "main.py").write_text("from fastapi import FastAPI")

    return root


def test_detector_initialization():
    """Test PackageDetector initialization."""
    detector = PackageDetector(Path("."))
    assert detector.repository_path == Path(".")
    assert detector.packages == []


def test_detector_invalid_path():
    """Test PackageDetector with invalid path."""
    with pytest.raises(ValueError, match="does not exist"):
        PackageDetector(Path("/nonexistent/path"))


def test_detect_npm_packages(temp_monorepo):
    """Test detection of npm packages."""
    detector = PackageDetector(temp_monorepo)
    packages = detector.detect_packages()

    # Find the npm package
    npm_pkg = next((p for p in packages if p.metadata.name == "@myapp/auth"), None)
    assert npm_pkg is not None
    assert npm_pkg.metadata.package_type == "npm"
    assert npm_pkg.metadata.version == "1.0.0"
    assert "express" in npm_pkg.metadata.dependencies
    assert "typescript" in npm_pkg.metadata.dev_dependencies
    assert npm_pkg.metadata.language == "typescript"


def test_detect_python_packages(temp_monorepo):
    """Test detection of Python packages."""
    detector = PackageDetector(temp_monorepo)
    packages = detector.detect_packages()

    # Find the Python package
    py_pkg = next((p for p in packages if p.metadata.name == "backend"), None)
    assert py_pkg is not None
    assert py_pkg.metadata.package_type == "poetry"
    assert py_pkg.metadata.version == "0.1.0"
    assert "fastapi" in py_pkg.metadata.dependencies
    assert py_pkg.metadata.language == "python"
    assert py_pkg.metadata.framework == "fastapi"


def test_detect_workspace_nx(tmp_path):
    """Test detection of Nx workspace."""
    root = tmp_path / "nx-monorepo"
    root.mkdir()

    # Create nx.json
    (root / "nx.json").write_text(json.dumps({
        "npmScope": "myorg",
        "affected": {"defaultBase": "main"}
    }))

    detector = PackageDetector(root)
    workspace_config = detector._detect_workspace_config()

    assert workspace_config is not None
    assert workspace_config["type"] == "nx"


def test_detect_workspace_turborepo(tmp_path):
    """Test detection of Turborepo workspace."""
    root = tmp_path / "turbo-monorepo"
    root.mkdir()

    # Create turbo.json
    (root / "turbo.json").write_text(json.dumps({
        "pipeline": {
            "build": {"dependsOn": ["^build"]}
        }
    }))

    detector = PackageDetector(root)
    workspace_config = detector._detect_workspace_config()

    assert workspace_config is not None
    assert workspace_config["type"] == "turborepo"


def test_package_dependency_building(temp_monorepo):
    """Test building package dependencies."""
    # Create two packages with dependency relationship
    pkg1_dir = temp_monorepo / "packages" / "shared"
    pkg1_dir.mkdir(parents=True)
    (pkg1_dir / "package.json").write_text(json.dumps({
        "name": "@myapp/shared",
        "version": "1.0.0"
    }))

    pkg2_dir = temp_monorepo / "packages" / "api"
    pkg2_dir.mkdir(parents=True)
    (pkg2_dir / "package.json").write_text(json.dumps({
        "name": "@myapp/api",
        "version": "1.0.0",
        "dependencies": {
            "@myapp/shared": "^1.0.0"
        }
    }))

    detector = PackageDetector(temp_monorepo)
    packages = detector.detect_packages()

    # Find packages
    shared = next((p for p in packages if p.metadata.name == "@myapp/shared"), None)
    api = next((p for p in packages if p.metadata.name == "@myapp/api"), None)

    assert shared is not None
    assert api is not None

    # Check dependency relationships
    assert shared.path in api.imports_packages
    assert api.path in shared.imported_by_packages


def test_filter_excluded_directories(tmp_path):
    """Test that node_modules and other directories are excluded."""
    root = tmp_path / "monorepo"
    root.mkdir()

    # Create package.json in node_modules (should be excluded)
    node_modules = root / "node_modules" / "some-package"
    node_modules.mkdir(parents=True)
    (node_modules / "package.json").write_text(json.dumps({
        "name": "some-package",
        "version": "1.0.0"
    }))

    # Create package.json in valid location
    valid_pkg = root / "packages" / "valid"
    valid_pkg.mkdir(parents=True)
    (valid_pkg / "package.json").write_text(json.dumps({
        "name": "valid-package",
        "version": "1.0.0"
    }))

    detector = PackageDetector(root)
    packages = detector.detect_packages()

    # Should only find the valid package
    assert len(packages) == 1
    assert packages[0].metadata.name == "valid-package"


def test_count_loc(temp_monorepo):
    """Test LOC counting for packages."""
    detector = PackageDetector(temp_monorepo)
    packages = detector.detect_packages()

    # Check that LOC is calculated
    for package in packages:
        assert package.loc >= 0


def test_detect_tests(temp_monorepo):
    """Test detection of test files in packages."""
    # Add test file to auth package
    auth_pkg = temp_monorepo / "packages" / "auth"
    test_dir = auth_pkg / "src" / "__tests__"
    test_dir.mkdir(parents=True)
    (test_dir / "auth.test.ts").write_text("test('auth', () => {});")

    detector = PackageDetector(temp_monorepo)
    packages = detector.detect_packages()

    auth = next((p for p in packages if p.metadata.name == "@myapp/auth"), None)
    assert auth is not None
    assert auth.has_tests is True
    assert auth.test_count >= 1


def test_detect_go_package(tmp_path):
    """Test detection of Go packages."""
    root = tmp_path / "go-monorepo"
    pkg_dir = root / "services" / "api"
    pkg_dir.mkdir(parents=True)

    # Create go.mod
    (pkg_dir / "go.mod").write_text("""
module github.com/myorg/api

go 1.21

require (
    github.com/gin-gonic/gin v1.9.0
)
""")

    # Create some Go files
    (pkg_dir / "main.go").write_text("package main\n\nfunc main() {}")
    (pkg_dir / "handler.go").write_text("package main\n\nfunc handler() {}")

    detector = PackageDetector(root)
    packages = detector.detect_packages()

    assert len(packages) == 1
    go_pkg = packages[0]
    assert go_pkg.metadata.name == "github.com/myorg/api"
    assert go_pkg.metadata.package_type == "go"
    assert go_pkg.metadata.language == "go"
    assert len(go_pkg.files) == 2


def test_detect_rust_package(tmp_path):
    """Test detection of Rust packages."""
    root = tmp_path / "rust-monorepo"
    pkg_dir = root / "crates" / "core"
    pkg_dir.mkdir(parents=True)

    # Create Cargo.toml
    (pkg_dir / "Cargo.toml").write_text("""
[package]
name = "myapp-core"
version = "0.1.0"
description = "Core library"

[dependencies]
serde = "1.0"
""")

    # Create Rust files
    src_dir = pkg_dir / "src"
    src_dir.mkdir()
    (src_dir / "lib.rs").write_text("pub fn hello() {}")

    detector = PackageDetector(root)
    packages = detector.detect_packages()

    assert len(packages) == 1
    rust_pkg = packages[0]
    assert rust_pkg.metadata.name == "myapp-core"
    assert rust_pkg.metadata.package_type == "cargo"
    assert rust_pkg.metadata.language == "rust"
    assert rust_pkg.metadata.version == "0.1.0"


def test_detect_bazel_package(tmp_path):
    """Test detection of Bazel packages."""
    root = tmp_path / "bazel-monorepo"
    pkg_dir = root / "services" / "api"
    pkg_dir.mkdir(parents=True)

    # Create BUILD file
    (pkg_dir / "BUILD").write_text("""
py_library(
    name = "api",
    srcs = ["api.py"],
)
""")

    detector = PackageDetector(root)
    packages = detector.detect_packages()

    assert len(packages) == 1
    bazel_pkg = packages[0]
    assert bazel_pkg.metadata.name == "api"
    assert bazel_pkg.metadata.package_type == "bazel"


def test_parse_package_json_framework_detection(tmp_path):
    """Test framework detection for different JS/TS frameworks."""
    root = tmp_path / "monorepo"

    frameworks = [
        ("react", "@myapp/react-app", {"react": "^18.0.0"}),
        ("vue", "@myapp/vue-app", {"vue": "^3.0.0"}),
        ("angular", "@myapp/angular-app", {"@angular/core": "^16.0.0"}),
        ("next", "@myapp/next-app", {"next": "^13.0.0"}),
        ("express", "@myapp/express-api", {"express": "^4.18.0"}),
    ]

    for expected_framework, name, deps in frameworks:
        pkg_dir = root / "packages" / name.split("/")[1]
        pkg_dir.mkdir(parents=True)
        (pkg_dir / "package.json").write_text(json.dumps({
            "name": name,
            "version": "1.0.0",
            "dependencies": deps
        }))

    detector = PackageDetector(root)
    packages = detector.detect_packages()

    for expected_framework, name, _ in frameworks:
        pkg = next((p for p in packages if p.metadata.name == name), None)
        assert pkg is not None, f"Package {name} not found"
        assert pkg.metadata.framework == expected_framework, \
            f"Expected {expected_framework}, got {pkg.metadata.framework}"


def test_to_dict_serialization(temp_monorepo):
    """Test Package serialization to dictionary."""
    detector = PackageDetector(temp_monorepo)
    packages = detector.detect_packages()

    assert len(packages) > 0
    pkg_dict = packages[0].to_dict()

    assert "path" in pkg_dict
    assert "metadata" in pkg_dict
    assert "files" in pkg_dict
    assert "imports_packages" in pkg_dict
    assert "has_tests" in pkg_dict
