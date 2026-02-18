//! Rule-based fix suggestions
//!
//! Deterministic fix suggestions that work without AI/API keys.
//! For common issues, generates actual code patches.

use crate::models::Finding;
use std::fs;
use std::path::Path;

/// A rule-based fix suggestion with optional code patch
#[derive(Debug, Clone)]
pub struct RuleFix {
    /// Human-readable title
    pub title: String,
    /// Explanation of the fix
    pub description: String,
    /// Step-by-step instructions
    pub steps: Vec<String>,
    /// Optional code patch (unified diff format)
    pub patch: Option<String>,
    /// Whether this is an auto-applicable fix
    pub auto_applicable: bool,
}

impl RuleFix {
    /// Create a simple suggestion without auto-apply
    pub fn suggestion(
        title: impl Into<String>,
        description: impl Into<String>,
        steps: Vec<String>,
    ) -> Self {
        Self {
            title: title.into(),
            description: description.into(),
            steps,
            patch: None,
            auto_applicable: false,
        }
    }

    /// Create an auto-applicable fix with patch
    pub fn patch(
        title: impl Into<String>,
        description: impl Into<String>,
        steps: Vec<String>,
        patch: String,
    ) -> Self {
        Self {
            title: title.into(),
            description: description.into(),
            steps,
            patch: Some(patch),
            auto_applicable: true,
        }
    }
}

/// Generate a rule-based fix for a finding
pub fn generate_rule_fix(finding: &Finding, repo_path: &Path) -> Option<RuleFix> {
    let detector = finding.detector.as_str();

    match detector {
        "UnusedImportsDetector" => fix_unused_imports(finding, repo_path),
        "empty-catch-block" | "EmptyCatchDetector" => fix_empty_catch(finding, repo_path),
        "magic-numbers" | "MagicNumbersDetector" => fix_magic_numbers(finding, repo_path),
        "DeadCodeDetector" => fix_dead_code(finding, repo_path),
        "deep-nesting" | "DeepNestingDetector" => fix_deep_nesting(finding),
        "missing-await" | "MissingAwaitDetector" => fix_missing_await(finding, repo_path),
        "global-variables" | "GlobalVariablesDetector" => fix_global_variables(finding),
        "LongMethodsDetector" | "long-methods" => fix_long_methods(finding),
        "BroadExceptionDetector" | "broad-exception" => fix_broad_exception(finding, repo_path),
        "WildcardImportsDetector" | "wildcard-imports" => fix_wildcard_imports(finding),
        _ => None,
    }
}

/// Fix unused imports by generating a removal patch
fn fix_unused_imports(finding: &Finding, repo_path: &Path) -> Option<RuleFix> {
    let file_path = finding.affected_files.first()?;
    let full_path = repo_path.join(file_path);
    let content = fs::read_to_string(&full_path).ok()?;
    let lines: Vec<&str> = content.lines().collect();

    let line_start = finding.line_start? as usize;
    let line_end = finding.line_end.unwrap_or(finding.line_start?) as usize;

    // Extract import names from title
    let import_names: Vec<&str> = finding
        .title
        .trim_start_matches("Unused import: ")
        .trim_start_matches("Unused imports: ")
        .split(", ")
        .map(|s| s.trim())
        .collect();

    // Build the patch
    let mut patch_lines = Vec::new();
    patch_lines.push(format!("--- a/{}", file_path.display()));
    patch_lines.push(format!("+++ b/{}", file_path.display()));

    // Context around the change
    let context_start = line_start.saturating_sub(4);
    let context_end = (line_end + 3).min(lines.len());

    patch_lines.push(format!(
        "@@ -{},{} +{},{} @@",
        context_start + 1,
        context_end - context_start,
        context_start + 1,
        context_end - context_start - (line_end - line_start + 1)
    ));

    for (i, line) in lines
        .iter()
        .enumerate()
        .skip(context_start)
        .take(context_end - context_start)
    {
        let line_num = i + 1;
        if line_num >= line_start && line_num <= line_end {
            patch_lines.push(format!("-{}", line));
        } else {
            patch_lines.push(format!(" {}", line));
        }
    }

    let patch = patch_lines.join("\n");

    Some(RuleFix::patch(
        format!(
            "Remove unused import{}",
            if import_names.len() > 1 { "s" } else { "" }
        ),
        format!(
            "Remove the unused import{} `{}`",
            if import_names.len() > 1 { "s" } else { "" },
            import_names.join("`, `")
        ),
        vec![
            format!(
                "Delete line{} {}-{}",
                if line_start == line_end { "" } else { "s" },
                line_start,
                line_end
            ),
            "Run your linter to verify no other issues".to_string(),
        ],
        patch,
    ))
}

