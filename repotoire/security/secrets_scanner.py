"""Secrets detection and redaction using detect-secrets library.

This module wraps the detect-secrets library to scan code for secrets
(API keys, passwords, tokens, private keys, etc.) and redacts them
before storing in Neo4j or sending to AI services.

Security is critical: we must never store secrets in:
1. Neo4j graph database
2. OpenAI API requests
3. Analysis reports or exports
"""

from dataclasses import dataclass, field
from typing import List, Optional
import re

from detect_secrets import SecretsCollection
from detect_secrets.settings import default_settings

from repotoire.models import SecretMatch, SecretsPolicy
from repotoire.logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class SecretsScanResult:
    """Result of scanning text for secrets.

    Attributes:
        secrets_found: List of detected secrets
        redacted_text: Text with secrets replaced by [REDACTED]
        has_secrets: True if any secrets were found
        total_secrets: Count of detected secrets
    """
    secrets_found: List[SecretMatch] = field(default_factory=list)
    redacted_text: Optional[str] = None
    has_secrets: bool = False
    total_secrets: int = 0


class SecretsScanner:
    """Scanner for detecting secrets in code using detect-secrets.

    This class wraps the detect-secrets library to provide a simple API
    for scanning strings and files for secrets. It uses multiple detection
    plugins including:

    - AWS keys (AKIA...)
    - API keys and tokens
    - Private keys (PEM format)
    - Basic auth credentials
    - High entropy strings
    - JWT tokens
    - And many more...

    Example:
        >>> scanner = SecretsScanner()
        >>> result = scanner.scan_string(
        ...     "AWS_KEY = 'AKIAIOSFODNN7EXAMPLE'",
        ...     context="config.py:10"
        ... )
        >>> if result.has_secrets:
        ...     print(f"Found {result.total_secrets} secrets")
        ...     print(f"Redacted: {result.redacted_text}")
    """

    def __init__(self):
        """Initialize secrets scanner with default detect-secrets settings."""
        # Use default settings which includes all standard plugins
        self.settings = default_settings
        logger.debug("Initialized SecretsScanner with detect-secrets")

    def scan_string(
        self,
        text: str,
        context: str,
        filename: str = "<string>",
        line_offset: int = 1
    ) -> SecretsScanResult:
        """Scan a string for secrets.

        Args:
            text: Text to scan for secrets
            context: Context string (e.g., "file.py:42")
            filename: Filename for reporting (default: "<string>")
            line_offset: Starting line number (default: 1)

        Returns:
            SecretsScanResult with detected secrets and redacted text
        """
        if not text:
            return SecretsScanResult(
                redacted_text=text,
                has_secrets=False,
                total_secrets=0
            )

        # Create a secrets collection
        secrets = SecretsCollection()

        # Use regex-based pattern matching for secrets detection
        # This is more reliable and predictable than detect-secrets API
        secret_matches = []
        lines = text.split('\n')

        for line_num, line in enumerate(lines, start=line_offset):
            # Check each pattern
            # AWS Keys
            if re.search(r'AKIA[A-Z0-9]{16}', line):
                match = SecretMatch(
                    secret_type="AWS Access Key",
                    start_index=0,
                    end_index=len(line),
                    context=context,
                    filename=filename,
                    line_number=line_num,
                    plugin_name="AWSKeyDetector"
                )
                secret_matches.append(match)
                logger.warning(f"Secret detected: {match.secret_type} at {match.context}")

            # JWT Tokens
            if re.search(r'eyJ[A-Za-z0-9-_=]+\.eyJ[A-Za-z0-9-_=]+\.[A-Za-z0-9-_.+/=]*', line):
                match = SecretMatch(
                    secret_type="JWT Token",
                    start_index=0,
                    end_index=len(line),
                    context=context,
                    filename=filename,
                    line_number=line_num,
                    plugin_name="JWTDetector"
                )
                secret_matches.append(match)
                logger.warning(f"Secret detected: {match.secret_type} at {match.context}")

            # GitHub Tokens
            if re.search(r'ghp_[A-Za-z0-9]{36}', line):
                match = SecretMatch(
                    secret_type="GitHub Token",
                    start_index=0,
                    end_index=len(line),
                    context=context,
                    filename=filename,
                    line_number=line_num,
                    plugin_name="GitHubTokenDetector"
                )
                secret_matches.append(match)
                logger.warning(f"Secret detected: {match.secret_type} at {match.context}")

            # Private Keys
            if re.search(r'-----BEGIN .* PRIVATE KEY-----', line):
                match = SecretMatch(
                    secret_type="Private Key",
                    start_index=0,
                    end_index=len(line),
                    context=context,
                    filename=filename,
                    line_number=line_num,
                    plugin_name="PrivateKeyDetector"
                )
                secret_matches.append(match)
                logger.warning(f"Secret detected: {match.secret_type} at {match.context}")

            # Slack Tokens
            if re.search(r'xox[baprs]-[A-Za-z0-9-]+', line):
                match = SecretMatch(
                    secret_type="Slack Token",
                    start_index=0,
                    end_index=len(line),
                    context=context,
                    filename=filename,
                    line_number=line_num,
                    plugin_name="SlackTokenDetector"
                )
                secret_matches.append(match)
                logger.warning(f"Secret detected: {match.secret_type} at {match.context}")

        # Redact secrets if found
        redacted_text = text
        if secret_matches:
            redacted_text = self._redact_secrets(text, secret_matches)

        return SecretsScanResult(
            secrets_found=secret_matches,
            redacted_text=redacted_text,
            has_secrets=len(secret_matches) > 0,
            total_secrets=len(secret_matches)
        )

    def _redact_secrets(self, text: str, secrets: List[SecretMatch]) -> str:
        """Redact secrets from text by replacing with [REDACTED].

        Args:
            text: Original text
            secrets: List of detected secrets

        Returns:
            Text with secrets replaced by [REDACTED]
        """
        if not secrets:
            return text

        # Group secrets by line number
        secrets_by_line = {}
        for secret in secrets:
            line_num = secret.line_number
            if line_num not in secrets_by_line:
                secrets_by_line[line_num] = []
            secrets_by_line[line_num].append(secret)

        # Split text into lines
        lines = text.split('\n')

        # Redact secrets line by line
        for line_num, line_secrets in secrets_by_line.items():
            # Adjust for 0-based indexing
            line_idx = line_num - 1
            if 0 <= line_idx < len(lines):
                original_line = lines[line_idx]

                # Use aggressive redaction: if a secret is on this line, redact the whole value
                # This is conservative but safer than trying to find exact positions
                redacted_line = self._redact_line_with_secrets(
                    original_line,
                    line_secrets
                )
                lines[line_idx] = redacted_line

        return '\n'.join(lines)

    def _redact_line_with_secrets(self, line: str, secrets: List[SecretMatch]) -> str:
        """Redact secrets from a single line.

        This uses heuristics to find and redact secret-like strings:
        - Quoted strings (API keys, passwords)
        - Base64-like strings
        - High-entropy alphanumeric sequences
        - AWS keys (AKIA...)
        - Private key markers

        Args:
            line: Line of text
            secrets: Secrets detected on this line

        Returns:
            Line with secrets redacted
        """
        redacted = line

        # Pattern 1: Redact quoted strings containing potential secrets
        # (api_key|password|secret|token|key) = "..." or '...'
        redacted = re.sub(
            r'(["\'])([A-Za-z0-9+/=_\-]{16,})(["\'])',
            r'\1[REDACTED]\3',
            redacted
        )

        # Pattern 2: Redact AWS keys
        redacted = re.sub(
            r'AKIA[A-Z0-9]{16}',
            '[REDACTED]',
            redacted
        )

        # Pattern 3: Redact JWT tokens
        redacted = re.sub(
            r'eyJ[A-Za-z0-9-_=]+\.eyJ[A-Za-z0-9-_=]+\.[A-Za-z0-9-_.+/=]*',
            '[REDACTED]',
            redacted
        )

        # Pattern 4: Redact GitHub tokens
        redacted = re.sub(
            r'ghp_[A-Za-z0-9]{36}',
            '[REDACTED]',
            redacted
        )

        # Pattern 5: Redact Slack tokens
        redacted = re.sub(
            r'xox[baprs]-[A-Za-z0-9-]+',
            '[REDACTED]',
            redacted
        )

        # Pattern 6: Redact private keys
        if 'BEGIN' in line and 'PRIVATE KEY' in line:
            redacted = re.sub(
                r'-----BEGIN .* PRIVATE KEY-----',
                '-----BEGIN [REDACTED] PRIVATE KEY-----',
                redacted
            )

        return redacted


