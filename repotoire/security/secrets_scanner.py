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

REPO-149: Enhanced with:
- Pre-compiled regex patterns for performance
- Streaming support for large files (>1MB)
- Parallel scanning with multiprocessing
- Hash-based caching for unchanged files
- Enhanced reporting (positions, risk levels, remediation)
- Custom pattern support via configuration

REPO-313: Enhanced with:
- Redis-based caching via ScanCache for distributed caching
- Content-hash based cache keys for automatic invalidation
- 24-hour TTL for long-term caching of unchanged files
"""

import hashlib
import math
import os
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Pattern, Set, Tuple
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

# Risk levels for secret types
RISK_LEVELS = {
    "critical": ["AWS Access Key", "Private Key", "Google Service Account Key",
                 "PostgreSQL Connection String", "MySQL Connection String",
                 "MongoDB Connection String", "Encrypted Private Key"],
    "high": ["GitHub Token", "OpenAI API Key", "OpenAI Project API Key",
             "Stripe Secret Key", "Azure Storage Account Key", "Google Cloud API Key",
             "OAuth Client Secret", "Database Password", "SSH Passphrase",
             "Certificate Password", "Twilio Auth Token", "SendGrid API Key"],
    "medium": ["JWT Token", "Slack Token", "Bearer Token", "OAuth Access Token",
               "OAuth Refresh Token", "Stripe Publishable Key", "Stripe Restricted Key",
               "Azure Connection String", "Redis Connection String", "Mailchimp API Key"],
    "low": ["High Entropy String"],
}

# Remediation suggestions by secret type
REMEDIATION_SUGGESTIONS = {
    "AWS Access Key": "Rotate the AWS access key immediately via IAM console. Use IAM roles or environment variables instead.",
    "Private Key": "Remove the private key from source code. Store in a secrets manager (AWS Secrets Manager, HashiCorp Vault).",
    "GitHub Token": "Revoke the token at github.com/settings/tokens and generate a new one. Use GitHub Actions secrets for CI/CD.",
    "OpenAI API Key": "Rotate the key at platform.openai.com/api-keys. Use environment variables (OPENAI_API_KEY).",
    "OpenAI Project API Key": "Rotate the key at platform.openai.com/api-keys. Use environment variables (OPENAI_API_KEY).",
    "JWT Token": "Tokens should not be hardcoded. Pass via headers or environment variables at runtime.",
    "Slack Token": "Revoke at api.slack.com/apps. Use Slack's OAuth flow and store tokens securely.",
    "Stripe Secret Key": "Rotate at dashboard.stripe.com/apikeys. Never expose in client-side code.",
    "Stripe Publishable Key": "While publishable keys are less sensitive, avoid hardcoding. Use environment variables.",
    "Azure Connection String": "Rotate keys in Azure portal. Use Azure Key Vault for production.",
    "Azure Storage Account Key": "Rotate in Azure portal. Use Managed Identity or SAS tokens instead.",
    "Google Cloud API Key": "Restrict the key in Google Cloud Console. Use service accounts for server-side code.",
    "Google Service Account Key": "Rotate in Google Cloud Console. Use Workload Identity Federation when possible.",
    "PostgreSQL Connection String": "Use environment variables or secrets manager. Never commit database credentials.",
    "MySQL Connection String": "Use environment variables or secrets manager. Never commit database credentials.",
    "MongoDB Connection String": "Use environment variables or secrets manager. Consider MongoDB Atlas with IAM.",
    "Redis Connection String": "Use environment variables. Consider Redis ACLs for access control.",
    "Database Password": "Store in environment variables or secrets manager. Use least-privilege database users.",
    "Bearer Token": "Tokens should be passed at runtime, not stored in code. Use secure token storage.",
    "OAuth Client Secret": "Store in environment variables or secrets manager. Never expose in client-side code.",
    "OAuth Access Token": "Access tokens are temporary. Implement proper token refresh flow.",
    "OAuth Refresh Token": "Store securely server-side. Implement token rotation.",
    "SSH Passphrase": "Use SSH agent or encrypted key files. Never store passphrases in code.",
    "Certificate Password": "Store in secrets manager. Use certificate-based auth where possible.",
    "Encrypted Private Key": "While encrypted, the key should not be in source control. Use secrets manager.",
    "Twilio Auth Token": "Rotate in Twilio Console. Use environment variables.",
    "SendGrid API Key": "Rotate in SendGrid dashboard. Use environment variables.",
    "Mailchimp API Key": "Rotate in Mailchimp account settings. Use environment variables.",
    "High Entropy String": "Review this string - it may be a secret. If so, move to environment variables.",
}

# Pre-compiled detection patterns for performance
# Each pattern tuple: (compiled_regex, secret_type, plugin_name, risk_level)
COMPILED_PATTERNS: List[Tuple[Pattern, str, str, str]] = []


def _compile_patterns() -> List[Tuple[Pattern, str, str, str]]:
    """Compile all detection patterns once at module load."""
    patterns = [
        # AWS Keys
        (r'AKIA[A-Z0-9]{16}', "AWS Access Key", "AWSKeyDetector", "critical"),
        # JWT Tokens
        (r'eyJ[A-Za-z0-9-_=]+\.eyJ[A-Za-z0-9-_=]+\.[A-Za-z0-9-_.+/=]*', "JWT Token", "JWTDetector", "medium"),
        # GitHub Tokens
        (r'ghp_[A-Za-z0-9]{36}', "GitHub Token", "GitHubTokenDetector", "high"),
        # Private Keys
        (r'-----BEGIN .* PRIVATE KEY-----', "Private Key", "PrivateKeyDetector", "critical"),
        # Slack Tokens
        (r'xox[baprs]-[A-Za-z0-9-]+', "Slack Token", "SlackTokenDetector", "medium"),
        # OpenAI API Keys
        (r'sk-proj-[A-Za-z0-9]{20,}', "OpenAI Project API Key", "OpenAIKeyDetector", "high"),
        (r'sk-[A-Za-z0-9]{32,}', "OpenAI API Key", "OpenAIKeyDetector", "high"),
        # Stripe Keys
        (r'sk_(test|live)_[A-Za-z0-9]{24,}', "Stripe Secret Key", "StripeKeyDetector", "high"),
        (r'pk_(test|live)_[A-Za-z0-9]{24,}', "Stripe Publishable Key", "StripeKeyDetector", "medium"),
        (r'rk_(test|live)_[A-Za-z0-9]{24,}', "Stripe Restricted Key", "StripeKeyDetector", "medium"),
        # Azure
        (r'DefaultEndpointsProtocol=https?;.*AccountKey=[A-Za-z0-9+/=]+', "Azure Connection String", "AzureKeyDetector", "medium"),
        (r'AccountKey=[A-Za-z0-9+/]{40,}=*', "Azure Storage Account Key", "AzureKeyDetector", "high"),
        # Google Cloud
        (r'AIza[A-Za-z0-9_-]{35}', "Google Cloud API Key", "GoogleCloudKeyDetector", "high"),
        (r'"private_key"\s*:\s*"-----BEGIN', "Google Service Account Key", "GoogleCloudKeyDetector", "critical"),
        # Database Connection Strings
        (r'postgres(?:ql)?://[^:]+:[^@]+@[^/]+', "PostgreSQL Connection String", "ConnectionStringDetector", "critical"),
        (r'mysql://[^:]+:[^@]+@[^/]+', "MySQL Connection String", "ConnectionStringDetector", "critical"),
        (r'mongodb(?:\+srv)?://[^:]+:[^@]+@[^/]+', "MongoDB Connection String", "ConnectionStringDetector", "critical"),
        (r'redis://(?:[^:]*:)?[^@]+@[^/]+', "Redis Connection String", "ConnectionStringDetector", "medium"),
        (r'(?:PWD|Password)\s*=\s*[^;\s]{4,}', "Database Password", "ConnectionStringDetector", "high"),
        # OAuth
        (r'Bearer\s+[A-Za-z0-9_\-\.]{20,}', "Bearer Token", "OAuthDetector", "medium"),
        (r'(?:client[_-]?secret|oauth[_-]?secret)\s*[=:]\s*["\']?[A-Za-z0-9_\-]{20,}', "OAuth Client Secret", "OAuthDetector", "high"),
        (r'(?:access[_-]?token|oauth[_-]?token)\s*[=:]\s*["\']?[A-Za-z0-9_\-\.]{20,}', "OAuth Access Token", "OAuthDetector", "medium"),
        (r'refresh[_-]?token\s*[=:]\s*["\']?[A-Za-z0-9_\-\.]{20,}', "OAuth Refresh Token", "OAuthDetector", "medium"),
        # SSH/Certificates
        (r'(?:passphrase|ssh[_-]?pass(?:word)?)\s*[=:]\s*["\'][^"\']{8,}["\']', "SSH Passphrase", "SSHDetector", "high"),
        (r'(?:pfx[_-]?password|pkcs12[_-]?pass(?:word)?|cert(?:ificate)?[_-]?pass(?:word)?)\s*[=:]\s*["\'][^"\']{4,}["\']', "Certificate Password", "CertificateDetector", "high"),
        (r'-----BEGIN ENCRYPTED PRIVATE KEY-----', "Encrypted Private Key", "PrivateKeyDetector", "critical"),
        # Third-party services
        (r'twilio[_-]?(?:auth[_-]?)?token\s*[=:]\s*["\']?[a-f0-9]{32}', "Twilio Auth Token", "TwilioDetector", "high"),
        (r'SG\.[A-Za-z0-9_\-]{22}\.[A-Za-z0-9_\-]{43}', "SendGrid API Key", "SendGridDetector", "high"),
        (r'[a-f0-9]{32}-us\d{1,2}', "Mailchimp API Key", "MailchimpDetector", "medium"),
    ]

    compiled = []
    for pattern, secret_type, plugin_name, risk_level in patterns:
        try:
            compiled.append((re.compile(pattern, re.IGNORECASE), secret_type, plugin_name, risk_level))
        except re.error as e:
            logger.warning(f"Failed to compile pattern for {secret_type}: {e}")

    return compiled


# Initialize compiled patterns at module load
COMPILED_PATTERNS = _compile_patterns()

# Compile safe patterns too
COMPILED_SAFE_PATTERNS = [re.compile(p, re.IGNORECASE) for p in SAFE_HIGH_ENTROPY_PATTERNS]


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

    # Check against safe patterns (hashes, UUIDs, etc.) using pre-compiled patterns
    for compiled_pattern in COMPILED_SAFE_PATTERNS:
        if compiled_pattern.match(value):
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


def get_risk_level(secret_type: str) -> str:
    """Get the risk level for a secret type.

    Args:
        secret_type: Type of secret detected

    Returns:
        Risk level: critical, high, medium, or low
    """
    for level, types in RISK_LEVELS.items():
        if secret_type in types:
            return level
    return "medium"  # Default


def get_remediation(secret_type: str) -> str:
    """Get remediation suggestion for a secret type.

    Args:
        secret_type: Type of secret detected

    Returns:
        Remediation suggestion string
    """
    # Handle entropy-based detections
    if secret_type.startswith("High Entropy String"):
        return REMEDIATION_SUGGESTIONS.get("High Entropy String", "")
    return REMEDIATION_SUGGESTIONS.get(secret_type, "Remove this secret from source code and use environment variables or a secrets manager.")


@dataclass
class SecretsScanResult:
    """Result of scanning text for secrets.

    Attributes:
        secrets_found: List of detected secrets
        redacted_text: Text with secrets replaced by [REDACTED]
        has_secrets: True if any secrets were found
        total_secrets: Count of detected secrets
        by_risk_level: Count of secrets by risk level
        by_type: Count of secrets by type
        file_hash: MD5 hash of scanned content (for caching)
    """
    secrets_found: List[SecretMatch] = field(default_factory=list)
    redacted_text: Optional[str] = None
    has_secrets: bool = False
    total_secrets: int = 0
    by_risk_level: Dict[str, int] = field(default_factory=dict)
    by_type: Dict[str, int] = field(default_factory=dict)
    file_hash: Optional[str] = None


# Global cache for scan results (hash -> result)
_scan_cache: Dict[str, SecretsScanResult] = {}


def _scan_file_worker(file_path: str) -> SecretsScanResult:
    """Worker function for parallel file scanning.

    This function is used by ProcessPoolExecutor to scan files in parallel.
    It creates a new SecretsScanner instance for each file to avoid
    pickle issues with compiled regex patterns.

    Args:
        file_path: Path to file to scan

    Returns:
        SecretsScanResult for the file
    """
    scanner = SecretsScanner(cache_enabled=False)
    return scanner.scan_file(Path(file_path))


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

    REPO-149 Enhancements:
    - Pre-compiled regex patterns for ~3x faster scanning
    - Streaming support for large files (>1MB)
    - Parallel scanning with multiprocessing
    - Hash-based caching for unchanged files
    - Enhanced reporting with risk levels and remediation
    - Custom pattern support via configuration

    Example:
        >>> scanner = SecretsScanner()
        >>> result = scanner.scan_string(
        ...     "AWS_KEY = 'AKIAIOSFODNN7EXAMPLE'",
        ...     context="config.py:10"
        ... )
        >>> if result.has_secrets:
        ...     print(f"Found {result.total_secrets} secrets")
        ...     print(f"Redacted: {result.redacted_text}")

    Example with custom patterns:
        >>> custom_patterns = [
        ...     {"name": "Internal Key", "pattern": r"MYCO_[A-Z0-9]{32}", "risk_level": "critical"}
        ... ]
        >>> scanner = SecretsScanner(custom_patterns=custom_patterns)
    """

    def __init__(
        self,
        entropy_detection: bool = True,
        entropy_threshold: float = 4.0,
        min_entropy_length: int = 20,
        large_file_threshold_mb: float = 1.0,
        parallel_workers: int = 4,
        cache_enabled: bool = True,
        custom_patterns: Optional[List[Dict[str, Any]]] = None,
    ):
        """Initialize secrets scanner with default detect-secrets settings.

        Args:
            entropy_detection: Enable entropy-based detection (REPO-148)
            entropy_threshold: Minimum entropy to flag as secret (default 4.0)
            min_entropy_length: Minimum string length for entropy check (default 20)
            large_file_threshold_mb: Stream files larger than this (default 1.0 MB)
            parallel_workers: Number of parallel workers for batch scanning
            cache_enabled: Enable hash-based caching for unchanged files
            custom_patterns: List of custom pattern dicts with name, pattern, risk_level, remediation
        """
        # Use default settings which includes all standard plugins
        self.settings = default_settings
        self.entropy_detection = entropy_detection
        self.entropy_threshold = entropy_threshold
        self.min_entropy_length = min_entropy_length
        self.large_file_threshold_bytes = int(large_file_threshold_mb * 1024 * 1024)
        self.parallel_workers = parallel_workers
        self.cache_enabled = cache_enabled

        # Compile custom patterns
        self.custom_patterns: List[Tuple[Pattern, str, str, str, str]] = []
        if custom_patterns:
            for cp in custom_patterns:
                try:
                    compiled = re.compile(cp["pattern"], re.IGNORECASE)
                    self.custom_patterns.append((
                        compiled,
                        cp["name"],
                        "CustomPatternDetector",
                        cp.get("risk_level", "high"),
                        cp.get("remediation", ""),
                    ))
                except re.error as e:
                    logger.warning(f"Failed to compile custom pattern '{cp['name']}': {e}")

        logger.debug(f"Initialized SecretsScanner with {len(self.custom_patterns)} custom patterns")

    def _create_secret_match(
        self,
        secret_type: str,
        plugin_name: str,
        line: str,
        line_num: int,
        context: str,
        filename: str,
        start_pos: int = 0,
        end_pos: Optional[int] = None,
        risk_level: Optional[str] = None,
        remediation: Optional[str] = None,
    ) -> SecretMatch:
        """Helper to create a SecretMatch with common parameters.

        Args:
            secret_type: Type of secret detected
            plugin_name: Name of detection plugin
            line: Line containing the secret
            line_num: Line number in file
            context: Context string
            filename: Filename for reporting
            start_pos: Character position where secret starts (default 0)
            end_pos: Character position where secret ends (default: line length)
            risk_level: Override risk level (default: lookup from RISK_LEVELS)
            remediation: Override remediation (default: lookup from REMEDIATION_SUGGESTIONS)

        Returns:
            SecretMatch instance
        """
        match = SecretMatch(
            secret_type=secret_type,
            start_index=start_pos,
            end_index=end_pos if end_pos is not None else len(line),
            context=context,
            filename=filename,
            line_number=line_num,
            plugin_name=plugin_name,
            risk_level=risk_level or get_risk_level(secret_type),
            remediation=remediation or get_remediation(secret_type),
        )
        logger.warning(f"Secret detected: {secret_type} ({match.risk_level}) at {context}")
        return match

    def scan_string(
        self,
        text: str,
        context: str,
        filename: str = "<string>",
        line_offset: int = 1,
        use_cache: bool = True,
    ) -> SecretsScanResult:
        """Scan a string for secrets using pre-compiled patterns.

        REPO-149: Uses pre-compiled regex patterns for ~3x faster scanning.

        Args:
            text: Text to scan for secrets
            context: Context string (e.g., "file.py:42")
            filename: Filename for reporting (default: "<string>")
            line_offset: Starting line number (default: 1)
            use_cache: Whether to use hash-based caching (default: True)

        Returns:
            SecretsScanResult with detected secrets and redacted text
        """
        if not text:
            return SecretsScanResult(
                redacted_text=text,
                has_secrets=False,
                total_secrets=0
            )

        # Check cache if enabled
        content_hash = None
        if use_cache and self.cache_enabled:
            content_hash = hashlib.md5(text.encode()).hexdigest()
            if content_hash in _scan_cache:
                logger.debug(f"Cache hit for {filename}")
                return _scan_cache[content_hash]

        secret_matches = []
        lines = text.split('\n')

        for line_num, line in enumerate(lines, start=line_offset):
            # Use pre-compiled patterns for faster matching
            for compiled_regex, secret_type, plugin_name, risk_level in COMPILED_PATTERNS:
                match_obj = compiled_regex.search(line)
                if match_obj:
                    secret_match = self._create_secret_match(
                        secret_type, plugin_name, line, line_num, context, filename,
                        start_pos=match_obj.start(),
                        end_pos=match_obj.end(),
                        risk_level=risk_level,
                    )
                    secret_matches.append(secret_match)

            # Check custom patterns
            for compiled_regex, name, plugin_name, risk_level, remediation in self.custom_patterns:
                match_obj = compiled_regex.search(line)
                if match_obj:
                    secret_match = self._create_secret_match(
                        name, plugin_name, line, line_num, context, filename,
                        start_pos=match_obj.start(),
                        end_pos=match_obj.end(),
                        risk_level=risk_level,
                        remediation=remediation,
                    )
                    secret_matches.append(secret_match)

            # Entropy-based detection
            if self.entropy_detection:
                # Find quoted strings that might be secrets
                quoted_strings = re.findall(r'["\']([A-Za-z0-9+/=_\-]{20,})["\']', line)
                for candidate in quoted_strings:
                    # Skip if already matched by a specific pattern
                    already_matched = any(
                        m.line_number == line_num and m.secret_type != "High Entropy String"
                        for m in secret_matches
                    )
                    if already_matched:
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
                            line, line_num, context, filename,
                            risk_level="low",
                        )
                        secret_matches.append(match)

        # Redact secrets if found
        redacted_text = text
        if secret_matches:
            redacted_text = self._redact_secrets(text, secret_matches)

        # Build statistics
        by_risk_level: Dict[str, int] = {}
        by_type: Dict[str, int] = {}
        for m in secret_matches:
            by_risk_level[m.risk_level] = by_risk_level.get(m.risk_level, 0) + 1
            # Normalize entropy-based types for counting
            type_key = "High Entropy String" if m.secret_type.startswith("High Entropy") else m.secret_type
            by_type[type_key] = by_type.get(type_key, 0) + 1

        result = SecretsScanResult(
            secrets_found=secret_matches,
            redacted_text=redacted_text,
            has_secrets=len(secret_matches) > 0,
            total_secrets=len(secret_matches),
            by_risk_level=by_risk_level,
            by_type=by_type,
            file_hash=content_hash,
        )

        # Cache result if enabled
        if use_cache and self.cache_enabled and content_hash:
            _scan_cache[content_hash] = result

        return result

    def scan_file(
        self,
        file_path: Path,
        use_streaming: bool = True,
    ) -> SecretsScanResult:
        """Scan a file for secrets, with streaming support for large files.

        REPO-149: Uses streaming for files larger than large_file_threshold_mb
        to avoid memory issues.

        Args:
            file_path: Path to the file to scan
            use_streaming: Use streaming for large files (default: True)

        Returns:
            SecretsScanResult with detected secrets and statistics
        """
        file_path = Path(file_path)
        if not file_path.exists():
            logger.warning(f"File not found: {file_path}")
            return SecretsScanResult()

        file_size = file_path.stat().st_size

        # Use streaming for large files
        if use_streaming and file_size > self.large_file_threshold_bytes:
            return self._scan_file_streaming(file_path)

        # Read entire file for small files
        try:
            content = file_path.read_text(encoding='utf-8', errors='replace')
            return self.scan_string(
                content,
                context=str(file_path),
                filename=str(file_path),
            )
        except Exception as e:
            logger.error(f"Error reading file {file_path}: {e}")
            return SecretsScanResult()

    def _scan_file_streaming(self, file_path: Path) -> SecretsScanResult:
        """Scan a large file using line-by-line streaming.

        REPO-149: Memory-efficient scanning for files >1MB.

        Args:
            file_path: Path to the file

        Returns:
            SecretsScanResult (without redacted_text for large files)
        """
        secret_matches = []
        line_num = 0

        try:
            with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
                for line in f:
                    line_num += 1
                    # Use pre-compiled patterns
                    for compiled_regex, secret_type, plugin_name, risk_level in COMPILED_PATTERNS:
                        match_obj = compiled_regex.search(line)
                        if match_obj:
                            secret_match = self._create_secret_match(
                                secret_type, plugin_name, line.rstrip('\n'),
                                line_num, str(file_path), str(file_path),
                                start_pos=match_obj.start(),
                                end_pos=match_obj.end(),
                                risk_level=risk_level,
                            )
                            secret_matches.append(secret_match)

                    # Check custom patterns
                    for compiled_regex, name, plugin_name, risk_level, remediation in self.custom_patterns:
                        match_obj = compiled_regex.search(line)
                        if match_obj:
                            secret_match = self._create_secret_match(
                                name, plugin_name, line.rstrip('\n'),
                                line_num, str(file_path), str(file_path),
                                start_pos=match_obj.start(),
                                end_pos=match_obj.end(),
                                risk_level=risk_level,
                                remediation=remediation,
                            )
                            secret_matches.append(secret_match)

        except Exception as e:
            logger.error(f"Error streaming file {file_path}: {e}")

        # Build statistics
        by_risk_level: Dict[str, int] = {}
        by_type: Dict[str, int] = {}
        for m in secret_matches:
            by_risk_level[m.risk_level] = by_risk_level.get(m.risk_level, 0) + 1
            type_key = "High Entropy String" if m.secret_type.startswith("High Entropy") else m.secret_type
            by_type[type_key] = by_type.get(type_key, 0) + 1

        return SecretsScanResult(
            secrets_found=secret_matches,
            redacted_text=None,  # Too large to redact in memory
            has_secrets=len(secret_matches) > 0,
            total_secrets=len(secret_matches),
            by_risk_level=by_risk_level,
            by_type=by_type,
        )

    def scan_files_parallel(
        self,
        file_paths: List[Path],
        max_workers: Optional[int] = None,
    ) -> Dict[str, SecretsScanResult]:
        """Scan multiple files in parallel.

        REPO-149: Uses ProcessPoolExecutor for parallel scanning.

        Args:
            file_paths: List of file paths to scan
            max_workers: Number of parallel workers (default: self.parallel_workers)

        Returns:
            Dict mapping file path to SecretsScanResult
        """
        max_workers = max_workers or self.parallel_workers
        results: Dict[str, SecretsScanResult] = {}

        # For small batches, just scan sequentially
        if len(file_paths) <= 2:
            for fp in file_paths:
                results[str(fp)] = self.scan_file(fp)
            return results

        # Use parallel scanning for larger batches
        with ProcessPoolExecutor(max_workers=max_workers) as executor:
            future_to_path = {
                executor.submit(_scan_file_worker, str(fp)): str(fp)
                for fp in file_paths
            }

            for future in as_completed(future_to_path):
                path = future_to_path[future]
                try:
                    results[path] = future.result()
                except Exception as e:
                    logger.error(f"Error scanning {path}: {e}")
                    results[path] = SecretsScanResult()

        return results

    def clear_cache(self) -> int:
        """Clear the scan result cache.

        Returns:
            Number of entries cleared
        """
        global _scan_cache
        count = len(_scan_cache)
        _scan_cache = {}
        logger.info(f"Cleared {count} entries from secrets scan cache")
        return count

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


