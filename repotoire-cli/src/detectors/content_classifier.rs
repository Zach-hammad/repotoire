//! Content-based code classifier
//!
//! Detects code type by analyzing content rather than paths:
//! - Generated/bundled code (webpack, rollup, etc.)
//! - Minified code
//! - AST/compiler code patterns
//! - Test fixtures

use regex::Regex;
use std::sync::OnceLock;

static GENERATED_COMMENT: OnceLock<Regex> = OnceLock::new();
static UMD_WRAPPER: OnceLock<Regex> = OnceLock::new();
static COMMONJS_WRAPPER: OnceLock<Regex> = OnceLock::new();

fn generated_comment_regex() -> &'static Regex {
    GENERATED_COMMENT.get_or_init(|| {
        Regex::new(r"(?i)(?:generated\s+(?:by|from|using)|auto[- ]?generated|do\s+not\s+edit|machine\s+generated|this\s+file\s+is\s+generated)").unwrap()
    })
}

fn umd_wrapper_regex() -> &'static Regex {
    UMD_WRAPPER.get_or_init(|| {
        // UMD pattern: (function(global, factory) { ... })(this, function() {})
        Regex::new(r"^\s*\(function\s*\(\s*\w+\s*,\s*\w+\s*\)\s*\{").unwrap()
    })
}

fn commonjs_wrapper_regex() -> &'static Regex {
    COMMONJS_WRAPPER.get_or_init(|| {
        // CommonJS exports at very start, or 'use strict' + immediate exports
        Regex::new(r#"^(?:'use strict';\s*)?(?:Object\.defineProperty\(exports|exports\.\w+\s*=|module\.exports\s*=)"#).unwrap()
    })
}

/// Check if file appears to be bundled/generated code by path hints
/// Semantic path patterns that indicate non-source code
pub fn is_likely_bundled_path(path: &str) -> bool {
    let path_lower = path.to_lowercase();
    
    // Build output directories (semantic: these contain generated code)
    path_lower.contains("/dist/")
        || path_lower.contains("/build/")
        || path_lower.contains("/npm/")
        || path_lower.contains("/cjs/")
        || path_lower.contains("/esm/")
        || path_lower.contains("/umd/")
        || path_lower.contains(".min.")
        || path_lower.contains(".bundle.")
        // Test fixtures (semantic: simplified test examples, not production code)
        || path_lower.contains("/fixtures/")
        || path_lower.contains("/__fixtures__/")
        // Legacy compatibility shims
        || path_lower.contains("/legacy-")
        // Devtools (separate tooling, not core library)
        || path_lower.contains("/devtools-")
        || path_lower.contains("-devtools/")
}

/// Check if file is in a compiler/AST directory (needs higher thresholds, not skip)
pub fn is_compiler_code_path(path: &str) -> bool {
    let path_lower = path.to_lowercase();
    
    path_lower.contains("/compiler/")
        || path_lower.contains("/babel-plugin-")
        || path_lower.contains("/hir/")
        || path_lower.contains("/mir/")
        || path_lower.contains("/ast/")
        || path_lower.contains("/parser/")
        || path_lower.contains("/transform")
}

/// Check if file content appears to be bundled/generated code
pub fn is_bundled_code(content: &str) -> bool {
    let header = &content[..content.len().min(1000)];
    
    // 1. License block at very top (build tools add these)
    if header.starts_with("/*!") 
        || header.starts_with("/** @license")
        || header.starts_with("/**\n * @license")
    {
        return true;
    }
    
    // 2. Bundle tool signatures
    if header.contains("__webpack_require__")
        || header.contains("__webpack_exports__")
        || header.contains("System.register")
        || header.contains("define([\"require\"")
        || header.contains("define(['require'")
    {
        return true;
    }
    
    // 3. UMD wrapper pattern
    if umd_wrapper_regex().is_match(header) {
        return true;
    }
    
    // 4. CommonJS build output (starts with exports boilerplate)
    if commonjs_wrapper_regex().is_match(header) {
        // Extra check: must also have NODE_ENV guard (dev/prod builds)
        if content.contains("process.env.NODE_ENV") {
            return true;
        }
    }
    
    // 5. Generated file comments
    if generated_comment_regex().is_match(header) {
        return true;
    }
    
    // 6. Source map reference (definitely generated)
    if content.contains("//# sourceMappingURL=") {
        return true;
    }
    
    false
}

/// Check if file appears to be minified
pub fn is_minified_code(content: &str) -> bool {
    let line_count = content.lines().count();
    if line_count == 0 {
        return false;
    }
    
    // Minified = very few lines relative to size
    let avg_line_len = content.len() / line_count;
    if avg_line_len > 500 {
        return true;
    }
    
    // Check first non-empty line - minified code has long lines with terse var names
    for line in content.lines().take(5) {
        let trimmed = line.trim();
        if trimmed.len() > 500 {
            // Long line with many semicolons = minified
            let semicolons = trimmed.matches(';').count();
            if semicolons > 20 {
                return true;
            }
        }
    }
    
    false
}