/// Fix empty catch by adding logging
fn fix_empty_catch(finding: &Finding, repo_path: &Path) -> Option<RuleFix> {
    let file_path = finding.affected_files.first()?;
    let full_path = repo_path.join(file_path);
    let content = fs::read_to_string(&full_path).ok()?;
    let lines: Vec<&str> = content.lines().collect();

    let line_num = finding.line_start? as usize;
    if line_num == 0 || line_num > lines.len() {
        return None;
    }

    let catch_line = lines[line_num - 1];
    let indent = catch_line.len() - catch_line.trim_start().len();
    let indent_str: String = " ".repeat(indent);
    let inner_indent: String = " ".repeat(indent + 4);

    let ext = file_path.extension().and_then(|e| e.to_str()).unwrap_or("");

    let Some((new_catch, new_body)) = empty_catch_replacement(ext, &indent_str, &inner_indent) else {
        return None;
    };

    // Build patch
    let mut patch_lines = Vec::new();
    patch_lines.push(format!("--- a/{}", file_path.display()));
    patch_lines.push(format!("+++ b/{}", file_path.display()));

    let context_start = line_num.saturating_sub(3);
    let context_end = (line_num + 3).min(lines.len());

    patch_lines.push(format!(
        "@@ -{},{} +{},{} @@",
        context_start + 1,
        context_end - context_start,
        context_start + 1,
        context_end - context_start + 2 // Adding lines
    ));

    for (i, line) in lines
        .iter()
        .enumerate()
        .skip(context_start)
        .take(context_end - context_start)
    {
        if i + 1 == line_num {
            patch_lines.push(format!("-{}", line));
            patch_lines.push(format!("+{}", new_catch));
            if let Some(next_line) = lines.get(i + 1) {
                if next_line.trim() == "pass" || next_line.trim() == "..." {
                    // Skip the pass/... line
                    continue;
                }
            }
            patch_lines.push(format!("+{}", new_body));
        } else if i + 1 == line_num + 1 && (lines[i].trim() == "pass" || lines[i].trim() == "...") {
            patch_lines.push(format!("-{}", line));
        } else {
            patch_lines.push(format!(" {}", line));
        }
    }

    Some(RuleFix::patch(
        "Add error handling to catch block".to_string(),
        "Replace empty catch with proper error logging".to_string(),
        vec![
            "Add exception variable to catch clause".to_string(),
            "Log the error with appropriate logger".to_string(),
            "Consider whether to re-throw or handle specifically".to_string(),
        ],
        patch_lines.join("\n"),
    ))
}

/// Fix magic numbers by suggesting constant extraction
fn empty_catch_replacement(ext: &str, indent: &str, inner: &str) -> Option<(String, String)> {
    match ext {
        "py" => Some((
            format!("{}except Exception as e:", indent),
            format!("{}import logging\n{}logger = logging.getLogger(__name__)\n{}logger.error(f\"Error occurred: {{{{e}}}}\")", inner, inner, inner),
        )),
        "js" | "ts" | "jsx" | "tsx" => Some((
            format!("{}catch (error) {{", indent),
            format!("{}console.error('Error occurred:', error);\n{}}}", inner, indent),
        )),
        "java" | "cs" => Some((
            format!("{}catch (Exception e) {{", indent),
            format!("{}// Log the exception\n{}System.err.println(\"Error: \" + e.getMessage());\n{}e.printStackTrace();\n{}}}", inner, inner, inner, indent),
        )),
        _ => None,
    }
}

fn fix_magic_numbers(finding: &Finding, repo_path: &Path) -> Option<RuleFix> {
    let file_path = finding.affected_files.first()?;
    let full_path = repo_path.join(file_path);
    let content = fs::read_to_string(&full_path).ok()?;
    let lines: Vec<&str> = content.lines().collect();

    let line_num = finding.line_start? as usize;
    if line_num == 0 || line_num > lines.len() {
        return None;
    }

    // Extract the magic number from the title
    let number = finding.title.trim_start_matches("Magic number: ").trim();

    let line = lines[line_num - 1];
    let ext = file_path.extension().and_then(|e| e.to_str()).unwrap_or("");

    // Suggest a constant name based on context
    let const_name = suggest_constant_name(number, line);

    // Generate the constant declaration
    let const_decl = match ext {
        "py" => format!("{} = {}", const_name, number),
        "js" | "ts" | "jsx" | "tsx" => format!("const {} = {};", const_name, number),
        "rs" => format!("const {}: i64 = {};", const_name, number),
        "go" => format!("const {} = {}", const_name, number),
        "java" | "cs" => format!("private static final int {} = {};", const_name, number),
        _ => format!("const {} = {}", const_name, number),
    };

    Some(RuleFix::suggestion(
        format!("Extract magic number {} to constant", number),
        format!(
            "Replace magic number {} with named constant `{}`",
            number, const_name
        ),
        vec![
            format!("Add constant at module/class level: `{}`", const_decl),
            format!(
                "Replace `{}` with `{}` on line {}",
                number, const_name, line_num
            ),
            "Search for other occurrences of this number in the file".to_string(),
        ],
    ))
}

