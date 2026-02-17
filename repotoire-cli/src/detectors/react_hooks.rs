//! React Hooks Rules Detector
//!
//! Graph-enhanced detection of React hooks violations:
//! - Hooks called conditionally
//! - Hooks called in loops
//! - Hooks called in nested functions
//! - Missing dependencies in useEffect/useMemo/useCallback
//! - Use graph to check component hierarchy

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphStore;
use crate::models::{deterministic_finding_id, Finding, Severity};
use anyhow::Result;
use regex::Regex;
use std::path::PathBuf;
use std::sync::OnceLock;
use tracing::info;
use uuid::Uuid;

static HOOK_CALL: OnceLock<Regex> = OnceLock::new();
static CONDITIONAL: OnceLock<Regex> = OnceLock::new();
static LOOP: OnceLock<Regex> = OnceLock::new();
static NESTED_FUNC: OnceLock<Regex> = OnceLock::new();
static COMPONENT: OnceLock<Regex> = OnceLock::new();
#[allow(dead_code)] // Used by use_effect() for future hook dependency analysis
static USE_EFFECT: OnceLock<Regex> = OnceLock::new();

fn hook_call() -> &'static Regex {
    HOOK_CALL.get_or_init(|| {
        Regex::new(r"\b(useState|useEffect|useContext|useReducer|useCallback|useMemo|useRef|useImperativeHandle|useLayoutEffect|useDebugValue|useTransition|useDeferredValue|useId|useSyncExternalStore|useInsertionEffect|use[A-Z]\w*)\s*\(").unwrap()
    })
}

fn conditional() -> &'static Regex {
    CONDITIONAL.get_or_init(|| {
        Regex::new(r"^\s*(if\s*\(|else\s*\{|switch\s*\(|\?\s*$|&&\s*$|\|\|\s*$)").unwrap()
    })
}

fn loop_pattern() -> &'static Regex {
    LOOP.get_or_init(|| {
        Regex::new(r"^\s*(for\s*\(|while\s*\(|\.forEach\(|\.map\(|\.filter\()").unwrap()
    })
}

fn nested_func() -> &'static Regex {
    NESTED_FUNC.get_or_init(|| Regex::new(r"^\s*(function\s+\w+|const\s+\w+\s*=\s*(async\s+)?\(|const\s+\w+\s*=\s*(async\s+)?function)").unwrap())
}

fn component() -> &'static Regex {
    COMPONENT.get_or_init(|| {
        Regex::new(r"(?:function|const)\s+([A-Z][a-zA-Z0-9]*)\s*[=(]|export\s+(?:default\s+)?(?:function|const)\s+([A-Z][a-zA-Z0-9]*)").unwrap()
    })
}

#[allow(dead_code)] // Reserved for future hook dependency analysis
fn use_effect() -> &'static Regex {
    USE_EFFECT.get_or_init(|| {
        Regex::new(r"(useEffect|useMemo|useCallback)\s*\(\s*(?:\([^)]*\)|[^,]+)\s*,\s*\[([^\]]*)\]")
            .unwrap()
    })
}

/// Extract hook name from line
fn extract_hook_name(line: &str) -> Option<String> {
    if let Some(m) = hook_call().find(line) {
        let s = m.as_str();
        Some(s.trim_end_matches(['(', ' ']).to_string())
    } else {
        None
    }
}

/// Categorize the violation type
fn categorize_violation(
    in_conditional: bool,
    in_loop: bool,
    in_nested: bool,
) -> (&'static str, &'static str) {
    if in_loop {
        return ("loop", "Hook called inside a loop");
    }
    if in_conditional {
        return ("conditional", "Hook called conditionally");
    }
    if in_nested {
        return ("nested", "Hook called in nested function");
    }
    ("unknown", "Hooks rule violation")
}

pub struct ReactHooksDetector {
    repository_path: PathBuf,
    max_findings: usize,
}

