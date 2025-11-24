"""Security module for Falkor.

This module handles security-sensitive operations like:
- Secrets detection and redaction
- Dependency vulnerability scanning
- SBOM generation (Software Bill of Materials)
- Compliance reporting (SOC2, ISO 27001, PCI DSS)
- Safe handling of sensitive data
- Security policy enforcement
"""

from repotoire.security.secrets_scanner import SecretsScanner, SecretsScanResult
from repotoire.security.dependency_scanner import DependencyScanner
from repotoire.security.sbom_generator import SBOMGenerator
from repotoire.security.compliance_reporter import ComplianceReporter, ComplianceFramework

__all__ = [
    "SecretsScanner",
    "SecretsScanResult",
    "DependencyScanner",
    "SBOMGenerator",
    "ComplianceReporter",
    "ComplianceFramework",
]
