//! Insecure Crypto Detector

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphStore;
use crate::models::{deterministic_finding_id, Finding, Severity};
use anyhow::Result;
use regex::Regex;
use std::path::PathBuf;
use std::sync::OnceLock;

static WEAK_HASH: OnceLock<Regex> = OnceLock::new();
static WEAK_CIPHER: OnceLock<Regex> = OnceLock::new();

fn weak_hash() -> &'static Regex {
    WEAK_HASH.get_or_init(|| Regex::new(r#"(?i)(?:^|[^_\w])(md5|sha1|sha-1)\s*\(|hashlib\.(md5|sha1)|Digest::(MD5|SHA1)|MessageDigest\.getInstance"#).expect("valid regex"))
}

/// Check if a line is merely mentioning a weak hash (in comments, strings, etc.)
/// rather than actually using it. Returns true if the line should be SKIPPED.
fn is_hash_mention_not_usage(line: &str) -> bool {
    // Skip lines containing detector-related patterns (avoid flagging our own source)
    if line.contains("is_hash_mention")
        || line.contains("is_cipher_mention")
        || line.contains("weak_hash")
        || line.contains("weak_cipher")
        || line.contains("usage_patterns")
        || line.contains("WEAK_")
    {
        return true;
    }

    let lower = line.to_lowercase();

    // Skip comments
    let trimmed = line.trim();
    if trimmed.starts_with("//")
        || trimmed.starts_with("#")
        || trimmed.starts_with("*")
        || trimmed.starts_with("/*")
    {
        return true;
    }

    // Skip Python hashlib calls with usedforsecurity=False
    // This is Python's official way to signal non-cryptographic usage
    if lower.contains("usedforsecurity=false") || lower.contains("usedforsecurity = false") {
        return true;
    }

    // Skip database function implementations and non-security contexts
    if lower.contains("_sqlite_") || lower.contains("checksum") || lower.contains("etag") || lower.contains("cache_key") {
        return true;
    }

    // Skip non-cryptographic hashing contexts (cache keys, template hashing)
    if (lower.contains("generate_hash") || lower.contains("hexdigest"))
        && (lower.contains("cache") || lower.contains("template") || lower.contains("loader") || lower.contains("join") || lower.contains("values"))
    {
        return true;
    }

    // Skip regex pattern definitions
    if line.contains("Regex::new")
        || line.contains("regex::Regex")
        || line.contains("r\"")
        || line.contains("r#\"")
    {
        return true;
    }

    // Skip error messages, warnings, documentation
    if lower.contains("deprecated")
        || lower.contains("insecure")
        || lower.contains("weak")
        || lower.contains("broken")
        || lower.contains("unsafe")
        || lower.contains("vulnerable")
        || lower.contains("warning")
        || lower.contains("error")
    {
        return true;
    }

    // Skip string comparisons and config checks
    if line.contains("==")
        || line.contains("!=")
        || line.contains("match")
        || line.contains("case ")
    {
        return true;
    }

    // Skip logging/print statements
    if lower.contains("print")
        || lower.contains("log")
        || lower.contains("console.")
        || lower.contains("logger")
    {
        return true;
    }

    // Skip SCREAMING_CASE constants
    if line.contains("const ") || line.contains("static ") {
        let parts: Vec<&str> = line.split('=').collect();
        if parts.len() >= 2 {
            let before_eq = parts[0];
            if before_eq.split_whitespace().any(|word| {
                word.chars()
                    .all(|c| c.is_uppercase() || c == '_' || c == ':')
                    && word.contains('_')
                    && word.len() > 2
            }) {
                return true;
            }
        }
    }

    false
}

fn weak_cipher() -> &'static Regex {
    // Use \b on both sides to prevent matching 'nodes', 'description', etc.
    WEAK_CIPHER
        .get_or_init(|| Regex::new(r"(?i)\b(DES|RC4|RC2|Blowfish|ECB)\b").expect("valid regex"))
}

