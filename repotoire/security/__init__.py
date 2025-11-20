"""Security module for Falkor.

This module handles security-sensitive operations like:
- Secrets detection and redaction
- Safe handling of sensitive data
- Security policy enforcement
"""

from repotoire.security.secrets_scanner import SecretsScanner, SecretsScanResult

__all__ = ["SecretsScanner", "SecretsScanResult"]
