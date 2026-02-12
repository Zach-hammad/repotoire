//! JWT Weak Algorithm Detector
//!
//! Graph-enhanced detection of JWT security issues:
//! - Detect algorithm 'none' attacks
//! - Warn about symmetric algorithms (HS256) when asymmetric needed
//! - Check for algorithm confusion vulnerabilities
//! - Use graph to trace JWT handling through auth flows

use crate::detectors::base::{Detector, DetectorConfig};
use uuid::Uuid;
use crate::graph::GraphStore;
use crate::models::{deterministic_finding_id, Finding, Severity};
use anyhow::Result;
use regex::Regex;
use std::path::PathBuf;
use std::sync::OnceLock;
use tracing::info;

static NONE_ALG: OnceLock<Regex> = OnceLock::new();
static HS256_ALG: OnceLock<Regex> = OnceLock::new();
static JWT_VERIFY: OnceLock<Regex> = OnceLock::new();
static ALG_PARAM: OnceLock<Regex> = OnceLock::new();

fn none_alg() -> &'static Regex {
    NONE_ALG.get_or_init(|| {
        Regex::new(r#"(?i)(algorithm\s*[=:]\s*["']?none["']?|alg["']?\s*:\s*["']?none)"#).unwrap()
    })
}

fn hs256_alg() -> &'static Regex {
    HS256_ALG.get_or_init(|| {
        Regex::new(r#"(?i)(algorithm\s*[=:]\s*["']?HS256["']?|alg["']?\s*:\s*["']?HS256)"#).unwrap()
    })
}

fn jwt_verify() -> &'static Regex {
    JWT_VERIFY.get_or_init(|| {
        Regex::new(r"(?i)(jwt\.(decode|verify)|verify_jwt|verifyToken|JWTVerifier)").unwrap()
    })
}

fn alg_param() -> &'static Regex {
    ALG_PARAM.get_or_init(|| {
        Regex::new(r"(?i)(algorithms?\s*[=:]\s*\[|verify\s*=\s*False|options.*verify)").unwrap()
    })
}

pub struct JwtWeakDetector {
    repository_path: PathBuf,
    max_findings: usize,
}

impl JwtWeakDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self { repository_path: repository_path.into(), max_findings: 50 }
    }

    /// Analyze JWT vulnerability type
    fn analyze_vulnerability(line: &str, context: &str) -> JwtVulnerability {
        let lower = line.to_lowercase();
        let ctx_lower = context.to_lowercase();
        
        // Check for 'none' algorithm (CVE-2015-2951)
        if lower.contains("none") && (lower.contains("algorithm") || lower.contains("alg")) {
            return JwtVulnerability::NoneAlgorithm;
        }
        
        // Check for disabled verification
        if lower.contains("verify") && (lower.contains("false") || lower.contains("skip")) {
            return JwtVulnerability::VerificationDisabled;
        }
        
        // Check for algorithm confusion (accepting header alg)
        if ctx_lower.contains("header") && ctx_lower.contains("alg") {
            return JwtVulnerability::AlgorithmConfusion;
        }
        
        // Check for HS256 with public key (JWT confusion attack)
        if lower.contains("hs256") {
            if ctx_lower.contains("public") || ctx_lower.contains("rsa") {
                return JwtVulnerability::KeyConfusion;
            }
            return JwtVulnerability::WeakSymmetric;
        }
        
        JwtVulnerability::Other
    }

    /// Find containing function
    fn find_containing_function(graph: &GraphStore, file_path: &str, line: u32) -> Option<(String, usize)> {
        graph.get_functions()
            .into_iter()
            .find(|f| f.file_path == file_path && f.line_start <= line && f.line_end >= line)
            .map(|f| {
                let callers = graph.get_callers(&f.qualified_name).len();
                (f.name, callers)
            })
    }

    /// Check if function is in auth flow
    fn is_auth_flow(func_name: &str, file_path: &str) -> bool {
        let name_lower = func_name.to_lowercase();
        let path_lower = file_path.to_lowercase();
        
        name_lower.contains("auth") || name_lower.contains("login") ||
        name_lower.contains("verify") || name_lower.contains("token") ||
        name_lower.contains("session") || name_lower.contains("middleware") ||
        path_lower.contains("auth") || path_lower.contains("security") ||
        path_lower.contains("middleware")
    }
}