/// Suggest a constant name based on context
fn suggest_constant_name(number: &str, context: &str) -> String {
    let ctx_lower = context.to_lowercase();
    let num: i64 = number.parse().unwrap_or(0);

    // Time constants
    if num == 3600 || ctx_lower.contains("hour") {
        return "SECONDS_PER_HOUR".to_string();
    }
    if num == 86400 || ctx_lower.contains("day") {
        return "SECONDS_PER_DAY".to_string();
    }
    if num == 604800 || ctx_lower.contains("week") {
        return "SECONDS_PER_WEEK".to_string();
    }

    // Common patterns
    if ctx_lower.contains("timeout") || ctx_lower.contains("delay") {
        return "TIMEOUT_MS".to_string();
    }
    if ctx_lower.contains("port") {
        return "PORT".to_string();
    }
    if ctx_lower.contains("retry") || ctx_lower.contains("attempt") {
        return "MAX_RETRIES".to_string();
    }
    if ctx_lower.contains("size") || ctx_lower.contains("limit") || ctx_lower.contains("max") {
        return "MAX_SIZE".to_string();
    }
    if ctx_lower.contains("min") {
        return "MIN_VALUE".to_string();
    }
    if (200..600).contains(&num) && (ctx_lower.contains("status") || ctx_lower.contains("http")) {
        return format!("HTTP_STATUS_{}", num);
    }

    // Default
    format!("CONSTANT_{}", num)
}

/// Fix dead code by suggesting removal
fn fix_dead_code(finding: &Finding, _repo_path: &Path) -> Option<RuleFix> {
    let _is_function = finding.title.contains("function");
    let is_class = finding.title.contains("class");

    let item_type = if is_class { "class" } else { "function" };
    let name = finding
        .title
        .trim_start_matches("Unused function: ")
        .trim_start_matches("Unused class: ")
        .to_string();

    Some(RuleFix::suggestion(
        format!("Remove unused {}: {}", item_type, name),
        format!(
            "The {} `{}` is never called. Review before removing.",
            item_type, name
        ),
        vec![
            format!(
                "Verify `{}` is not called dynamically (reflection, eval, etc.)",
                name
            ),
            "Check if it's an API endpoint, callback, or event handler".to_string(),
            format!("If truly unused, delete the {} definition", item_type),
            "Run tests to verify nothing breaks".to_string(),
        ],
    ))
}

/// Fix deep nesting by suggesting extraction
fn fix_deep_nesting(finding: &Finding) -> Option<RuleFix> {
    let nesting_level: usize = finding
        .title
        .split(':')
        .nth(1)?
        .split_whitespace()
        .next()?
        .parse()
        .ok()?;

    let mut steps = vec![
        "Use guard clauses (early returns) to reduce nesting:".to_string(),
        "  `if (!condition) return;` instead of `if (condition) { ... }`".to_string(),
    ];

    if nesting_level > 5 {
        steps.extend(vec![
            "Extract deeply nested blocks into separate functions:".to_string(),
            "  - Each nested block can become its own function".to_string(),
            "  - Name functions descriptively for what they do".to_string(),
        ]);
    }

    if nesting_level > 7 {
        steps.extend(vec![
            "Consider using strategy/command pattern for complex conditionals".to_string(),
            "Replace nested loops with functional patterns (map/filter/reduce)".to_string(),
        ]);
    }

    Some(RuleFix::suggestion(
        "Reduce nesting depth".to_string(),
        format!(
            "Code has {} levels of nesting. Deeply nested code is hard to read and maintain.",
            nesting_level
        ),
        steps,
    ))
}