/// Check if a line is merely mentioning a weak cipher (in definitions, error messages, etc.)
/// rather than actually using it. Returns true if the line should be SKIPPED.
fn is_cipher_mention_not_usage(line: &str) -> bool {
    // Skip lines containing detector-related patterns (avoid flagging our own source)
    if line.contains("is_hash_mention")
        || line.contains("is_cipher_mention")
        || line.contains("weak_hash")
        || line.contains("weak_cipher")
        || line.contains("usage_patterns")
        || line.contains("WEAK_")
        || line.contains("des.new")
        || line.contains("arc4.new")
        || line.contains("blowfish.new")
        || line.contains("cipher.newecb")
    {
        return true;
    }

    // Skip string literal pattern checks (e.g. line.contains("\"ECB\""))
    // These are checking FOR weak ciphers, not USING them
    let lower = line.to_lowercase();
    if line.contains(".contains(")
        && (lower.contains("\"des")
            || lower.contains("'des")
            || lower.contains("\"rc4")
            || lower.contains("'rc4")
            || lower.contains("\"rc2")
            || lower.contains("'rc2")
            || lower.contains("\"ecb")
            || lower.contains("'ecb")
            || lower.contains("\"blowfish")
            || lower.contains("'blowfish"))
    {
        return true;
    }

    // Skip regex pattern definitions (like this detector's own source!)
    if line.contains("Regex::new") || line.contains("regex::Regex") {
        return true;
    }

    // Skip raw string literals (Rust r"..." or r#"..."#)
    if line.contains("r\"") || line.contains("r#\"") || line.contains("r##\"") {
        return true;
    }

    // Skip string assignments to SCREAMING_CASE constants
    // Pattern: const/static FOO_BAR = "..." or let FOO_BAR = "..."
    if line.contains("const ") || line.contains("static ") || line.contains("let ") {
        // Check if there's a SCREAMING_CASE identifier before an =
        let parts: Vec<&str> = line.split('=').collect();
        if parts.len() >= 2 {
            let before_eq = parts[0];
            // Look for SCREAMING_CASE pattern (uppercase + underscores)
            if before_eq.split_whitespace().any(|word| {
                word.chars()
                    .all(|c| c.is_uppercase() || c == '_' || c == ':')
                    && word.contains('_')
                    && word.len() > 2
            }) {
                return true;
            }
        }
    }

    // Skip lines that are rejecting/warning about weak ciphers
    let rejection_patterns = [
        "reject",
        "deny",
        "error",
        "warn",
        "throw",
        "panic",
        "not allowed",
        "not supported",
        "forbidden",
        "invalid",
        "disallow",
        "prohibit",
        "refuse",
        "fail",
    ];
    for pattern in rejection_patterns {
        if lower.contains(pattern) {
            return true;
        }
    }

    // Skip exclusion checks: != "DES" or !== "DES"
    if line.contains("!=")
        && (line.contains("\"DES\"")
            || line.contains("'DES'")
            || line.contains("\"RC4\"")
            || line.contains("'RC4'")
            || line.contains("\"RC2\"")
            || line.contains("'RC2'")
            || line.contains("\"Blowfish\"")
            || line.contains("'Blowfish'")
            || line.contains("\"ECB\"")
            || line.contains("'ECB'"))
    {
        return true;
    }

    // Skip test assertions about weak ciphers
    if lower.contains("assert") || lower.contains("expect") || lower.contains("should") {
        return true;
    }

    // Skip documentation strings and string literals that describe ciphers
    if lower.contains("deprecated")
        || lower.contains("insecure")
        || lower.contains("vulnerable")
        || lower.contains("weak")
        || lower.contains("broken")
        || lower.contains("unsafe")
    {
        return true;
    }

    // Now check for ACTUAL usage patterns (positive signals)
    let usage_patterns = [
        // Java
        "cipher.getinstance",
        "secretkeyspec",
        "keygenerator.getinstance",
        // Node.js / JavaScript
        "createcipher",
        "createcipheriv",
        "createdecipheriv",
        "crypto.cipher",
        // Python
        "cipher.new",
        "des.new",
        "arc4.new",
        "blowfish.new",
        // Go
        "cipher.newecb",
        "des.newcipher",
        // Ruby
        "openssl::cipher",
        // PHP
        "openssl_encrypt",
        "mcrypt_encrypt",
        // .NET / C#
        "descryptoserviceprovider",
        "rc2cryptoserviceprovider",
        "rijndaelmanaged", // flagged when combined with electronic codebook mode
    ];

    for pattern in usage_patterns {
        if lower.contains(pattern) {
            return false; // This IS a usage, don't skip
        }
    }

    // If we have a cipher name in quotes followed by common crypto function patterns,
    // it's likely a usage
    if (line.contains("\"DES")
        || line.contains("'DES")
        || line.contains("\"RC4")
        || line.contains("'RC4")
        || line.contains("\"ECB")
        || line.contains("'ECB")
        || line.contains("\"des")
        || line.contains("'des")
        || line.contains("\"rc4")
        || line.contains("'rc4")
        || line.contains("\"ecb")
        || line.contains("'ecb"))
        && (line.contains("(")
            || line.contains("getInstance")
            || line.contains("cipher")
            || line.contains("Cipher"))
    {
        return false; // Looks like actual usage
    }

    // Default: if none of the usage patterns matched, skip it
    // This catches incidental mentions
    true
}