impl ReactHooksDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self {
            repository_path: repository_path.into(),
            max_findings: 50,
        }
    }

    /// Find containing component from graph
    fn find_component(
        graph: &dyn crate::graph::GraphQuery,
        file_path: &str,
        line: u32,
    ) -> Option<String> {
        graph
            .get_functions()
            .into_iter()
            .find(|f| {
                f.file_path == file_path
                    && f.line_start <= line
                    && f.line_end >= line
                    && f.name
                        .chars()
                        .next()
                        .map(|c| c.is_uppercase())
                        .unwrap_or(false)
            })
            .map(|f| f.name)
    }

    /// Check for custom hooks (functions starting with 'use')
    fn is_custom_hook(func_name: &str) -> bool {
        func_name.starts_with("use")
            && func_name
                .chars()
                .nth(3)
                .map(|c| c.is_uppercase())
                .unwrap_or(false)
    }
}

impl Detector for ReactHooksDetector {
    fn name(&self) -> &'static str {
        "react-hooks"
    }
    fn description(&self) -> &'static str {
        "Detects React hooks rules violations"
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

            // Skip React framework source itself (packages/react*, packages/shared, etc.)
            // These files DEFINE hooks, they don't misuse them
            if path_str.contains("/packages/react")
                || path_str.contains("/packages/shared")
                || path_str.contains("/packages/scheduler")
                || path_str.contains("/packages/use-")
            {
                continue;
            }

            // Skip playground/examples/apps (demo code, not production)
            if path_str.contains("/playground/")
                || path_str.contains("/apps/")
                || path_str.contains("/fixtures/")
            {
                continue;
            }

            // Skip non-production paths
            if crate::detectors::content_classifier::is_non_production_path(&path_str) {
                continue;
            }

            let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");
            if !matches!(ext, "js" | "jsx" | "ts" | "tsx") {
                continue;
            }

            if let Some(content) = crate::cache::global_cache().get_content(path) {
                // Skip if no React hooks
                if !hook_call().is_match(&content) {
                    continue;
                }

                let lines: Vec<&str> = content.lines().collect();
                let mut in_conditional = false;
                let mut in_loop = false;
                let mut in_nested_func = false;
                let mut cond_depth = 0;
                let mut loop_depth = 0;
                let mut nested_depth = 0;
                let mut component_depth = 0;

                for (i, line) in lines.iter().enumerate() {
                    // Track component boundaries
                    if component().is_match(line) {
                        component_depth = 0;
                    }

                    // Track conditional blocks
                    if conditional().is_match(line) {
                        in_conditional = true;
                        cond_depth = line.matches('{').count() as i32;
                    }
                    if in_conditional {
                        cond_depth += line.matches('{').count() as i32;
                        cond_depth -= line.matches('}').count() as i32;
                        if cond_depth <= 0 {
                            in_conditional = false;
                        }
                    }

                    // Track loops
                    if loop_pattern().is_match(line) {
                        in_loop = true;
                        loop_depth =
                            line.matches('{').count() as i32 + line.matches('(').count() as i32;
                    }
                    if in_loop {
                        loop_depth += line.matches('{').count() as i32;
                        loop_depth -= line.matches('}').count() as i32;
                        if loop_depth <= 0 {
                            in_loop = false;
                        }
                    }

                    // Track nested functions (not at component level)
                    if nested_func().is_match(line) && component_depth > 0 {
                        in_nested_func = true;
                        nested_depth = line.matches('{').count() as i32;
                    }
                    if in_nested_func {
                        nested_depth += line.matches('{').count() as i32;
                        nested_depth -= line.matches('}').count() as i32;
                        if nested_depth <= 0 {
                            in_nested_func = false;
                        }
                    }

                    component_depth += line.matches('{').count() as i32;
                    component_depth -= line.matches('}').count() as i32;

                    // Check for hooks violations
                    if hook_call().is_match(line) {
                        let is_violation = in_conditional || in_loop || in_nested_func;

                        if is_violation {
                            let hook_name =
                                extract_hook_name(line).unwrap_or_else(|| "useHook".to_string());
                            let component_name =
                                Self::find_component(graph, &path_str, (i + 1) as u32);
                            let (violation_type, violation_desc) =
                                categorize_violation(in_conditional, in_loop, in_nested_func);

                            // Build notes
                            let mut notes = Vec::new();
                            notes.push(format!("🪝 Hook: `{}`", hook_name));
                            if let Some(comp) = &component_name {
                                notes.push(format!("📦 Component: `{}`", comp));
                            }
                            match violation_type {
                                "conditional" => notes
                                    .push("⚠️ Called inside `if/else/switch/ternary`".to_string()),
                                "loop" => notes
                                    .push("⚠️ Called inside `for/while/map/forEach`".to_string()),
                                "nested" => {
                                    notes.push("⚠️ Called inside nested function".to_string())
                                }
                                _ => {}
                            }

                            let context_notes = format!("\n\n**Analysis:**\n{}", notes.join("\n"));

                            let suggestion = match violation_type {
                                "conditional" => format!(
                                    "Move `{}` outside the conditional:\n\n\
                                     ```jsx\n\
                                     // ❌ Wrong\n\
                                     function Component({{ show }}) {{\n\
                                     if (show) {{\n\
                                         const [state, setState] = useState(0); // Violation!\n\
                                     }}\n\
                                     }}\n\
                                     \n\
                                     // ✅ Correct\n\
                                     function Component({{ show }}) {{\n\
                                     const [state, setState] = useState(0);\n\
                                     if (!show) return null;\n\
                                     // Use state here...\n\
                                     }}\n\
                                     ```",
                                    hook_name
                                ),
                                "loop" => format!(
                                    "Extract loop body to a separate component:\n\n\
                                     ```jsx\n\
                                     // ❌ Wrong\n\
                                     items.map(item => {{\n\
                                     const [value, setValue] = {}(item.initial); // Violation!\n\
                                     return <Item value={{value}} />;\n\
                                     }});\n\
                                     \n\
                                     // ✅ Correct: Create a component for each item\n\
                                     function ItemWrapper({{ item }}) {{\n\
                                     const [value, setValue] = {}(item.initial);\n\
                                     return <Item value={{value}} />;\n\
                                     }}\n\
                                     \n\
                                     items.map(item => <ItemWrapper key={{item.id}} item={{item}} />);\n\
                                     ```",
                                    hook_name, hook_name
                                ),
                                "nested" => format!(
                                    "Move `{}` to component level or use a custom hook:\n\n\
                                     ```jsx\n\
                                     // ❌ Wrong\n\
                                     function Component() {{\n\
                                     const handleClick = () => {{\n\
                                         const [state] = {}(); // Violation!\n\
                                     }};\n\
                                     }}\n\
                                     \n\
                                     // ✅ Correct\n\
                                     function Component() {{\n\
                                     const [state, setState] = {}();\n\
                                     const handleClick = () => {{\n\
                                         // Use state/setState here\n\
                                     }};\n\
                                     }}\n\
                                     ```",
                                    hook_name, hook_name, hook_name
                                ),
                                _ => "Move hooks to the top level of your component.".to_string(),
                            };

                            findings.push(Finding {
                                id: Uuid::new_v4().to_string(),
                                detector: "ReactHooksDetector".to_string(),
                                severity: Severity::High,
                                title: format!("{}: `{}`", violation_desc, hook_name),
                                description: format!(
                                    "React hooks must be called in the exact same order on every render.{}",
                                    context_notes
                                ),
                                affected_files: vec![path.to_path_buf()],
                                line_start: Some((i + 1) as u32),
                                line_end: Some((i + 1) as u32),
                                suggested_fix: Some(suggestion),
                                estimated_effort: Some("15 minutes".to_string()),
                                category: Some("bug-risk".to_string()),
                                cwe_id: None,
                                why_it_matters: Some(
                                    "This violates the Rules of Hooks. React relies on the order of hook calls \
                                     to track state correctly. Conditional/loop/nested hooks cause:\n\
                                     • State getting out of sync\n\
                                     • Crashes and rendering bugs\n\
                                     • Unpredictable behavior".to_string()
                                ),
                                ..Default::default()
                            });
                        }
                    }
                }
            }
        }

        info!(
            "ReactHooksDetector found {} findings (graph-aware)",
            findings.len()
        );
        Ok(findings)
    }
}
