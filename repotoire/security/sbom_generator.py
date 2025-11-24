"""SBOM (Software Bill of Materials) generator using CycloneDX format.

Generates comprehensive SBOM documents for dependency tracking, compliance,
and supply chain security analysis.

Supports:
    - CycloneDX format (JSON and XML)
    - SPDX format (via conversion)
    - Component metadata (licenses, versions, hashes)
    - Dependency relationships
    - Vulnerability tracking integration
"""

import json
import subprocess
from pathlib import Path
from typing import Optional, Dict, Any, List
from datetime import datetime

from repotoire.logging_config import get_logger

logger = get_logger(__name__)


class SBOMGenerator:
    """Generate Software Bill of Materials (SBOM) documents.

    Uses cyclonedx-py for SBOM generation with CycloneDX format.

    Configuration:
        repository_path: Path to repository root (required)
        requirements_file: Path to requirements file (default: requirements.txt)
        output_format: SBOM format - json or xml (default: json)
        include_dev: Include development dependencies (default: False)
    """

    SUPPORTED_FORMATS = ["json", "xml"]

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """Initialize SBOM generator.

        Args:
            config: Configuration dictionary with:
                - repository_path: Path to repository root (required)
                - requirements_file: Path to requirements file
                - output_format: Format (json or xml)
                - include_dev: Include dev dependencies
        """
        config = config or {}
        self.repository_path = Path(config.get("repository_path", "."))
        self.requirements_file = config.get("requirements_file", "requirements.txt")
        self.output_format = config.get("output_format", "json")
        self.include_dev = config.get("include_dev", False)

        if not self.repository_path.exists():
            raise ValueError(f"Repository path does not exist: {self.repository_path}")

        if self.output_format not in self.SUPPORTED_FORMATS:
            raise ValueError(
                f"Unsupported format: {self.output_format}. "
                f"Supported: {', '.join(self.SUPPORTED_FORMATS)}"
            )

    def generate(self, output_path: Optional[Path] = None) -> Path:
        """Generate SBOM document.

        Args:
            output_path: Optional output file path. If not provided,
                        generates to repository_path/sbom.{format}

        Returns:
            Path to generated SBOM file

        Raises:
            RuntimeError: If SBOM generation fails
        """
        logger.info(f"Generating SBOM for {self.repository_path}")

        try:
            # Determine output path
            if output_path is None:
                output_path = self.repository_path / f"sbom.{self.output_format}"

            # Generate SBOM using cyclonedx-py
            self._generate_cyclonedx(output_path)

            logger.info(f"SBOM generated successfully: {output_path}")
            return output_path

        except Exception as e:
            logger.error(f"Failed to generate SBOM: {e}", exc_info=True)
            raise RuntimeError(f"SBOM generation failed: {e}")

    def _generate_cyclonedx(self, output_path: Path) -> None:
        """Generate CycloneDX SBOM using cyclonedx-bom tool.

        Args:
            output_path: Path where SBOM will be written
        """
        cmd = ["cyclonedx-py"]

        # Add requirements file if exists
        req_path = self.repository_path / self.requirements_file
        if req_path.exists():
            cmd.extend(["requirements", str(req_path)])
        else:
            # Use pip freeze as fallback
            cmd.extend(["environment"])
            logger.warning(
                f"Requirements file not found: {req_path}, using environment"
            )

        # Set output format
        if self.output_format == "json":
            cmd.extend(["--format", "json"])
        elif self.output_format == "xml":
            cmd.extend(["--format", "xml"])

        # Output file
        cmd.extend(["--output", str(output_path)])

        logger.debug(f"Running: {' '.join(cmd)}")

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=self.repository_path,
            timeout=120,  # 2 minute timeout
        )

        if result.returncode != 0:
            logger.error(f"cyclonedx-py failed: {result.stderr}")
            raise RuntimeError(f"cyclonedx-py execution failed: {result.stderr}")

        # Enrich SBOM with repository metadata
        self._enrich_sbom(output_path)

    def _enrich_sbom(self, sbom_path: Path) -> None:
        """Enrich SBOM with additional metadata.

        Args:
            sbom_path: Path to SBOM file
        """
        if self.output_format != "json":
            return  # Only enrich JSON format

        try:
            with open(sbom_path, "r") as f:
                sbom = json.load(f)

            # Add metadata section if missing
            if "metadata" not in sbom:
                sbom["metadata"] = {}

            # Add generation timestamp
            sbom["metadata"]["timestamp"] = datetime.utcnow().isoformat() + "Z"

            # Add tool information
            sbom["metadata"]["tools"] = [
                {
                    "vendor": "Repotoire",
                    "name": "repotoire-sbom-generator",
                    "version": "0.1.0",
                }
            ]

            # Add repository component info if available
            repo_name = self.repository_path.name
            if "component" not in sbom["metadata"]:
                sbom["metadata"]["component"] = {
                    "type": "application",
                    "name": repo_name,
                    "version": "unknown",
                }

            # Write enriched SBOM
            with open(sbom_path, "w") as f:
                json.dump(sbom, f, indent=2)

        except Exception as e:
            logger.warning(f"Failed to enrich SBOM: {e}")
            # Non-fatal, continue with generated SBOM

    def get_component_count(self, sbom_path: Path) -> int:
        """Get count of components in SBOM.

        Args:
            sbom_path: Path to SBOM file

        Returns:
            Number of components
        """
        try:
            if self.output_format == "json":
                with open(sbom_path, "r") as f:
                    sbom = json.load(f)
                return len(sbom.get("components", []))
            else:
                # For XML, use simple line count heuristic
                with open(sbom_path, "r") as f:
                    content = f.read()
                return content.count("<component>")
        except Exception as e:
            logger.warning(f"Failed to count components: {e}")
            return 0

    def get_summary(self, sbom_path: Path) -> Dict[str, Any]:
        """Get summary statistics from SBOM.

        Args:
            sbom_path: Path to SBOM file

        Returns:
            Dictionary with SBOM statistics
        """
        summary = {
            "total_components": 0,
            "direct_dependencies": 0,
            "transitive_dependencies": 0,
            "licenses": [],
            "format": self.output_format,
            "generated_at": None,
        }

        try:
            if self.output_format == "json":
                with open(sbom_path, "r") as f:
                    sbom = json.load(f)

                components = sbom.get("components", [])
                summary["total_components"] = len(components)

                # Count licenses
                licenses = set()
                for component in components:
                    if "licenses" in component:
                        for license_obj in component["licenses"]:
                            if "license" in license_obj:
                                license_id = license_obj["license"].get("id", "")
                                if license_id:
                                    licenses.add(license_id)

                summary["licenses"] = sorted(licenses)

                # Get timestamp
                if "metadata" in sbom and "timestamp" in sbom["metadata"]:
                    summary["generated_at"] = sbom["metadata"]["timestamp"]

                # Try to determine direct vs transitive
                # (This is a heuristic - CycloneDX has dependency graph)
                dependencies = sbom.get("dependencies", [])
                if dependencies:
                    # Count root dependencies
                    root_deps = [
                        d for d in dependencies
                        if d.get("ref") == sbom.get("metadata", {}).get("component", {}).get("bom-ref")
                    ]
                    if root_deps:
                        summary["direct_dependencies"] = len(root_deps[0].get("dependsOn", []))
                        summary["transitive_dependencies"] = (
                            summary["total_components"] - summary["direct_dependencies"]
                        )

        except Exception as e:
            logger.warning(f"Failed to generate summary: {e}")

        return summary