pub struct InsecureCryptoDetector {
    repository_path: PathBuf,
    max_findings: usize,
}

impl InsecureCryptoDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self {
            repository_path: repository_path.into(),
            max_findings: 50,
        }
    }

    /// Convert absolute path to relative path for consistent output
    fn relative_path(&self, path: &std::path::Path) -> PathBuf {
        path.strip_prefix(&self.repository_path)
            .unwrap_or(path)
            .to_path_buf()
    }
}

impl Detector for InsecureCryptoDetector {
    fn name(&self) -> &'static str {
        "insecure-crypto"
    }
    fn description(&self) -> &'static str {
        "Detects weak cryptographic algorithms"
    }

    fn detect(&self, _graph: &dyn crate::graph::GraphQuery, files: &dyn crate::detectors::file_provider::FileProvider) -> Result<Vec<Finding>> {
        let mut findings = vec![];

        for path in files.files_with_extensions(&["py", "js", "ts", "java", "go", "rs", "rb", "php", "cs"]) {
            if findings.len() >= self.max_findings {
                break;
            }

            // Skip test files - hash usage in tests is for test fixtures
            let path_str = path.to_string_lossy().to_lowercase();
            if crate::detectors::base::is_test_path(&path_str) {
                continue;
            }

            // Skip translation/localization files (French "des" = "of the", not DES cipher)
            if path_str.contains("/lang/")
                || path_str.contains("/locale")
                || path_str.contains("/i18n/")
                || path_str.contains("/translations/")
                || path_str.contains("_lang")
                || path_str.contains(".lang.")
            {
                continue;
            }

            // Skip scripts/build/release tooling (checksums for file integrity, not security)
            if path_str.contains("scripts/") || path_str.contains("/script/") {
                continue;
            }

            if let Some(content) = files.masked_content(path) {
                let lines: Vec<&str> = content.lines().collect();
                let mut in_non_crypto_func = false;
                let mut func_indent: usize = 0;
                for (i, line) in lines.iter().enumerate() {
                    let prev_line = if i > 0 { Some(lines[i - 1]) } else { None };
                    if crate::detectors::is_line_suppressed(line, prev_line) {
                        continue;
                    }

                    let trimmed = line.trim();
                    let indent = line.len() - line.trim_start().len();

                    // Track whether we're inside a non-cryptographic function
                    // e.g., _sqlite_md5, make_cache_key, compute_checksum, etag
                    if trimmed.starts_with("def ") || trimmed.starts_with("fn ") {
                        let lower_trimmed = trimmed.to_lowercase();
                        if lower_trimmed.contains("_sqlite_") || lower_trimmed.contains("checksum") || lower_trimmed.contains("etag") || lower_trimmed.contains("cache_key") || lower_trimmed.contains("generate_hash") {
                            in_non_crypto_func = true;
                            func_indent = indent;
                        } else {
                            in_non_crypto_func = false;
                        }
                    } else if in_non_crypto_func && !trimmed.is_empty() && indent <= func_indent {
                        // Left the function body (dedented to same or less than def line)
                        in_non_crypto_func = false;
                    }

                    // Skip lines inside non-cryptographic functions
                    if in_non_crypto_func {
                        continue;
                    }
                    // Skip comments
                    if trimmed.starts_with("//")
                        || trimmed.starts_with("#")
                        || trimmed.starts_with("*")
                    {
                        continue;
                    }

                    // Skip TypeScript type definitions (interface, type alias, generic constraints)
                    // These are compile-time only and don't represent actual crypto usage
                    if trimmed.starts_with("interface ")
                        || trimmed.starts_with("type ")
                        || trimmed.starts_with("export interface ")
                        || trimmed.starts_with("export type ")
                        || trimmed.contains(": ") && !trimmed.contains("(")
                    {
                        // type annotation, not function call
                        continue;
                    }

                    // Skip enum declarations
                    if trimmed.starts_with("enum ") || trimmed.starts_with("export enum ") {
                        continue;
                    }

                    // Skip class/function definitions where MD5/SHA1 is just a name
                    // e.g., "class MD5(Transform):" or "def _sqlite_md5(text):"
                    if trimmed.starts_with("class ") || trimmed.starts_with("def ") || trimmed.starts_with("fn ") {
                        continue;
                    }

                    if weak_hash().is_match(line) && !is_hash_mention_not_usage(line) {
                        findings.push(Finding {
                            id: String::new(),
                            detector: "InsecureCryptoDetector".to_string(),
                            severity: Severity::High,
                            title: "Weak hash algorithm (MD5/SHA1)".to_string(),
                            description: "MD5 and SHA1 are cryptographically broken.".to_string(),
                            affected_files: vec![self.relative_path(path)],
                            line_start: Some((i + 1) as u32),
                            line_end: Some((i + 1) as u32),
                            suggested_fix: Some(
                                "Use SHA-256 or better (SHA-3, BLAKE3).".to_string(),
                            ),
                            estimated_effort: Some("15 minutes".to_string()),
                            category: Some("security".to_string()),
                            cwe_id: Some("CWE-328".to_string()),
                            why_it_matters: Some(
                                "Weak hashes can be cracked or collided.".to_string(),
                            ),
                            ..Default::default()
                        });
                    }
                    // Check for weak cipher usage, but skip mere mentions
                    if weak_cipher().is_match(line) && !is_cipher_mention_not_usage(line) {
                        findings.push(Finding {
                            id: String::new(),
                            detector: "InsecureCryptoDetector".to_string(),
                            severity: Severity::High,
                            title: "Weak cipher algorithm".to_string(),
                            description: "DES, RC4, and ECB mode are insecure.".to_string(),
                            affected_files: vec![self.relative_path(path)],
                            line_start: Some((i + 1) as u32),
                            line_end: Some((i + 1) as u32),
                            suggested_fix: Some("Use AES-GCM or ChaCha20-Poly1305.".to_string()),
                            estimated_effort: Some("30 minutes".to_string()),
                            category: Some("security".to_string()),
                            cwe_id: Some("CWE-327".to_string()),
                            why_it_matters: Some("Weak ciphers can be broken.".to_string()),
                            ..Default::default()
                        });
                    }
                }
            }
        }
        Ok(findings)
    }
}