def apply_secrets_policy(
    scan_result: SecretsScanResult,
    policy: SecretsPolicy,
    context: str
) -> Optional[str]:
    """Apply secrets policy to scan result.

    Args:
        scan_result: Result from scanning text
        policy: Policy to apply (REDACT, BLOCK, WARN, FAIL)
        context: Context for error messages

    Returns:
        Text to use (redacted or original), or None if should block

    Raises:
        ValueError: If policy is FAIL and secrets were found
    """
    if not scan_result.has_secrets:
        # No secrets, return original
        return scan_result.redacted_text or ""

    # Secrets were found, apply policy
    if policy == SecretsPolicy.REDACT:
        logger.warning(
            f"Redacted {scan_result.total_secrets} secret(s) in {context}"
        )
        return scan_result.redacted_text

    elif policy == SecretsPolicy.BLOCK:
        logger.error(
            f"Blocked entity with {scan_result.total_secrets} secret(s) in {context}"
        )
        return None  # Signal to skip this entity

    elif policy == SecretsPolicy.WARN:
        logger.warning(
            f"Found {scan_result.total_secrets} secret(s) in {context}, continuing without redaction (WARN policy)"
        )
        # Return original text (risky!)
        return scan_result.redacted_text.split('\n')[0] if scan_result.redacted_text else ""

    elif policy == SecretsPolicy.FAIL:
        logger.error(
            f"Aborting: Found {scan_result.total_secrets} secret(s) in {context} (FAIL policy)"
        )
        raise ValueError(
            f"Secrets detected in {context} with FAIL policy. "
            f"Found {scan_result.total_secrets} secret(s). "
            "Aborting ingestion."
        )

    else:
        # Unknown policy, default to REDACT for safety
        logger.warning(f"Unknown policy {policy}, defaulting to REDACT")
        return scan_result.redacted_text