/// Fix missing await
fn fix_missing_await(finding: &Finding, repo_path: &Path) -> Option<RuleFix> {
    let file_path = finding.affected_files.first()?;
    let full_path = repo_path.join(file_path);
    let content = fs::read_to_string(&full_path).ok()?;
    let lines: Vec<&str> = content.lines().collect();

    let line_num = finding.line_start? as usize;
    if line_num == 0 || line_num > lines.len() {
        return None;
    }

    let line = lines[line_num - 1];
    let _ext = file_path.extension().and_then(|e| e.to_str()).unwrap_or("");

    // Try to identify where to add await
    let trimmed = line.trim();

    // Common patterns: result = fetch(...), data = asyncFunc()
    let new_line = if trimmed.contains(" = ") {
        let parts: Vec<&str> = trimmed.splitn(2, " = ").collect();
        if parts.len() == 2 {
            let indent: String = line.chars().take_while(|c| c.is_whitespace()).collect();
            Some(format!("{}{} = await {}", indent, parts[0], parts[1]))
        } else {
            None
        }
    } else {
        // Just prepend await
        let indent: String = line.chars().take_while(|c| c.is_whitespace()).collect();
        Some(format!("{}await {}", indent, trimmed))
    };

    if let Some(fixed_line) = new_line {
        let mut patch_lines = Vec::new();
        patch_lines.push(format!("--- a/{}", file_path.display()));
        patch_lines.push(format!("+++ b/{}", file_path.display()));

        let context_start = line_num.saturating_sub(3);
        let context_end = (line_num + 3).min(lines.len());

        patch_lines.push(format!(
            "@@ -{},{} +{},{} @@",
            context_start + 1,
            context_end - context_start,
            context_start + 1,
            context_end - context_start
        ));

        for (i, l) in lines
            .iter()
            .enumerate()
            .skip(context_start)
            .take(context_end - context_start)
        {
            if i + 1 == line_num {
                patch_lines.push(format!("-{}", l));
                patch_lines.push(format!("+{}", fixed_line));
            } else {
                patch_lines.push(format!(" {}", l));
            }
        }

        return Some(RuleFix::patch(
            "Add await to async call".to_string(),
            "Add `await` keyword before the async function call".to_string(),
            vec![
                format!("Change line {} to include await", line_num),
                "Ensure the containing function is async".to_string(),
            ],
            patch_lines.join("\n"),
        ));
    }

    // Fallback to suggestion
    Some(RuleFix::suggestion(
        "Add await to async call".to_string(),
        "The async function is called without await, returning a Promise/coroutine instead of the value.".to_string(),
        vec![
            format!("Add `await` before the async call on line {}", line_num),
            "Python: `result = await async_function()`".to_string(),
            "JS/TS: `const result = await asyncFunction();`".to_string(),
            "Ensure the containing function is marked as async".to_string(),
        ],
    ))
}

/// Fix global variables
fn fix_global_variables(finding: &Finding) -> Option<RuleFix> {
    let var_name = finding
        .title
        .trim_start_matches("Global mutable variable: ")
        .to_string();

    Some(RuleFix::suggestion(
        format!("Encapsulate global variable: {}", var_name),
        format!(
            "Global mutable state `{}` makes code harder to reason about and test.",
            var_name
        ),
        vec![
            "Option 1: Make it a constant if it shouldn't change:".to_string(),
            format!("  `const {} = value;`  // JS/TS", var_name.to_uppercase()),
            format!("  `{} = value  # Final/constant`", var_name.to_uppercase()),
            "".to_string(),
            "Option 2: Encapsulate in a class or module:".to_string(),
            "  ```".to_string(),
            "  class Config:".to_string(),
            format!("      _{}: int = 0", var_name),
            "      @classmethod".to_string(),
            format!("      def get_{}(cls): return cls._{}", var_name, var_name),
            "  ```".to_string(),
            "".to_string(),
            "Option 3: Pass as function parameter instead".to_string(),
        ],
    ))
}

/// Fix long methods
fn fix_long_methods(_finding: &Finding) -> Option<RuleFix> {
    Some(RuleFix::suggestion(
        "Break down long method into smaller functions".to_string(),
        "Long methods are hard to understand, test, and maintain.".to_string(),
        vec![
            "1. Identify logical sections in the method".to_string(),
            "2. Extract each section into a well-named helper function".to_string(),
            "3. Give each function a single responsibility".to_string(),
            "4. Keep functions under 20-30 lines when possible".to_string(),
            "".to_string(),
            "Common extraction patterns:".to_string(),
            "  - Validation logic → `validate_input()`".to_string(),
            "  - Data transformation → `transform_data()`".to_string(),
            "  - API calls → `fetch_from_api()`".to_string(),
            "  - Formatting → `format_output()`".to_string(),
        ],
    ))
}

