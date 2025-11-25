"""Secrets detection and redaction using detect-secrets library.

This module wraps the detect-secrets library to scan code for secrets
(API keys, passwords, tokens, private keys, etc.) and redacts them
before storing in Neo4j or sending to AI services.

Security is critical: we must never store secrets in:
1. Neo4j graph database
2. OpenAI API requests
3. Analysis reports or exports

REPO-148: Enhanced with:
- Entropy-based detection for unknown high-entropy secrets
- Database connection string patterns (PostgreSQL, MySQL, MongoDB, Redis)
- OAuth credential patterns (Bearer tokens, client secrets)
- Additional patterns (SSH keys, certificates)
"""

import math
from collections import Counter
from dataclasses import dataclass, field
from typing import List, Optional, Set, Tuple
import re

from detect_secrets import SecretsCollection
from detect_secrets.settings import default_settings

from repotoire.models import SecretMatch, SecretsPolicy
from repotoire.logging_config import get_logger

logger = get_logger(__name__)

# Entropy thresholds for different string lengths
# Higher entropy = more random = more likely to be a secret
ENTROPY_THRESHOLDS = {
    "short": (16, 32, 3.5),   # (min_len, max_len, threshold)
    "medium": (32, 64, 4.0),
    "long": (64, 256, 4.5),
}

# Known safe high-entropy patterns (hashes, UUIDs, etc.) to allowlist
SAFE_HIGH_ENTROPY_PATTERNS = [
    r'^[a-f0-9]{32}$',  # MD5 hash
    r'^[a-f0-9]{40}$',  # SHA1 hash
    r'^[a-f0-9]{64}$',  # SHA256 hash
    r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$',  # UUID
    r'^\d+\.\d+\.\d+$',  # Version numbers
    r'^v\d+\.\d+\.\d+',  # Version tags
]


def calculate_shannon_entropy(data: str) -> float:
    """Calculate Shannon entropy of a string.

    Higher entropy indicates more randomness, which is characteristic
    of secrets like API keys and passwords.

    Args:
        data: String to analyze

    Returns:
        Shannon entropy value (0.0 to ~4.7 for printable ASCII)
    """
    if not data:
        return 0.0

    # Count character frequencies
    counter = Counter(data)
    length = len(data)

    # Calculate entropy: -sum(p * log2(p)) for each character
    entropy = 0.0
    for count in counter.values():
        probability = count / length
        entropy -= probability * math.log2(probability)

    return entropy