/// Check if function appears to be AST/compiler manipulation code
pub fn is_ast_manipulation_code(func_name: &str, content: &str) -> bool {
    let name_lower = func_name.to_lowercase();
    
    // AST visitor patterns
    if name_lower.starts_with("visit")
        || name_lower.starts_with("transform")
        || name_lower.starts_with("traverse")
        || name_lower.starts_with("enter")
        || name_lower.starts_with("exit")
        || name_lower.starts_with("parse")
        || name_lower.starts_with("emit")
        || name_lower.starts_with("lower")
        || name_lower.starts_with("infer")
    {
        return true;
    }
    
    // Check content for AST-related types
    let ast_keywords = [
        "AST", "Node", "visitor", "Expr", "Stmt", "Decl",
        "Identifier", "Literal", "BinaryExpression", "CallExpression",
        "FunctionDeclaration", "VariableDeclaration", "BlockStatement",
    ];
    
    let content_sample = &content[..content.len().min(2000)];
    let matches = ast_keywords.iter().filter(|kw| content_sample.contains(*kw)).count();
    
    // If 3+ AST keywords in the sample, it's likely AST code
    matches >= 3
}

/// Check if this is test infrastructure code (fixtures, mocks, test utilities)
/// These should be analyzed with test-context rules, not skipped entirely
pub fn is_test_infrastructure(file_path: &str, content: &str) -> bool {
    let path_lower = file_path.to_lowercase();
    let header = &content[..content.len().min(500)];
    
    // 1. Explicit fixture/mock markers in content
    if header.contains("@fixture")
        || header.contains("@mock")
        || header.contains("// test fixture")
        || header.contains("// mock")
        || header.contains("/* fixture")
    {
        return true;
    }
    
    // 2. Jest/testing-library setup files
    if path_lower.contains("setuptest")
        || path_lower.contains("testsetup")
        || path_lower.contains("jest.config")
        || path_lower.contains("jest.setup")
    {
        return true;
    }
    
    // 3. Mock implementations (content-based)
    if header.contains("jest.fn()")
        || header.contains("jest.mock(")
        || header.contains("vi.fn()")
        || header.contains("sinon.stub")
        || header.contains("Mock")
    {
        // Count mock-related tokens
        let mock_count = content.matches("mock").count()
            + content.matches("Mock").count()
            + content.matches("stub").count()
            + content.matches("fake").count();
        if mock_count >= 3 {
            return true;
        }
    }
    
    false
}

/// Check if this is test fixture code (simplified examples for testing)
/// Returns true for code that exists only to be tested against, not production code
pub fn is_fixture_code(_file_path: &str, content: &str) -> bool {
    let header = &content[..content.len().min(1000)];
    
    // 1. Explicit fixture markers
    if header.contains("@fixture")
        || header.contains("// fixture")
        || header.contains("/* fixture")
        || header.contains("# fixture")
        || header.contains("test fixture")
    {
        return true;
    }
    
    // 2. High density of placeholder variable names (foo, bar, baz pattern)
    let placeholder_count = content.matches("foo").count()
        + content.matches("bar").count()
        + content.matches("baz").count()
        + content.matches("qux").count();
    
    // 4+ placeholders in a small file = fixture
    if placeholder_count >= 4 && content.len() < 5000 {
        return true;
    }
    
    // 3. Test assertion examples (code snippets for testing)
    let example_markers = ["// input:", "// output:", "// expected:", "// before:", "// after:"];
    let marker_count = example_markers.iter().filter(|m| content.contains(*m)).count();
    if marker_count >= 2 {
        return true;
    }
    
    false
}

#[cfg(test)]
mod tests {
    use super::*;
    
    #[test]
    fn test_webpack_detection() {
        let code = r#"(function(modules) { __webpack_require__(0); })"#;
        assert!(is_bundled_code(code));
    }
    
    #[test]
    fn test_minified_detection() {
        let code = "a={b:c,d:e,f:g,h:i,j:k,l:m,n:o,p:q,r:s,t:u,v:w,x:y,z:0};".repeat(100);
        assert!(is_minified_code(&code));
    }
    
    #[test]
    fn test_ast_code_detection() {
        let code = "function visitExpression(node: AST.Expression) { traverse(node.Identifier); }";
        assert!(is_ast_manipulation_code("visitExpression", code));
    }
}

#[cfg(test)]
mod react_tests {
    use super::*;
    
    #[test]
    fn test_react_license_header() {
        let content = r#"/** @license React v0.14.10
 * react-jsx-dev-runtime.development.js
 *
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 */
'use strict';
"#;
        assert!(is_bundled_code(content), "Should detect React license header as bundled");
    }
}
