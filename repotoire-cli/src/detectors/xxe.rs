//! XXE Injection Detector
//!
//! Graph-enhanced detection of XXE vulnerabilities:
//! - Detect XML parsers without secure configuration
//! - Language-specific protection checks
//! - Trace user input to XML parsing

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphStore;
use crate::models::{deterministic_finding_id, Finding, Severity};
use anyhow::Result;
use regex::Regex;
use std::path::PathBuf;
use std::sync::OnceLock;
use tracing::info;
use uuid::Uuid;

static XXE_PATTERN: OnceLock<Regex> = OnceLock::new();
static USER_INPUT: OnceLock<Regex> = OnceLock::new();

fn xxe_pattern() -> &'static Regex {
    XXE_PATTERN.get_or_init(|| {
        Regex::new(r"(?i)(xml\.parse|parseXML|XMLParser|DocumentBuilder|SAXParser|etree\.parse|lxml\.etree|xml\.etree|DOMParser|XMLReader|xml\.dom|minidom|pulldom|xml2js|fast-xml-parser|libxml)").unwrap()
    })
}

fn user_input() -> &'static Regex {
    USER_INPUT.get_or_init(|| {
        Regex::new(r"(req\.(body|file|files)|request\.(data|files)|uploaded|file_content|input|read\(|getInputStream)").unwrap()
    })
}

/// Get language-specific protection patterns
fn get_protection_patterns(ext: &str) -> Vec<&'static str> {
    match ext {
        "py" => vec![
            "resolve_entities=False",
            "no_network=True",
            "defusedxml",
            "forbid_dtd=True",
            "forbid_entities=True",
        ],
        "java" => vec![
            "FEATURE_SECURE_PROCESSING",
            "FEATURE_EXTERNAL_GENERAL_ENTITIES",
            "FEATURE_EXTERNAL_PARAMETER_ENTITIES",
            "FEATURE_DISALLOW_DOCTYPE_DECL",
            "setExpandEntityReferences(false)",
        ],
        "js" | "ts" => vec![
            "noent: false",
            "nonet: true",
            "dtdload: false",
            "dtdvalid: false",
            "explicitEntities: false",
        ],
        "php" => vec![
            "LIBXML_NOENT",
            "LIBXML_DTDLOAD",
            "libxml_disable_entity_loader",
        ],
        "cs" => vec![
            "DtdProcessing.Prohibit",
            "XmlResolver = null",
            "ProhibitDtd = true",
        ],
        "rb" => vec![
            "nonet: true",
            "noent: false",
            "Nokogiri::XML::ParseOptions::NONET",
        ],
        _ => vec![],
    }
}

