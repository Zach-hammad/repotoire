"""Monorepo analysis and optimization.

Provides specialized support for monorepos with:
- Automatic package detection (package.json, pyproject.toml, BUILD files)
- Per-package health scoring
- Affected packages detection
- Cross-package dependency analysis
- Build impact analysis
- Integration with monorepo tools (Nx, Turborepo, Bazel)
"""

from repotoire.monorepo.affected import AffectedPackagesDetector
from repotoire.monorepo.analyzer import PackageAnalyzer
from repotoire.monorepo.cross_package import CrossPackageAnalyzer
from repotoire.monorepo.detector import PackageDetector
from repotoire.monorepo.models import Package, PackageHealth, PackageMetadata

__all__ = [
    "Package",
    "PackageMetadata",
    "PackageHealth",
    "PackageDetector",
    "PackageAnalyzer",
    "AffectedPackagesDetector",
    "CrossPackageAnalyzer",
]
