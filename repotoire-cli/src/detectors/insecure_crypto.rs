//! Insecure Crypto Detector

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphStore;
use crate::models::{Finding, Severity};
use anyhow::Result;
use regex::Regex;
use std::path::PathBuf;
use std::sync::OnceLock;
use uuid::Uuid;

static WEAK_HASH: OnceLock<Regex> = OnceLock::new();
static WEAK_CIPHER: OnceLock<Regex> = OnceLock::new();

fn weak_hash() -> &'static Regex {
    WEAK_HASH.get_or_init(|| Regex::new(r#"(?i)(md5|sha1|sha-1)\s*\(|hashlib\.(md5|sha1)|Digest::(MD5|SHA1)|MessageDigest\.getInstance"#).unwrap())
}

fn weak_cipher() -> &'static Regex {
    // Use \b on both sides to prevent matching 'nodes', 'description', etc.
    WEAK_CIPHER.get_or_init(|| Regex::new(r"(?i)\b(DES|RC4|RC2|Blowfish|ECB)\b").unwrap())
}

pub struct InsecureCryptoDetector {
    repository_path: PathBuf,
    max_findings: usize,
}

impl InsecureCryptoDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self { repository_path: repository_path.into(), max_findings: 50 }
    }
    
    /// Convert absolute path to relative path for consistent output
    fn relative_path(&self, path: &std::path::Path) -> PathBuf {
        path.strip_prefix(&self.repository_path)
            .unwrap_or(path)
            .to_path_buf()
    }
}

impl Detector for InsecureCryptoDetector {
    fn name(&self) -> &'static str { "insecure-crypto" }
    fn description(&self) -> &'static str { "Detects weak cryptographic algorithms" }

    fn detect(&self, _graph: &GraphStore) -> Result<Vec<Finding>> {
        let mut findings = vec![];
        let walker = ignore::WalkBuilder::new(&self.repository_path).hidden(false).git_ignore(true).build();

        for entry in walker.filter_map(|e| e.ok()) {
            if findings.len() >= self.max_findings { break; }
            let path = entry.path();
            if !path.is_file() { continue; }
            
            let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");
            if !matches!(ext, "py"|"js"|"ts"|"java"|"go"|"rs"|"rb"|"php"|"cs") { continue; }

            if let Some(content) = crate::cache::global_cache().get_content(path) {
                for (i, line) in content.lines().enumerate() {
                    let trimmed = line.trim();
                    // Skip comments
                    if trimmed.starts_with("//") || trimmed.starts_with("#") || trimmed.starts_with("*") { continue; }
                    
                    // Skip TypeScript type definitions (interface, type alias, generic constraints)
                    // These are compile-time only and don't represent actual crypto usage
                    if trimmed.starts_with("interface ") || trimmed.starts_with("type ") ||
                       trimmed.starts_with("export interface ") || trimmed.starts_with("export type ") ||
                       trimmed.contains(": ") && !trimmed.contains("(") { // type annotation, not function call
                        continue;
                    }
                    
                    // Skip enum declarations
                    if trimmed.starts_with("enum ") || trimmed.starts_with("export enum ") {
                        continue;
                    }

                    if weak_hash().is_match(line) {
                        findings.push(Finding {
                            id: Uuid::new_v4().to_string(),
                            detector: "InsecureCryptoDetector".to_string(),
                            severity: Severity::High,
                            title: "Weak hash algorithm (MD5/SHA1)".to_string(),
                            description: "MD5 and SHA1 are cryptographically broken.".to_string(),
                            affected_files: vec![self.relative_path(path)],
                            line_start: Some((i + 1) as u32),
                            line_end: Some((i + 1) as u32),
                            suggested_fix: Some("Use SHA-256 or better (SHA-3, BLAKE3).".to_string()),
                            estimated_effort: Some("15 minutes".to_string()),
                            category: Some("security".to_string()),
                            cwe_id: Some("CWE-328".to_string()),
                            why_it_matters: Some("Weak hashes can be cracked or collided.".to_string()),
                        });
                    }
                    if weak_cipher().is_match(line) {
                        findings.push(Finding {
                            id: Uuid::new_v4().to_string(),
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
                        });
                    }
                }
            }
        }
        Ok(findings)
    }
}