/// Get language-specific fix example
fn get_fix_example(ext: &str) -> &'static str {
    match ext {
        "py" => {
            "```python\n\
             # Use defusedxml (recommended)\n\
             import defusedxml.ElementTree as ET\n\
             tree = ET.parse(xml_file)\n\
             \n\
             # Or configure lxml safely\n\
             from lxml import etree\n\
             parser = etree.XMLParser(\n\
                 resolve_entities=False,\n\
                 no_network=True,\n\
                 dtd_validation=False\n\
             )\n\
             tree = etree.parse(xml_file, parser)\n\
             ```"
        }
        "java" => {
            "```java\n\
             DocumentBuilderFactory dbf = DocumentBuilderFactory.newInstance();\n\
             \n\
             // Disable XXE\n\
             dbf.setFeature(\"http://apache.org/xml/features/disallow-doctype-decl\", true);\n\
             dbf.setFeature(\"http://xml.org/sax/features/external-general-entities\", false);\n\
             dbf.setFeature(\"http://xml.org/sax/features/external-parameter-entities\", false);\n\
             dbf.setXIncludeAware(false);\n\
             dbf.setExpandEntityReferences(false);\n\
             \n\
             DocumentBuilder db = dbf.newDocumentBuilder();\n\
             ```"
        }
        "js" | "ts" => {
            "```javascript\n\
             // Use a safe parser\n\
             const { XMLParser } = require('fast-xml-parser');\n\
             const parser = new XMLParser({\n\
                 allowBooleanAttributes: true,\n\
                 // No external entity resolution by default\n\
             });\n\
             \n\
             // Or configure libxmljs safely\n\
             const libxmljs = require('libxmljs');\n\
             const doc = libxmljs.parseXml(xmlString, {\n\
                 noent: false,  // Don't expand entities\n\
                 nonet: true,   // Don't fetch from network\n\
                 dtdload: false\n\
             });\n\
             ```"
        }
        "php" => {
            "```php\n\
             // Disable entity loading (PHP < 8.0)\n\
             libxml_disable_entity_loader(true);\n\
             \n\
             // Use LIBXML_NOENT and LIBXML_DTDLOAD flags\n\
             $doc = new DOMDocument();\n\
             $doc->loadXML($xml, LIBXML_NONET | LIBXML_DTDLOAD);\n\
             \n\
             // Better: Use SimpleXML with safe options\n\
             $xml = simplexml_load_string($data, 'SimpleXMLElement', LIBXML_NOENT);\n\
             ```"
        }
        "cs" => {
            "```csharp\n\
             XmlReaderSettings settings = new XmlReaderSettings();\n\
             settings.DtdProcessing = DtdProcessing.Prohibit;\n\
             settings.XmlResolver = null;\n\
             \n\
             using (XmlReader reader = XmlReader.Create(stream, settings))\n\
             {\n\
                 // Process XML safely\n\
             }\n\
             ```"
        }
        _ => "Disable external entity resolution in your XML parser configuration.",
    }
}

pub struct XxeDetector {
    repository_path: PathBuf,
    max_findings: usize,
}

impl XxeDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self {
            repository_path: repository_path.into(),
            max_findings: 50,
        }
    }

    /// Check for XXE protections in file content
    fn has_protection(content: &str, ext: &str) -> bool {
        let patterns = get_protection_patterns(ext);
        let content_lower = content.to_lowercase();

        patterns
            .iter()
            .any(|p| content_lower.contains(&p.to_lowercase()))
    }

    /// Check if user input flows to XML parsing
    fn has_user_input_flow(lines: &[&str], parse_line: usize) -> bool {
        let start = parse_line.saturating_sub(10);
        let context = lines[start..parse_line].join(" ");

        user_input().is_match(&context)
    }

    /// Find containing function
    fn find_containing_function(
        graph: &dyn crate::graph::GraphQuery,
        file_path: &str,
        line: u32,
    ) -> Option<(String, bool)> {
        graph
            .get_functions()
            .into_iter()
            .find(|f| f.file_path == file_path && f.line_start <= line && f.line_end >= line)
            .map(|f| {
                let callers = graph.get_callers(&f.qualified_name);
                let has_external_callers = callers.iter().any(|c| {
                    let name = c.name.to_lowercase();
                    name.contains("route")
                        || name.contains("handler")
                        || name.contains("api")
                        || name.contains("upload")
                        || name.contains("import")
                        || name.contains("parse")
                });
                (f.name, has_external_callers)
            })
    }
}