/// Fix broad exception handling
fn fix_broad_exception(finding: &Finding, _repo_path: &Path) -> Option<RuleFix> {
    let file_path = finding.affected_files.first()?;
    let ext = file_path.extension().and_then(|e| e.to_str()).unwrap_or("");

    let examples = match ext {
        "py" => vec![
            "Catch specific exceptions instead of bare `except:` or `except Exception:`"
                .to_string(),
            "".to_string(),
            "Example:".to_string(),
            "```python".to_string(),
            "try:".to_string(),
            "    response = requests.get(url)".to_string(),
            "except requests.Timeout:".to_string(),
            "    # Handle timeout specifically".to_string(),
            "except requests.RequestException as e:".to_string(),
            "    logger.error(f\"Request failed: {e}\")".to_string(),
            "```".to_string(),
        ],
        "js" | "ts" | "jsx" | "tsx" => vec![
            "Use type checking or custom error classes:".to_string(),
            "".to_string(),
            "```typescript".to_string(),
            "try {".to_string(),
            "    await fetchData();".to_string(),
            "} catch (error) {".to_string(),
            "    if (error instanceof NetworkError) {".to_string(),
            "        // Handle network issues".to_string(),
            "    } else if (error instanceof ValidationError) {".to_string(),
            "        // Handle validation".to_string(),
            "    } else {".to_string(),
            "        throw error; // Re-throw unknown errors".to_string(),
            "    }".to_string(),
            "}".to_string(),
            "```".to_string(),
        ],
        _ => vec![
            "Catch specific exception types instead of catching all exceptions.".to_string(),
            "Log or re-throw exceptions you can't handle.".to_string(),
        ],
    };

    Some(RuleFix::suggestion(
        "Use specific exception handling".to_string(),
        "Broad exception handling can hide bugs and make debugging difficult.".to_string(),
        examples,
    ))
}

/// Fix wildcard imports
fn fix_wildcard_imports(_finding: &Finding) -> Option<RuleFix> {
    Some(RuleFix::suggestion(
        "Replace wildcard import with explicit imports".to_string(),
        "Wildcard imports (`from x import *`) pollute the namespace and make it unclear what's being used.".to_string(),
        vec![
            "1. Identify which names are actually used from the module".to_string(),
            "2. Replace `from module import *` with explicit imports:".to_string(),
            "   `from module import ClassA, function_b, CONSTANT_C`".to_string(),
            "".to_string(),
            "Benefits:".to_string(),
            "  - Clear dependencies".to_string(),
            "  - Better IDE support".to_string(),
            "  - Avoids name collisions".to_string(),
            "  - Enables tree-shaking in bundlers".to_string(),
        ],
    ))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::models::{Finding, Severity};
    use std::path::PathBuf;

    fn make_finding(detector: &str, title: &str, file: &str, line: u32) -> Finding {
        Finding {
            id: "test".to_string(),
            detector: detector.to_string(),
            severity: Severity::Medium,
            title: title.to_string(),
            description: "Test".to_string(),
            affected_files: vec![PathBuf::from(file)],
            line_start: Some(line),
            line_end: Some(line),
            ..Default::default()
        }
    }

    #[test]
    fn test_suggest_constant_name() {
        assert_eq!(
            suggest_constant_name("3600", "timeout = 3600"),
            "SECONDS_PER_HOUR"
        );
        assert_eq!(suggest_constant_name("86400", "days"), "SECONDS_PER_DAY");
        assert_eq!(suggest_constant_name("5", "retry_count = 5"), "MAX_RETRIES");
        assert_eq!(suggest_constant_name("8080", "port = 8080"), "PORT");
        assert_eq!(
            suggest_constant_name("200", "status == 200"),
            "HTTP_STATUS_200"
        );
    }

    #[test]
    fn test_dead_code_fix() {
        let finding = make_finding(
            "DeadCodeDetector",
            "Unused function: calculate_old",
            "src/utils.py",
            42,
        );

        let fix = fix_dead_code(&finding, Path::new(".")).unwrap();
        assert!(fix.title.contains("Remove unused function"));
        assert!(!fix.auto_applicable);
    }

    #[test]
    fn test_deep_nesting_fix() {
        let finding = make_finding(
            "deep-nesting",
            "Excessive nesting: 6 levels",
            "src/handler.ts",
            100,
        );

        let fix = fix_deep_nesting(&finding).unwrap();
        assert!(fix.title.contains("Reduce nesting"));
        assert!(fix.steps.iter().any(|s| s.contains("guard clauses")));
    }
}