#[derive(Debug, Clone)]
enum JwtVulnerability {
    NoneAlgorithm,       // Algorithm 'none' allows unsigned tokens
    VerificationDisabled, // Verification explicitly disabled
    AlgorithmConfusion,  // Accepting algorithm from header
    KeyConfusion,        // HS256 with RSA public key
    WeakSymmetric,       // HS256 when asymmetric recommended
    Other,
}

impl JwtVulnerability {
    fn severity(&self) -> Severity {
        match self {
            JwtVulnerability::NoneAlgorithm => Severity::Critical,
            JwtVulnerability::VerificationDisabled => Severity::Critical,
            JwtVulnerability::AlgorithmConfusion => Severity::Critical,
            JwtVulnerability::KeyConfusion => Severity::Critical,
            JwtVulnerability::WeakSymmetric => Severity::Medium,
            JwtVulnerability::Other => Severity::Low,
        }
    }

    fn title(&self) -> &'static str {
        match self {
            JwtVulnerability::NoneAlgorithm => "JWT algorithm 'none' allows unsigned tokens",
            JwtVulnerability::VerificationDisabled => "JWT verification is disabled",
            JwtVulnerability::AlgorithmConfusion => "JWT algorithm confusion vulnerability",
            JwtVulnerability::KeyConfusion => "JWT key confusion (HS256 with RSA key)",
            JwtVulnerability::WeakSymmetric => "JWT using symmetric algorithm (HS256)",
            JwtVulnerability::Other => "Potential JWT security issue",
        }
    }

    fn description(&self) -> &'static str {
        match self {
            JwtVulnerability::NoneAlgorithm => 
                "Using algorithm 'none' means tokens aren't signed. \
                 Any attacker can forge valid tokens.",
            JwtVulnerability::VerificationDisabled =>
                "Signature verification is disabled. \
                 Tokens are accepted without validation.",
            JwtVulnerability::AlgorithmConfusion =>
                "The algorithm is read from the token header instead of being enforced. \
                 Attackers can switch algorithms to bypass verification.",
            JwtVulnerability::KeyConfusion =>
                "Using HS256 (symmetric) with an RSA public key allows attackers \
                 to sign tokens with the public key.",
            JwtVulnerability::WeakSymmetric =>
                "HS256 uses a shared secret. If the secret is weak or leaked, \
                 attackers can forge tokens. Consider RS256/ES256.",
            JwtVulnerability::Other =>
                "Potential JWT security concern detected.",
        }
    }

    fn fix(&self) -> &'static str {
        match self {
            JwtVulnerability::NoneAlgorithm =>
                "Never allow 'none' algorithm in production:\n\n\
                 ```python\n\
                 # Python (PyJWT)\n\
                 jwt.decode(token, key, algorithms=['RS256'])  # Explicit whitelist\n\
                 ```\n\n\
                 ```javascript\n\
                 // Node.js\n\
                 jwt.verify(token, publicKey, { algorithms: ['RS256'] });\n\
                 ```",
            JwtVulnerability::VerificationDisabled =>
                "Always verify JWT signatures:\n\n\
                 ```python\n\
                 # Never do this:\n\
                 # jwt.decode(token, options={'verify_signature': False})\n\
                 \n\
                 # Always verify:\n\
                 jwt.decode(token, key, algorithms=['RS256'])\n\
                 ```",
            JwtVulnerability::AlgorithmConfusion =>
                "Always specify allowed algorithms explicitly:\n\n\
                 ```python\n\
                 # Don't trust the token's 'alg' header\n\
                 jwt.decode(token, key, algorithms=['RS256'])  # Whitelist\n\
                 ```",
            JwtVulnerability::KeyConfusion =>
                "Use asymmetric algorithms (RS256/ES256) with proper key pairs:\n\n\
                 ```python\n\
                 # Sign with private key\n\
                 jwt.encode(payload, private_key, algorithm='RS256')\n\
                 \n\
                 # Verify with public key\n\
                 jwt.decode(token, public_key, algorithms=['RS256'])\n\
                 ```",
            JwtVulnerability::WeakSymmetric =>
                "Consider using asymmetric algorithms:\n\n\
                 ```python\n\
                 # RS256 (RSA) or ES256 (ECDSA) recommended\n\
                 jwt.encode(payload, private_key, algorithm='RS256')\n\
                 \n\
                 # If using HS256, ensure secret is:\n\
                 # - At least 256 bits (32 bytes)\n\
                 # - Cryptographically random\n\
                 # - Never hardcoded\n\
                 ```",
            JwtVulnerability::Other =>
                "Review JWT implementation for security best practices.",
        }
    }
}

