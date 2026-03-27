use std::collections::HashMap;

use tower_lsp::lsp_types::{
    CodeAction, CodeActionKind, Position, Range, TextEdit, Url, WorkspaceEdit,
};

use crate::models::Finding;

/// Comment prefix for a given file extension.
fn comment_prefix(uri: &Url) -> &'static str {
    let path = uri.path();
    if path.ends_with(".py") || path.ends_with(".rb") {
        "#"
    } else {
        "//"
    }
}

/// Generate code actions for a finding at a given URI.
pub fn actions_for_finding(finding: &Finding, uri: &Url) -> Vec<CodeAction> {
    let mut actions = Vec::new();
    let line = finding.line_start.unwrap_or(1).saturating_sub(1); // 0-indexed

    // 1. Ignore suppression
    let prefix = comment_prefix(uri);
    let ignore_text = format!(
        "{} repotoire:ignore[{}]\n",
        prefix,
        finding.detector.to_lowercase()
    );

    let ignore_edit = TextEdit {
        range: Range {
            start: Position::new(line, 0),
            end: Position::new(line, 0),
        },
        new_text: ignore_text,
    };

    actions.push(CodeAction {
        title: format!("Ignore: {} (repotoire)", finding.detector),
        kind: Some(CodeActionKind::QUICKFIX),
        edit: Some(WorkspaceEdit {
            changes: Some(HashMap::from([(uri.clone(), vec![ignore_edit])])),
            ..Default::default()
        }),
        ..Default::default()
    });

    // 2. Suggested fix (if available)
    if let Some(fix) = &finding.suggested_fix {
        actions.push(CodeAction {
            title: format!("Fix: {}", finding.title),
            kind: Some(CodeActionKind::QUICKFIX),
            // Show fix description as a comment above the line
            edit: Some(WorkspaceEdit {
                changes: Some(HashMap::from([(
                    uri.clone(),
                    vec![TextEdit {
                        range: Range {
                            start: Position::new(line, 0),
                            end: Position::new(line, 0),
                        },
                        new_text: format!("{} FIX: {}\n", comment_prefix(uri), fix),
                    }],
                )])),
                ..Default::default()
            }),
            ..Default::default()
        });
    }

    actions
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::models::Severity;
    use std::path::PathBuf;

    #[test]
    fn ignore_action_python() {
        let f = Finding {
            detector: "SQLInjection".to_string(),
            line_start: Some(10),
            affected_files: vec![PathBuf::from("/tmp/app.py")],
            title: "SQL Injection".to_string(),
            severity: Severity::Critical,
            ..Default::default()
        };
        let uri = Url::from_file_path("/tmp/app.py").unwrap();
        let actions = actions_for_finding(&f, &uri);
        assert_eq!(actions.len(), 1); // no suggested_fix
        let edit = &actions[0].edit.as_ref().unwrap().changes.as_ref().unwrap()[&uri][0];
        assert!(edit.new_text.starts_with("# repotoire:ignore"));
    }

    #[test]
    fn ignore_action_rust() {
        let f = Finding {
            detector: "UnwrapDetector".to_string(),
            line_start: Some(5),
            affected_files: vec![PathBuf::from("/tmp/main.rs")],
            title: "Unwrap".to_string(),
            severity: Severity::Medium,
            ..Default::default()
        };
        let uri = Url::from_file_path("/tmp/main.rs").unwrap();
        let actions = actions_for_finding(&f, &uri);
        let edit = &actions[0].edit.as_ref().unwrap().changes.as_ref().unwrap()[&uri][0];
        assert!(edit.new_text.starts_with("// repotoire:ignore"));
    }

    #[test]
    fn suggested_fix_action() {
        let f = Finding {
            detector: "SQLi".to_string(),
            line_start: Some(10),
            affected_files: vec![PathBuf::from("/tmp/app.py")],
            title: "SQL Injection".to_string(),
            suggested_fix: Some("Use parameterized queries".to_string()),
            severity: Severity::Critical,
            ..Default::default()
        };
        let uri = Url::from_file_path("/tmp/app.py").unwrap();
        let actions = actions_for_finding(&f, &uri);
        assert_eq!(actions.len(), 2); // ignore + fix
        assert!(actions[1].title.contains("Fix:"));
    }
}