# =============================================================================
# Redis Cache Integration (REPO-313)
# =============================================================================


async def scan_with_cache(
    content: str,
    context: str,
    filename: str = "<string>",
    scanner: Optional[SecretsScanner] = None,
) -> SecretsScanResult:
    """Scan content for secrets with Redis caching support.

    Uses the ScanCache for distributed caching across workers.
    Falls back to in-memory scanning if cache is unavailable.

    Args:
        content: Text content to scan
        context: Context string for logging (e.g., "module.py:42")
        filename: Filename for reporting
        scanner: Optional SecretsScanner instance (creates one if not provided)

    Returns:
        SecretsScanResult with detected secrets

    Example:
        ```python
        result = await scan_with_cache(
            content=file_content,
            context="src/config.py",
            filename="config.py",
        )
        if result.has_secrets:
            print(f"Found {result.total_secrets} secrets")
        ```
    """
    from repotoire.cache import ScanCache, get_scan_cache

    # Get the scan cache
    try:
        cache = await get_scan_cache()
    except Exception as e:
        logger.warning(f"Could not get scan cache: {e}")
        cache = None

    # Check cache first
    if cache:
        cached = await cache.get_by_content(content)
        if cached:
            # Reconstruct SecretsScanResult from cached data
            # Note: We don't cache the full redacted_text (it's large)
            # We return the cached stats and let caller re-redact if needed
            result = SecretsScanResult(
                secrets_found=[],  # Not cached, but stats are
                redacted_text=None,
                has_secrets=cached.has_secrets,
                total_secrets=cached.total_secrets,
                by_risk_level=cached.by_risk_level,
                by_type=cached.by_type,
                file_hash=cached.file_hash,
            )
            logger.debug(
                "Secrets scan cache hit",
                extra={
                    "context": context,
                    "has_secrets": cached.has_secrets,
                    "total_secrets": cached.total_secrets,
                },
            )
            return result

    # Cache miss - perform scan
    if scanner is None:
        scanner = SecretsScanner(cache_enabled=False)  # Disable in-memory cache

    result = scanner.scan_string(content, context, filename, use_cache=False)

    # Cache the result
    if cache and result.file_hash:
        try:
            await cache.set_from_scan_result(content, result)
        except Exception as e:
            logger.warning(f"Could not cache scan result: {e}")

    return result
