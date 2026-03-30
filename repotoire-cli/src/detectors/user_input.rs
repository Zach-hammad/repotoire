use crate::detectors::fast_search::{find_in, *};

/// Check if any line within +/-window of `line_num` contains user-input indicators.
///
/// Uses specific patterns to avoid false positives from generic words like "data":
/// - HTTP request accessors: `req.`, `request.`, `.body`, `.query`, `.params`
/// - Framework-specific: `getParameter`, `FormValue`, `getInputStream`, `PostForm`
/// - Variable naming: `user_input`, `userInput`, `payload`
pub fn has_nearby_user_input(lines: &[&str], line_num: usize, window: usize) -> bool {
    let start = line_num.saturating_sub(window);
    let end = (line_num + window + 1).min(lines.len());
    lines[start..end].iter().any(|l| {
        // HTTP request object accessors
        find_in(&FIND_REQ_DOT, l) || find_in(&FIND_REQUEST_DOT, l) || find_in(&FIND_R_URL, l)
        // Body/query/params accessors
        || find_in(&FIND_DOT_BODY, l) || find_in(&FIND_DOT_QUERY, l) || find_in(&FIND_DOT_PARAMS, l)
        // Framework-specific input methods
        || find_in(&FIND_GET_PARAMETER, l) || find_in(&FIND_FORM_VALUE, l)
        || find_in(&FIND_GET_INPUT_STREAM, l) || find_in(&FIND_POST_FORM, l)
        || find_in(&FIND_GET_HEADER, l) || find_in(&FIND_R_FORM, l)
        // Common user-input variable names
        || find_in(&FIND_USER_INPUT, l) || find_in(&FIND_USER_INPUT_CAMEL, l)
        || find_in(&FIND_USER_DATA, l) || find_in(&FIND_USER_DATA_CAMEL, l)
        || find_in(&FIND_PAYLOAD, l)
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_finds_request_on_nearby_line() {
        let lines = vec![
            "func handle(w http.ResponseWriter, r *http.Request) {",
            "    cmd := r.FormValue(\"command\")",
            "    // some processing",
            "    exec.Command(cmd).Run()",
        ];
        assert!(has_nearby_user_input(&lines, 3, 5));
    }

    #[test]
    fn test_no_match_without_user_input() {
        let lines = vec![
            "func processFile() {",
            "    data := readConfig()",
            "    exec.Command(\"ls\").Run()",
        ];
        assert!(!has_nearby_user_input(&lines, 2, 5));
    }

    #[test]
    fn test_window_boundary() {
        let lines = vec![
            "req := request.getParameter(\"id\")",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "exec.Command(cmd).Run()",
        ];
        // Window of 5 shouldn't reach line 0 from line 11
        assert!(!has_nearby_user_input(&lines, 11, 5));
        // Window of 15 should
        assert!(has_nearby_user_input(&lines, 11, 15));
    }

    #[test]
    fn test_does_not_match_generic_words() {
        let lines = vec![
            "let metadata = process_formatted_data(records);",
            "exec.Command(\"ls\").Run()",
        ];
        assert!(!has_nearby_user_input(&lines, 1, 5));
    }
}