impl Detector for XxeDetector {
    fn name(&self) -> &'static str {
        "xxe"
    }
    fn description(&self) -> &'static str {
        "Detects XXE vulnerabilities"
    }

    fn detect(&self, graph: &dyn crate::graph::GraphQuery) -> Result<Vec<Finding>> {
        let mut findings = vec![];
        let walker = ignore::WalkBuilder::new(&self.repository_path)
            .hidden(false)
            .git_ignore(true)
            .build();

        for entry in walker.filter_map(|e| e.ok()) {
            if findings.len() >= self.max_findings {
                break;
            }
            let path = entry.path();
            if !path.is_file() {
                continue;
            }

            let path_str = path.to_string_lossy().to_string();

            // Skip test files
            if crate::detectors::base::is_test_path(&path_str) {
                continue;
            }

            let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");
            if !matches!(
                ext,
                "py" | "js" | "ts" | "java" | "php" | "cs" | "rb" | "go"
            ) {
                continue;
            }

            if let Some(content) = crate::cache::global_cache().get_content(path) {
                // Don't skip entire file — check protection near each parse call (#16)
                let file_has_any_protection = Self::has_protection(&content, ext);

                let lines: Vec<&str> = content.lines().collect();

                for (i, line) in lines.iter().enumerate() {
                    if !xxe_pattern().is_match(line) {
                        continue;
                    }

                    // Check for protection near this parse call, not just file-wide (#16)
                    // Look 15 lines before and after for protection patterns
                    if file_has_any_protection {
                        let local_start = i.saturating_sub(15);
                        let local_end = (i + 15).min(lines.len());
                        let local_context = lines[local_start..local_end].join("\n");
                        if Self::has_protection(&local_context, ext) {
                            continue; // This specific parser is protected
                        }
                    }

                    // Check for user input flow
                    let has_user_input = Self::has_user_input_flow(&lines, i);

                    // Get function context
                    let func_context =
                        Self::find_containing_function(graph, &path_str, (i + 1) as u32);

                    // Calculate severity
                    let severity = if has_user_input {
                        Severity::Critical // User input directly to XML parser
                    } else {
                        Severity::High // XXE is always serious
                    };

                    // Build notes
                    let mut notes = Vec::new();
                    if has_user_input {
                        notes.push("⚠️ User input flows to XML parser".to_string());
                    }
                    if let Some((func_name, external)) = &func_context {
                        notes.push(format!("📦 In function: `{}`", func_name));
                        if *external {
                            notes.push("🌐 Called from route handlers".to_string());
                        }
                    }
                    notes.push(format!("❌ No XXE protection detected for {}", ext));

                    let context_notes = format!("\n\n**Analysis:**\n{}", notes.join("\n"));

                    findings.push(Finding {
                        id: Uuid::new_v4().to_string(),
                        detector: "XxeDetector".to_string(),
                        severity,
                        title: "XML External Entity (XXE) vulnerability".to_string(),
                        description: format!(
                            "XML parser processes external entities without proper restrictions.{}",
                            context_notes
                        ),
                        affected_files: vec![path.to_path_buf()],
                        line_start: Some((i + 1) as u32),
                        line_end: Some((i + 1) as u32),
                        suggested_fix: Some(get_fix_example(ext).to_string()),
                        estimated_effort: Some("20 minutes".to_string()),
                        category: Some("security".to_string()),
                        cwe_id: Some("CWE-611".to_string()),
                        why_it_matters: Some(
                            "XXE vulnerabilities allow attackers to:\n\
                             • Read arbitrary files from the server (file:///etc/passwd)\n\
                             • Perform SSRF attacks (http://internal-server/)\n\
                             • Denial of service (billion laughs attack)\n\
                             • Port scanning of internal networks"
                                .to_string(),
                        ),
                        ..Default::default()
                    });
                }
            }
        }

        // Supplement with intra-function taint analysis
        let taint_analyzer = crate::detectors::taint::TaintAnalyzer::new();
        let intra_paths = crate::detectors::data_flow::run_intra_function_taint(
            // XXE is closer to PathTraversal (file disclosure) than CodeInjection (#16)
            &taint_analyzer,
            graph,
            crate::detectors::taint::TaintCategory::PathTraversal,
            &self.repository_path,
        );
        let mut seen: std::collections::HashSet<(String, u32)> = findings
            .iter()
            .filter_map(|f| {
                f.affected_files
                    .first()
                    .map(|p| (p.to_string_lossy().to_string(), f.line_start.unwrap_or(0)))
            })
            .collect();
        for path in intra_paths.iter().filter(|p| !p.is_sanitized) {
            let loc = (path.sink_file.clone(), path.sink_line);
            if !seen.insert(loc) {
                continue;
            }
            findings.push(crate::detectors::data_flow::taint_path_to_finding(
                path,
                "XxeDetector",
                "XML External Entity Injection",
            ));
            if findings.len() >= self.max_findings {
                break;
            }
        }

        info!(
            "XxeDetector found {} findings (graph-aware + taint)",
            findings.len()
        );
        Ok(findings)
    }
}
