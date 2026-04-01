//! Parse git diff -U0 output to extract changed line ranges per file.
//!
//! Used by the diff command to attribute findings to changed hunks.

use std::collections::{HashMap, HashSet};
use std::path::{Path, PathBuf};

/// How a finding relates to the code changes in a diff.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Attribution {
    /// Finding's line falls within a changed hunk (±3 lines margin).
    /// This is the PR author's responsibility.
    InChangedHunk,
    /// Finding is in a changed file but NOT in a changed hunk.
    /// Pre-existing issue.
    InChangedFile,
    /// Finding is in a file not touched by the diff.
    InUnchangedFile,
}

/// Changed line ranges extracted from a git diff.
pub struct DiffHunks {
    /// file_path → Vec of (start_line, end_line) ranges (1-based, inclusive).
    hunks: HashMap<PathBuf, Vec<(u32, u32)>>,
    /// All files that appear in the diff.
    changed_files: HashSet<PathBuf>,
    /// Renamed files: old_path → new_path.
    renames: HashMap<PathBuf, PathBuf>,
}

/// Line tolerance for hunk attribution (matches fuzzy matching in findings_match).
const HUNK_MARGIN: u32 = 3;

impl DiffHunks {
    /// Parse `git diff -U0 <base_ref>..HEAD` output.
    pub fn from_git_diff(repo_path: &Path, base_ref: &str) -> anyhow::Result<Self> {
        let output = std::process::Command::new("git")
            .args(["diff", "-U0", &format!("{base_ref}..HEAD")])
            .current_dir(repo_path)
            .output()
            .map_err(|e| anyhow::anyhow!("Failed to run git diff: {e}"))?;

        if !output.status.success() {
            // If git diff fails (e.g., invalid ref), return empty hunks
            return Ok(Self {
                hunks: HashMap::new(),
                changed_files: HashSet::new(),
                renames: HashMap::new(),
            });
        }

        let diff_text = String::from_utf8_lossy(&output.stdout);
        Ok(Self::parse_diff(&diff_text))
    }

    /// Parse raw git diff -U0 text into DiffHunks.
    pub fn parse_diff(diff_text: &str) -> Self {
        let mut hunks: HashMap<PathBuf, Vec<(u32, u32)>> = HashMap::new();
        let mut changed_files: HashSet<PathBuf> = HashSet::new();
        let mut renames: HashMap<PathBuf, PathBuf> = HashMap::new();
        let mut current_file: Option<PathBuf> = None;
        let mut pending_rename_from: Option<PathBuf> = None;

        for line in diff_text.lines() {
            // Track renames
            if let Some(old) = line.strip_prefix("rename from ") {
                pending_rename_from = Some(PathBuf::from(old));
            } else if let Some(new) = line.strip_prefix("rename to ") {
                if let Some(old) = pending_rename_from.take() {
                    let new_path = PathBuf::from(new);
                    changed_files.insert(new_path.clone());
                    renames.insert(old, new_path);
                }
            } else if let Some(path) = line.strip_prefix("+++ b/") {
                let p = PathBuf::from(path);
                changed_files.insert(p.clone());
                current_file = Some(p);
            } else if line.starts_with("--- ") {
                // Also track files from --- header (for deleted files)
                if let Some(path) = line.strip_prefix("--- a/") {
                    changed_files.insert(PathBuf::from(path));
                }
            } else if line.starts_with("@@ ") {
                // Parse hunk header: @@ -old_start,old_count +new_start,new_count @@
                if let Some(ref file) = current_file {
                    if let Some((start, count)) = parse_hunk_header(line) {
                        let end = if count == 0 {
                            start // deletion at this line, no new lines
                        } else {
                            start + count - 1
                        };
                        if count > 0 {
                            hunks.entry(file.clone()).or_default().push((start, end));
                        }
                    }
                }
            }
        }

        Self {
            hunks,
            changed_files,
            renames,
        }
    }