impl Detector for JwtWeakDetector {
    fn name(&self) -> &'static str { "jwt-weak" }
    fn description(&self) -> &'static str { "Detects weak JWT algorithms and configurations" }

    fn detect(&self, graph: &GraphStore) -> Result<Vec<Finding>> {
        let mut findings = vec![];
        let walker = ignore::WalkBuilder::new(&self.repository_path)
            .hidden(false)
            .git_ignore(true)
            .build();

        for entry in walker.filter_map(|e| e.ok()) {
            if findings.len() >= self.max_findings { break; }
            let path = entry.path();
            if !path.is_file() { continue; }
            
            let path_str = path.to_string_lossy().to_string();
            
            // Skip test files
            if path_str.contains("test") { continue; }
            
            let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");
            if !matches!(ext, "py"|"js"|"ts"|"java"|"go"|"rb"|"php") { continue; }

            if let Some(content) = crate::cache::global_cache().get_content(path) {
                let lines: Vec<&str> = content.lines().collect();
                
                for (i, line) in lines.iter().enumerate() {
                    // Skip comments
                    let trimmed = line.trim();
                    if trimmed.starts_with("//") || trimmed.starts_with("#") { continue; }
                    
                    // Check for JWT-related patterns
                    let has_none = none_alg().is_match(line);
                    let has_hs256 = hs256_alg().is_match(line);
                    let has_verify_issue = alg_param().is_match(line) && 
                        (line.to_lowercase().contains("false") || line.contains("none"));
                    
                    if !has_none && !has_hs256 && !has_verify_issue { continue; }
                    
                    // Get surrounding context
                    let start = i.saturating_sub(5);
                    let end = (i + 5).min(lines.len());
                    let context = lines[start..end].join(" ");
                    
                    let vuln = Self::analyze_vulnerability(line, &context);
                    if matches!(vuln, JwtVulnerability::Other) { continue; }
                    
                    let containing_func = Self::find_containing_function(graph, &path_str, (i + 1) as u32);
                    let is_auth = containing_func.as_ref()
                        .map(|(name, _)| Self::is_auth_flow(name, &path_str))
                        .unwrap_or(false);
                    
                    // Build notes
                    let mut notes = Vec::new();
                    if let Some((func_name, callers)) = &containing_func {
                        notes.push(format!("📦 In function: `{}` ({} callers)", func_name, callers));
                    }
                    if is_auth {
                        notes.push("🔐 Part of authentication flow".to_string());
                    }
                    
                    let context_notes = if notes.is_empty() {
                        String::new()
                    } else {
                        format!("\n\n**Context:**\n{}", notes.join("\n"))
                    };
                    
                    findings.push(Finding {
                        id: Uuid::new_v4().to_string(),
                        detector: "JwtWeakDetector".to_string(),
                        severity: vuln.severity(),
                        title: vuln.title().to_string(),
                        description: format!("{}{}", vuln.description(), context_notes),
                        affected_files: vec![path.to_path_buf()],
                        line_start: Some((i + 1) as u32),
                        line_end: Some((i + 1) as u32),
                        suggested_fix: Some(vuln.fix().to_string()),
                        estimated_effort: Some("30 minutes".to_string()),
                        category: Some("security".to_string()),
                        cwe_id: Some("CWE-327".to_string()),
                        why_it_matters: Some(
                            "JWT vulnerabilities can allow attackers to forge authentication tokens, \
                             impersonate users, escalate privileges, or bypass authorization entirely.".to_string()
                        ),
                        ..Default::default()
                    });
                }
            }
        }
        
        info!("JwtWeakDetector found {} findings (graph-aware)", findings.len());
        Ok(findings)
    }
}