def is_high_entropy_secret(
    value: str,
    min_length: int = 16,
    entropy_threshold: float = 4.0,
) -> Tuple[bool, float]:
    """Check if a string is likely a secret based on entropy.

    Args:
        value: String to check
        min_length: Minimum length to consider
        entropy_threshold: Entropy threshold for detection

    Returns:
        Tuple of (is_secret, entropy_value)
    """
    if len(value) < min_length:
        return False, 0.0

    # Check against safe patterns (hashes, UUIDs, etc.)
    for pattern in SAFE_HIGH_ENTROPY_PATTERNS:
        if re.match(pattern, value, re.IGNORECASE):
            return False, 0.0

    entropy = calculate_shannon_entropy(value)

    # Use dynamic threshold based on length
    if len(value) < 32:
        threshold = ENTROPY_THRESHOLDS["short"][2]
    elif len(value) < 64:
        threshold = ENTROPY_THRESHOLDS["medium"][2]
    else:
        threshold = ENTROPY_THRESHOLDS["long"][2]

    # Override with explicit threshold if provided
    if entropy_threshold:
        threshold = entropy_threshold

    return entropy >= threshold, entropy


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

    def __init__(
        self,
        entropy_detection: bool = True,
        entropy_threshold: float = 4.0,
        min_entropy_length: int = 20,
    ):
        """Initialize secrets scanner with default detect-secrets settings.

        Args:
            entropy_detection: Enable entropy-based detection (REPO-148)
            entropy_threshold: Minimum entropy to flag as secret (default 4.0)
            min_entropy_length: Minimum string length for entropy check (default 20)
        """
        # Use default settings which includes all standard plugins
        self.settings = default_settings
        self.entropy_detection = entropy_detection
        self.entropy_threshold = entropy_threshold
        self.min_entropy_length = min_entropy_length
        logger.debug("Initialized SecretsScanner with detect-secrets")

    def _create_secret_match(
        self,
        secret_type: str,
        plugin_name: str,
        line: str,
        line_num: int,
        context: str,
        filename: str
    ) -> SecretMatch:
        """Helper to create a SecretMatch with common parameters.

        Args:
            secret_type: Type of secret detected
            plugin_name: Name of detection plugin
            line: Line containing the secret
            line_num: Line number in file
            context: Context string
            filename: Filename for reporting

        Returns:
            SecretMatch instance
        """
        match = SecretMatch(
            secret_type=secret_type,
            start_index=0,
            end_index=len(line),
            context=context,
            filename=filename,
            line_number=line_num,
            plugin_name=plugin_name
        )
        logger.warning(f"Secret detected: {secret_type} at {context}")
        return match

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
                match = self._create_secret_match(
                    "AWS Access Key", "AWSKeyDetector",
                    line, line_num, context, filename
                )
                secret_matches.append(match)

            # JWT Tokens
            if re.search(r'eyJ[A-Za-z0-9-_=]+\.eyJ[A-Za-z0-9-_=]+\.[A-Za-z0-9-_.+/=]*', line):
                match = self._create_secret_match(
                    "JWT Token", "JWTDetector",
                    line, line_num, context, filename
                )
                secret_matches.append(match)

            # GitHub Tokens
            if re.search(r'ghp_[A-Za-z0-9]{36}', line):
                match = self._create_secret_match(
                    "GitHub Token", "GitHubTokenDetector",
                    line, line_num, context, filename
                )
                secret_matches.append(match)

            # Private Keys
            if re.search(r'-----BEGIN .* PRIVATE KEY-----', line):
                match = self._create_secret_match(
                    "Private Key", "PrivateKeyDetector",
                    line, line_num, context, filename
                )
                secret_matches.append(match)

            # Slack Tokens
            if re.search(r'xox[baprs]-[A-Za-z0-9-]+', line):
                match = self._create_secret_match(
                    "Slack Token", "SlackTokenDetector",
                    line, line_num, context, filename
                )
                secret_matches.append(match)

            # OpenAI API Keys (sk-proj-... or sk-...)
            if re.search(r'sk-proj-[A-Za-z0-9]{20,}', line):
                match = self._create_secret_match(
                    "OpenAI Project API Key", "OpenAIKeyDetector",
                    line, line_num, context, filename
                )
                secret_matches.append(match)
            elif re.search(r'sk-[A-Za-z0-9]{32,}', line):
                match = self._create_secret_match(
                    "OpenAI API Key", "OpenAIKeyDetector",
                    line, line_num, context, filename
                )
                secret_matches.append(match)

            # Stripe Keys (sk_test/live_, pk_test/live_, rk_test/live_)
            if re.search(r'sk_(test|live)_[A-Za-z0-9]{24,}', line):
                match = self._create_secret_match(
                    "Stripe Secret Key", "StripeKeyDetector",
                    line, line_num, context, filename
                )
                secret_matches.append(match)
            if re.search(r'pk_(test|live)_[A-Za-z0-9]{24,}', line):
                match = self._create_secret_match(
                    "Stripe Publishable Key", "StripeKeyDetector",
                    line, line_num, context, filename
                )
                secret_matches.append(match)
            if re.search(r'rk_(test|live)_[A-Za-z0-9]{24,}', line):
                match = self._create_secret_match(
                    "Stripe Restricted Key", "StripeKeyDetector",
                    line, line_num, context, filename
                )
                secret_matches.append(match)

            # Azure Connection Strings
            if re.search(r'DefaultEndpointsProtocol=https?;.*AccountKey=[A-Za-z0-9+/=]+', line):
                match = self._create_secret_match(
                    "Azure Connection String", "AzureKeyDetector",
                    line, line_num, context, filename
                )
                secret_matches.append(match)

            # Azure Storage Account Keys (base64-like, typically 44-88 chars)
            if re.search(r'AccountKey=[A-Za-z0-9+/]{40,}=*', line):
                match = self._create_secret_match(
                    "Azure Storage Account Key", "AzureKeyDetector",
                    line, line_num, context, filename
                )
                secret_matches.append(match)

            # Google Cloud API Keys
            if re.search(r'AIza[A-Za-z0-9_-]{35}', line):
                match = self._create_secret_match(
                    "Google Cloud API Key", "GoogleCloudKeyDetector",
                    line, line_num, context, filename
                )
                secret_matches.append(match)

            # Google Service Account JSON (private_key field)
            if re.search(r'"private_key"\s*:\s*"-----BEGIN', line):
                match = self._create_secret_match(
                    "Google Service Account Key", "GoogleCloudKeyDetector",
                    line, line_num, context, filename
                )
                secret_matches.append(match)

            # =================================================================
            # REPO-148: Database Connection Strings
            # =================================================================

            # PostgreSQL connection strings
            # postgresql://user:password@host:port/database
            if re.search(r'postgres(?:ql)?://[^:]+:[^@]+@[^/]+', line, re.IGNORECASE):
                match = self._create_secret_match(
                    "PostgreSQL Connection String", "ConnectionStringDetector",
                    line, line_num, context, filename
                )
                secret_matches.append(match)

            # MySQL connection strings
            # mysql://user:password@host:port/database
            if re.search(r'mysql://[^:]+:[^@]+@[^/]+', line, re.IGNORECASE):
                match = self._create_secret_match(
                    "MySQL Connection String", "ConnectionStringDetector",
                    line, line_num, context, filename
                )
                secret_matches.append(match)

            # MongoDB connection strings
            # mongodb://user:password@host:port/database
            # mongodb+srv://user:password@cluster/database
            if re.search(r'mongodb(?:\+srv)?://[^:]+:[^@]+@[^/]+', line, re.IGNORECASE):
                match = self._create_secret_match(
                    "MongoDB Connection String", "ConnectionStringDetector",
                    line, line_num, context, filename
                )
                secret_matches.append(match)

            # Redis connection strings
            # redis://:password@host:port or redis://user:password@host:port
            if re.search(r'redis://(?:[^:]*:)?[^@]+@[^/]+', line, re.IGNORECASE):
                match = self._create_secret_match(
                    "Redis Connection String", "ConnectionStringDetector",
                    line, line_num, context, filename
                )
                secret_matches.append(match)

            # Generic database DSN with password
            # DSN=...;PWD=password or Password=password
            if re.search(r'(?:PWD|Password)\s*=\s*[^;\s]{4,}', line, re.IGNORECASE):
                match = self._create_secret_match(
                    "Database Password", "ConnectionStringDetector",
                    line, line_num, context, filename
                )
                secret_matches.append(match)

            # =================================================================
            # REPO-148: OAuth Credentials
            # =================================================================

            # Bearer tokens (Authorization: Bearer ...)
            if re.search(r'Bearer\s+[A-Za-z0-9_\-\.]{20,}', line):
                match = self._create_secret_match(
                    "Bearer Token", "OAuthDetector",
                    line, line_num, context, filename
                )
                secret_matches.append(match)

            # OAuth client secrets (various formats)
            # client_secret = "..." or clientSecret = "..."
            if re.search(r'(?:client[_-]?secret|oauth[_-]?secret)\s*[=:]\s*["\']?[A-Za-z0-9_\-]{20,}', line, re.IGNORECASE):
                match = self._create_secret_match(
                    "OAuth Client Secret", "OAuthDetector",
                    line, line_num, context, filename
                )
                secret_matches.append(match)

            # OAuth access tokens
            if re.search(r'(?:access[_-]?token|oauth[_-]?token)\s*[=:]\s*["\']?[A-Za-z0-9_\-\.]{20,}', line, re.IGNORECASE):
                match = self._create_secret_match(
                    "OAuth Access Token", "OAuthDetector",
                    line, line_num, context, filename
                )
                secret_matches.append(match)

            # OAuth refresh tokens
            if re.search(r'refresh[_-]?token\s*[=:]\s*["\']?[A-Za-z0-9_\-\.]{20,}', line, re.IGNORECASE):
                match = self._create_secret_match(
                    "OAuth Refresh Token", "OAuthDetector",
                    line, line_num, context, filename
                )
                secret_matches.append(match)

            # =================================================================
            # REPO-148: Additional Patterns
            # =================================================================

            # SSH key passphrases in config or scripts
            if re.search(r'(?:passphrase|ssh[_-]?pass(?:word)?)\s*[=:]\s*["\'][^"\']{8,}["\']', line, re.IGNORECASE):
                match = self._create_secret_match(
                    "SSH Passphrase", "SSHDetector",
                    line, line_num, context, filename
                )
                secret_matches.append(match)

            # PFX/PKCS12 passwords
            if re.search(r'(?:pfx[_-]?password|pkcs12[_-]?pass(?:word)?|cert(?:ificate)?[_-]?pass(?:word)?)\s*[=:]\s*["\'][^"\']{4,}["\']', line, re.IGNORECASE):
                match = self._create_secret_match(
                    "Certificate Password", "CertificateDetector",
                    line, line_num, context, filename
                )
                secret_matches.append(match)

            # Encrypted key materials (looks like base64 encrypted content)
            if re.search(r'-----BEGIN ENCRYPTED PRIVATE KEY-----', line):
                match = self._create_secret_match(
                    "Encrypted Private Key", "PrivateKeyDetector",
                    line, line_num, context, filename
                )
                secret_matches.append(match)

            # Twilio auth tokens
            if re.search(r'twilio[_-]?(?:auth[_-]?)?token\s*[=:]\s*["\']?[a-f0-9]{32}', line, re.IGNORECASE):
                match = self._create_secret_match(
                    "Twilio Auth Token", "TwilioDetector",
                    line, line_num, context, filename
                )
                secret_matches.append(match)

            # SendGrid API keys
            if re.search(r'SG\.[A-Za-z0-9_\-]{22}\.[A-Za-z0-9_\-]{43}', line):
                match = self._create_secret_match(
                    "SendGrid API Key", "SendGridDetector",
                    line, line_num, context, filename
                )
                secret_matches.append(match)

            # Mailchimp API keys
            if re.search(r'[a-f0-9]{32}-us\d{1,2}', line):
                match = self._create_secret_match(
                    "Mailchimp API Key", "MailchimpDetector",
                    line, line_num, context, filename
                )
                secret_matches.append(match)

            # =================================================================
            # REPO-148: Entropy-based Detection
            # =================================================================
            if self.entropy_detection:
                # Find quoted strings that might be secrets
                quoted_strings = re.findall(r'["\']([A-Za-z0-9+/=_\-]{20,})["\']', line)
                for candidate in quoted_strings:
                    # Skip if already matched by a specific pattern
                    if any(candidate in str(m.context) for m in secret_matches if m.line_number == line_num):
                        continue

                    is_secret, entropy = is_high_entropy_secret(
                        candidate,
                        min_length=self.min_entropy_length,
                        entropy_threshold=self.entropy_threshold,
                    )
                    if is_secret:
                        match = self._create_secret_match(
                            f"High Entropy String (entropy={entropy:.2f})",
                            "EntropyDetector",
                            line, line_num, context, filename
                        )
                        secret_matches.append(match)

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

        # Pattern 7: Redact OpenAI API keys
        redacted = re.sub(
            r'sk-proj-[A-Za-z0-9]{20,}',
            '[REDACTED]',
            redacted
        )
        redacted = re.sub(
            r'sk-[A-Za-z0-9]{32,}',
            '[REDACTED]',
            redacted
        )

        # Pattern 8: Redact Stripe keys
        redacted = re.sub(
            r'sk_(test|live)_[A-Za-z0-9]{24,}',
            '[REDACTED]',
            redacted
        )
        redacted = re.sub(
            r'pk_(test|live)_[A-Za-z0-9]{24,}',
            '[REDACTED]',
            redacted
        )
        redacted = re.sub(
            r'rk_(test|live)_[A-Za-z0-9]{24,}',
            '[REDACTED]',
            redacted
        )

        # Pattern 9: Redact Azure connection strings and keys
        redacted = re.sub(
            r'AccountKey=[A-Za-z0-9+/=]+',
            'AccountKey=[REDACTED]',
            redacted
        )

        # Pattern 10: Redact Google Cloud API keys
        redacted = re.sub(
            r'AIza[A-Za-z0-9_-]{35}',
            '[REDACTED]',
            redacted
        )

        # =================================================================
        # REPO-148: Database Connection String Redaction
        # =================================================================

        # Pattern 11: Redact PostgreSQL connection strings (password portion)
        redacted = re.sub(
            r'(postgres(?:ql)?://[^:]+:)([^@]+)(@)',
            r'\1[REDACTED]\3',
            redacted,
            flags=re.IGNORECASE
        )

        # Pattern 12: Redact MySQL connection strings (password portion)
        redacted = re.sub(
            r'(mysql://[^:]+:)([^@]+)(@)',
            r'\1[REDACTED]\3',
            redacted,
            flags=re.IGNORECASE
        )

        # Pattern 13: Redact MongoDB connection strings (password portion)
        redacted = re.sub(
            r'(mongodb(?:\+srv)?://[^:]+:)([^@]+)(@)',
            r'\1[REDACTED]\3',
            redacted,
            flags=re.IGNORECASE
        )

        # Pattern 14: Redact Redis connection strings (password portion)
        redacted = re.sub(
            r'(redis://(?:[^:]*:)?)([^@]+)(@)',
            r'\1[REDACTED]\3',
            redacted,
            flags=re.IGNORECASE
        )

        # Pattern 15: Redact generic database passwords
        redacted = re.sub(
            r'((?:PWD|Password)\s*=\s*)([^;\s]+)',
            r'\1[REDACTED]',
            redacted,
            flags=re.IGNORECASE
        )

        # =================================================================
        # REPO-148: OAuth Credential Redaction
        # =================================================================

        # Pattern 16: Redact Bearer tokens
        redacted = re.sub(
            r'(Bearer\s+)[A-Za-z0-9_\-\.]{20,}',
            r'\1[REDACTED]',
            redacted
        )

        # Pattern 17: Redact OAuth client secrets
        redacted = re.sub(
            r'((?:client[_-]?secret|oauth[_-]?secret)\s*[=:]\s*["\']?)[A-Za-z0-9_\-]{20,}',
            r'\1[REDACTED]',
            redacted,
            flags=re.IGNORECASE
        )

        # Pattern 18: Redact OAuth access/refresh tokens
        redacted = re.sub(
            r'((?:access[_-]?token|oauth[_-]?token|refresh[_-]?token)\s*[=:]\s*["\']?)[A-Za-z0-9_\-\.]{20,}',
            r'\1[REDACTED]',
            redacted,
            flags=re.IGNORECASE
        )

        # =================================================================
        # REPO-148: Additional Pattern Redaction
        # =================================================================

        # Pattern 19: Redact SSH passphrases
        redacted = re.sub(
            r'((?:passphrase|ssh[_-]?pass(?:word)?)\s*[=:]\s*["\'])([^"\']+)(["\'])',
            r'\1[REDACTED]\3',
            redacted,
            flags=re.IGNORECASE
        )

        # Pattern 20: Redact certificate passwords
        redacted = re.sub(
            r'((?:pfx[_-]?password|pkcs12[_-]?pass(?:word)?|cert(?:ificate)?[_-]?pass(?:word)?)\s*[=:]\s*["\'])([^"\']+)(["\'])',
            r'\1[REDACTED]\3',
            redacted,
            flags=re.IGNORECASE
        )

        # Pattern 21: Redact Twilio auth tokens
        redacted = re.sub(
            r'(twilio[_-]?(?:auth[_-]?)?token\s*[=:]\s*["\']?)[a-f0-9]{32}',
            r'\1[REDACTED]',
            redacted,
            flags=re.IGNORECASE
        )

        # Pattern 22: Redact SendGrid API keys
        redacted = re.sub(
            r'SG\.[A-Za-z0-9_\-]{22}\.[A-Za-z0-9_\-]{43}',
            '[REDACTED]',
            redacted
        )

        # Pattern 23: Redact Mailchimp API keys
        redacted = re.sub(
            r'[a-f0-9]{32}-us\d{1,2}',
            '[REDACTED]',
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