    /// Attribute a finding based on its file and line.
    pub fn attribute(&self, file: &Path, line: Option<u32>) -> Attribution {
        // Resolve old->new rename if this finding uses the pre-rename path
        let effective = self.renames.get(file).map(|p| p.as_path()).unwrap_or(file);

        if !self.changed_files.contains(effective) {
            return Attribution::InUnchangedFile;
        }

        // File-level findings (no line number) → InChangedFile
        let line = match line {
            Some(l) => l,
            None => return Attribution::InChangedFile,
        };

        // Check if line falls within any hunk (±HUNK_MARGIN)
        if let Some(file_hunks) = self.hunks.get(effective) {
            for &(start, end) in file_hunks {
                let expanded_start = start.saturating_sub(HUNK_MARGIN);
                let expanded_end = end.saturating_add(HUNK_MARGIN);
                if line >= expanded_start && line <= expanded_end {
                    return Attribution::InChangedHunk;
                }
            }
        }

        Attribution::InChangedFile
    }

    /// Number of changed files.
    pub fn changed_file_count(&self) -> usize {
        self.changed_files.len()
    }
}

/// Parse a hunk header line and extract (new_start, new_count).
/// Format: @@ -old_start,old_count +new_start,new_count @@
/// Count defaults to 1 if omitted (e.g., @@ -1 +1 @@).
fn parse_hunk_header(line: &str) -> Option<(u32, u32)> {
    // Find the + section
    let plus_idx = line.find('+')?;
    let after_plus = &line[plus_idx + 1..];

    // Find the end (space before @@)
    let end_idx = after_plus.find(" @@").unwrap_or(after_plus.len());
    let range_str = &after_plus[..end_idx];

    if let Some((start_str, count_str)) = range_str.split_once(',') {
        let start = start_str.parse::<u32>().ok()?;
        let count = count_str.parse::<u32>().ok()?;
        Some((start, count))
    } else {
        let start = range_str.parse::<u32>().ok()?;
        Some((start, 1)) // count defaults to 1
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_parse_hunk_header_with_count() {
        assert_eq!(
            parse_hunk_header("@@ -10,5 +20,3 @@ fn foo()"),
            Some((20, 3))
        );
    }

    #[test]
    fn test_parse_hunk_header_without_count() {
        assert_eq!(parse_hunk_header("@@ -10 +20 @@"), Some((20, 1)));
    }

    #[test]
    fn test_parse_hunk_header_zero_count() {
        // Deletion: no new lines added
        assert_eq!(parse_hunk_header("@@ -10,3 +20,0 @@"), Some((20, 0)));
    }

    #[test]
    fn test_parse_diff_single_file() {
        let diff = "\
diff --git a/src/main.rs b/src/main.rs
--- a/src/main.rs
+++ b/src/main.rs
@@ -10,3 +10,5 @@ fn main() {
+    let x = 1;
+    let y = 2;
";
        let hunks = DiffHunks::parse_diff(diff);
        assert!(hunks.changed_files.contains(&PathBuf::from("src/main.rs")));
        let file_hunks = hunks.hunks.get(&PathBuf::from("src/main.rs")).unwrap();
        assert_eq!(file_hunks, &[(10, 14)]); // start=10, count=5, end=14
    }

    #[test]
    fn test_parse_diff_multiple_hunks() {
        let diff = "\
diff --git a/src/api.rs b/src/api.rs
--- a/src/api.rs
+++ b/src/api.rs
@@ -5,2 +5,3 @@ fn handler() {
+    new_line();
@@ -50,1 +51,4 @@ fn query() {
+    more();
+    code();
+    here();
";
        let hunks = DiffHunks::parse_diff(diff);
        let file_hunks = hunks.hunks.get(&PathBuf::from("src/api.rs")).unwrap();
        assert_eq!(file_hunks.len(), 2);
        assert_eq!(file_hunks[0], (5, 7)); // start=5, count=3
        assert_eq!(file_hunks[1], (51, 54)); // start=51, count=4
    }

    #[test]
    fn test_attribute_in_changed_hunk() {
        let diff = "\
diff --git a/src/api.rs b/src/api.rs
--- a/src/api.rs
+++ b/src/api.rs
@@ -10,2 +10,5 @@ fn handler() {
";
        let hunks = DiffHunks::parse_diff(diff);
        // Line 12 is within hunk (10-14)
        assert_eq!(
            hunks.attribute(Path::new("src/api.rs"), Some(12)),
            Attribution::InChangedHunk
        );
    }

    #[test]
    fn test_attribute_in_changed_hunk_with_margin() {
        let diff = "\
diff --git a/src/api.rs b/src/api.rs
--- a/src/api.rs
+++ b/src/api.rs
@@ -10,2 +10,5 @@ fn handler() {
";
        let hunks = DiffHunks::parse_diff(diff);
        // Line 17 is hunk_end(14) + 3 margin = within margin
        assert_eq!(
            hunks.attribute(Path::new("src/api.rs"), Some(17)),
            Attribution::InChangedHunk
        );
        // Line 18 is hunk_end(14) + 4 = outside margin
        assert_eq!(
            hunks.attribute(Path::new("src/api.rs"), Some(18)),
            Attribution::InChangedFile
        );
    }

    #[test]
    fn test_attribute_in_changed_file() {
        let diff = "\
diff --git a/src/api.rs b/src/api.rs
--- a/src/api.rs
+++ b/src/api.rs
@@ -10,2 +10,5 @@ fn handler() {
";
        let hunks = DiffHunks::parse_diff(diff);
        // Line 100 is in the file but far from the hunk
        assert_eq!(
            hunks.attribute(Path::new("src/api.rs"), Some(100)),
            Attribution::InChangedFile
        );
    }

    #[test]
    fn test_attribute_in_unchanged_file() {
        let diff = "\
diff --git a/src/api.rs b/src/api.rs
--- a/src/api.rs
+++ b/src/api.rs
@@ -10,2 +10,5 @@ fn handler() {
";
        let hunks = DiffHunks::parse_diff(diff);
        assert_eq!(
            hunks.attribute(Path::new("src/other.rs"), Some(10)),
            Attribution::InUnchangedFile
        );
    }

    #[test]
    fn test_attribute_no_line_number() {
        let diff = "\
diff --git a/src/api.rs b/src/api.rs
--- a/src/api.rs
+++ b/src/api.rs
@@ -10,2 +10,5 @@ fn handler() {
";
        let hunks = DiffHunks::parse_diff(diff);
        // File-level finding (no line) → InChangedFile
        assert_eq!(
            hunks.attribute(Path::new("src/api.rs"), None),
            Attribution::InChangedFile
        );
    }

    #[test]
    fn test_empty_diff() {
        let hunks = DiffHunks::parse_diff("");
        assert_eq!(hunks.changed_file_count(), 0);
        assert_eq!(
            hunks.attribute(Path::new("any.rs"), Some(1)),
            Attribution::InUnchangedFile
        );
    }

    #[test]
    fn test_parse_diff_rename_without_content_change() {
        let diff = "\
diff --git a/src/old.rs b/src/new.rs
similarity index 100%
rename from src/old.rs
rename to src/new.rs
";
        let hunks = DiffHunks::parse_diff(diff);
        // Old path resolves to new path via rename
        assert_eq!(
            hunks.attribute(Path::new("src/old.rs"), Some(10)),
            Attribution::InChangedFile
        );
    }

    #[test]
    fn test_parse_diff_rename_with_content_change() {
        let diff = "\
diff --git a/src/old.rs b/src/new.rs
similarity index 80%
rename from src/old.rs
rename to src/new.rs
--- a/src/old.rs
+++ b/src/new.rs
@@ -5,0 +5,3 @@ fn foo() {
+    added();
+    lines();
+    here();
";
        let hunks = DiffHunks::parse_diff(diff);
        // Finding at line 6 in old path -> resolves via rename -> in hunk
        assert_eq!(
            hunks.attribute(Path::new("src/old.rs"), Some(6)),
            Attribution::InChangedHunk
        );
        // Finding at line 100 in old path -> resolves via rename -> in file but not hunk
        assert_eq!(
            hunks.attribute(Path::new("src/old.rs"), Some(100)),
            Attribution::InChangedFile
        );
    }
}