#[test]
fn test_self_flagging_protection() {
    // These are lines from our own source code that should NOT be flagged
    let lines = vec![
        r#"        line.contains("\"RC4\"") || line.contains("'RC4'") ||"#,
        r#"       line.contains("blowfish.new") || line.contains("cipher.newecb") {"#,
        r#"       lower.contains("\"ecb") || lower.contains("'ecb") ||"#,
    ];

    for line in lines {
        if weak_cipher().is_match(line) {
            assert!(
                is_cipher_mention_not_usage(line),
                "Should skip detector source line: {}",
                line
            );
        }
    }
}

#[test]
fn test_self_flagging_line_106() {
    // Line 106 of this file
    let line = r#"lower.contains("\"ecb") || lower.contains("'ecb") ||"#;
    println!("Line: {}", line);
    println!("Contains .contains(: {}", line.contains(".contains("));
    let lower = line.to_lowercase();
    println!("Lower contains \"ecb: {}", lower.contains("\"ecb"));

    assert!(
        is_cipher_mention_not_usage(line),
        "Line 106 should be skipped"
    );
}

#[test]
fn test_self_flagging_line_215() {
    // Line 215 of this file
    let line = r#"        line.contains("\"rc4") || line.contains("'rc4") ||"#;
    println!("Line: {}", line);
    println!("Contains .contains(: {}", line.contains(".contains("));
    let lower = line.to_lowercase();
    println!("Lower contains \"rc4: {}", lower.contains("\"rc4"));

    // Does weak_cipher match?
    println!("Weak cipher matches: {}", weak_cipher().is_match(line));

    assert!(
        is_cipher_mention_not_usage(line),
        "Line 215 should be skipped"
    );
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::graph::GraphStore;

    #[test]
    fn test_detects_md5_usage() {
        let store = GraphStore::in_memory();
        let detector = InsecureCryptoDetector::new("/mock/repo");
        let files = crate::detectors::file_provider::MockFileProvider::new(vec![
            ("crypto_util.py", "import hashlib\n\ndef compute_hash(data):\n    return hashlib.md5(data).hexdigest()\n"),
        ]);
        let findings = detector.detect(&store, &files).expect("detection should succeed");
        assert!(!findings.is_empty(), "Should detect hashlib.md5 usage");
        assert!(
            findings.iter().any(|f| f.title.contains("MD5") || f.title.contains("SHA1")),
            "Finding should mention weak hash. Titles: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }

    #[test]
    fn test_no_finding_for_sha256() {
        let store = GraphStore::in_memory();
        let detector = InsecureCryptoDetector::new("/mock/repo");
        let files = crate::detectors::file_provider::MockFileProvider::new(vec![
            ("crypto_util.py", "import hashlib\n\ndef compute_hash(data):\n    return hashlib.sha256(data).hexdigest()\n"),
        ]);
        let findings = detector.detect(&store, &files).expect("detection should succeed");
        assert!(findings.is_empty(), "Should not detect sha256 as insecure. Found: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>());
    }

    #[test]
    fn test_no_finding_for_usedforsecurity_false() {
        let store = GraphStore::in_memory();
        let detector = InsecureCryptoDetector::new("/mock/repo");
        let files = crate::detectors::file_provider::MockFileProvider::new(vec![
            ("cache.py", "import hashlib\n\ndef make_cache_key(data):\n    return hashlib.md5(data.encode(), usedforsecurity=False).hexdigest()\n"),
        ]);
        let findings = detector.detect(&store, &files).expect("detection should succeed");
        assert!(findings.is_empty(), "Should not flag md5 with usedforsecurity=False. Found: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>());
    }

    #[test]
    fn test_no_finding_for_class_definition() {
        let store = GraphStore::in_memory();
        let detector = InsecureCryptoDetector::new("/mock/repo");
        let files = crate::detectors::file_provider::MockFileProvider::new(vec![
            ("text.py", "from django.db.models import Transform\n\nclass MD5(Transform):\n    function = 'MD5'\n    lookup_name = 'md5'\n"),
        ]);
        let findings = detector.detect(&store, &files).expect("detection should succeed");
        assert!(findings.is_empty(), "Should not flag class MD5() definition. Found: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>());
    }

    #[test]
    fn test_no_finding_for_sqlite_shim() {
        let store = GraphStore::in_memory();
        let detector = InsecureCryptoDetector::new("/mock/repo");
        let files = crate::detectors::file_provider::MockFileProvider::new(vec![
            ("_functions.py", "import hashlib\n\ndef _sqlite_md5(text):\n    return hashlib.md5(text.encode()).hexdigest()\n"),
        ]);
        let findings = detector.detect(&store, &files).expect("detection should succeed");
        assert!(findings.is_empty(), "Should not flag _sqlite_md5 shim function. Found: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>());
    }

    #[test]
    fn test_still_detects_real_md5_usage() {
        let store = GraphStore::in_memory();
        let detector = InsecureCryptoDetector::new("/mock/repo");
        let files = crate::detectors::file_provider::MockFileProvider::new(vec![
            ("auth.py", "import hashlib\n\ndef hash_password(password, salt):\n    return hashlib.md5((salt + password).encode()).hexdigest()\n"),
        ]);
        let findings = detector.detect(&store, &files).expect("detection should succeed");
        assert!(!findings.is_empty(), "Should still detect real md5 password hashing");
    }

    #[test]
    fn test_no_finding_for_cache_key_hash() {
        let store = GraphStore::in_memory();
        let detector = InsecureCryptoDetector::new("/mock/repo");
        let mock_files = crate::detectors::file_provider::MockFileProvider::new(vec![
            ("template/loaders/cached.py", "def generate_hash(self, values):\n    return hashlib.sha1(\"|\".join(values).encode()).hexdigest()\n"),
        ]);
        let findings = detector.detect(&store, &mock_files).expect("detection should succeed");
        assert!(
            findings.is_empty(),
            "Should not flag SHA1 used for cache key generation. Found: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }

    #[test]
    fn test_no_finding_for_release_checksums() {
        let store = GraphStore::in_memory();
        let detector = InsecureCryptoDetector::new("/mock/repo");
        let mock_files = crate::detectors::file_provider::MockFileProvider::new(vec![
            ("scripts/do_release.py", "checksums = [\n    (\"md5\", hashlib.md5),\n    (\"sha1\", hashlib.sha1),\n    (\"sha256\", hashlib.sha256),\n]\n"),
        ]);
        let findings = detector.detect(&store, &mock_files).expect("detection should succeed");
        assert!(
            findings.is_empty(),
            "Should not flag hashes in release/build scripts. Found: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }
}
