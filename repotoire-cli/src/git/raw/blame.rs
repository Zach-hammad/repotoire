use super::diff::diff_blobs;
use super::error::GitError;
use super::oid::Oid;
use super::repo::RawRepo;

/// Upper bound on commits walked during a single `blame_file` call.
///
/// Blame history pre-dating this many commits contributes to line ownership
/// but costs a blob fetch + tree-diff per commit. On long-lived files with
/// deep history the tail of the walk dominates wall time while contributing
/// only attribution for the small fraction of lines that are truly ancient.
/// Capping at 500 caps the tail without materially changing signal for the
/// architectural-health consumers (bus factor, recency).
const MAX_BLAME_COMMITS: usize = 500;

#[derive(Debug, Clone)]
pub struct BlameHunk {
    pub commit: Oid,
    pub orig_start_line: u32,
    pub num_lines: u32,
    pub author_name: String,
    pub author_email: String,
    pub author_time: i64,
}

/// Blame a file, returning hunks that cover all lines.
///
/// Walks the commit history from HEAD, tracking which lines were introduced
/// by each commit using Myers diff. Bounded by `MAX_BLAME_COMMITS`; any lines
/// still unassigned after the cap are attributed to the oldest commit walked.
pub fn blame_file(repo: &RawRepo, file_path: &str) -> Result<Vec<BlameHunk>, GitError> {
    let head = repo.resolve_head()?;
    let head_commit = repo.find_commit(&head)?;

    // Find current file blob
    let current_blob_oid = find_blob_in_tree(repo, &head_commit.tree_oid, file_path)?
        .ok_or_else(|| GitError::RefNotFound(format!("file not found: {file_path}")))?;

    let content = repo.find_blob(&current_blob_oid)?;
    let total_lines = content.split(|&b| b == b'\n').filter(|l| !l.is_empty()).count() as u32;

    if total_lines == 0 {
        return Ok(Vec::new());
    }

    // Track which commit owns each line (1-indexed). None = unassigned.
    let mut line_owner: Vec<Option<Oid>> = vec![None; total_lines as usize];

    // Walk commits
    let mut current_oid = head;
    let mut current_content = content;
    let mut commits_walked: usize = 0;

    loop {
        let commit = repo.find_commit(&current_oid)?;
        commits_walked += 1;

        if commit.parents.is_empty() || repo.is_shallow(&current_oid) {
            // Root commit: assign all remaining lines to this commit
            for owner in &mut line_owner {
                if owner.is_none() {
                    *owner = Some(current_oid);
                }
            }
            break;
        }

        let parent_oid = commit.parents[0]; // first-parent only
        let parent_commit = repo.find_commit(&parent_oid)?;

        // Find file in parent tree
        let parent_blob_oid = find_blob_in_tree(repo, &parent_commit.tree_oid, file_path)?;

        match parent_blob_oid {
            None => {
                // File didn't exist in parent — all remaining lines belong to this commit
                for owner in &mut line_owner {
                    if owner.is_none() {
                        *owner = Some(current_oid);
                    }
                }
                break;
            }
            Some(parent_blob) => {
                let current_blob_oid_now =
                    find_blob_in_tree(repo, &commit.tree_oid, file_path)?;

                if current_blob_oid_now == Some(parent_blob) {
                    // No change in this commit, skip to parent
                    current_oid = parent_oid;
                    continue;
                }

                let parent_content = repo.find_blob(&parent_blob)?;
                let hunks = diff_blobs(&parent_content, &current_content);

                // Lines that were inserted/modified in this diff belong to current_oid
                for hunk in &hunks {
                    if hunk.new_lines > 0 {
                        let start = hunk.new_start.saturating_sub(1);
                        for i in start..start + hunk.new_lines {
                            if (i as u32) < total_lines && line_owner[i].is_none() {
                                line_owner[i] = Some(current_oid);
                            }
                        }
                    }
                }

                // Move to parent
                current_oid = parent_oid;
                current_content = parent_content;
            }
        }

        // Check if all lines assigned
        if line_owner.iter().all(|o| o.is_some()) {
            break;
        }

        // Bound the walk. If the cap is reached before every line is assigned,
        // the remaining ownerless lines get attributed to the oldest commit we
        // did walk — accurate enough for recency/bus-factor signals, and it
        // ensures we never pay the full-history tail.
        if commits_walked >= MAX_BLAME_COMMITS {
            for owner in &mut line_owner {
                if owner.is_none() {
                    *owner = Some(current_oid);
                }
            }
            break;
        }
    }

    // Build hunks from line_owner
    build_hunks(repo, &line_owner)
}

/// Walk tree to find a blob by file path (e.g., "src/main.rs").
fn find_blob_in_tree(
    repo: &RawRepo,
    tree_oid: &Oid,
    path: &str,
) -> Result<Option<Oid>, GitError> {
    let parts: Vec<&str> = path.split('/').collect();
    find_blob_recursive(repo, tree_oid, &parts)
}

fn find_blob_recursive(
    repo: &RawRepo,
    tree_oid: &Oid,
    parts: &[&str],
) -> Result<Option<Oid>, GitError> {
    if parts.is_empty() {
        return Ok(None);
    }

    let entries = repo.find_tree(tree_oid)?;
    let target = parts[0];

    for entry in &entries {
        if entry.name == target {
            if parts.len() == 1 {
                // Found the file
                return Ok(Some(entry.oid));
            }
            if entry.is_tree() {
                return find_blob_recursive(repo, &entry.oid, &parts[1..]);
            }
            return Ok(None);
        }
    }

    Ok(None)
}

fn build_hunks(repo: &RawRepo, line_owner: &[Option<Oid>]) -> Result<Vec<BlameHunk>, GitError> {
    if line_owner.is_empty() {
        return Ok(Vec::new());
    }

    let mut hunks = Vec::new();
    let mut current_commit = line_owner[0].unwrap_or(Oid::ZERO);
    let mut start = 0u32;
    let mut count = 1u32;

    for (i, owner) in line_owner.iter().enumerate().skip(1) {
        let commit = owner.unwrap_or(Oid::ZERO);
        if commit == current_commit {
            count += 1;
        } else {
            hunks.push(make_hunk(repo, current_commit, start + 1, count)?);
            current_commit = commit;
            start = i as u32;
            count = 1;
        }
    }
    hunks.push(make_hunk(repo, current_commit, start + 1, count)?);

    Ok(hunks)
}

fn make_hunk(
    repo: &RawRepo,
    commit_oid: Oid,
    start_line: u32,
    num_lines: u32,
) -> Result<BlameHunk, GitError> {
    let (author_name, author_email, author_time) = if commit_oid == Oid::ZERO {
        (String::new(), String::new(), 0)
    } else {
        match repo.find_commit(&commit_oid) {
            Ok(c) => (c.author_name, c.author_email, c.author_time),
            Err(_) => (String::new(), String::new(), 0),
        }
    };

    Ok(BlameHunk {
        commit: commit_oid,
        orig_start_line: start_line,
        num_lines,
        author_name,
        author_email,
        author_time,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::process::Command;

    fn create_test_repo() -> tempfile::TempDir {
        let dir = tempfile::tempdir().unwrap();
        let run = |args: &[&str]| {
            Command::new("git")
                .args(args)
                .current_dir(dir.path())
                .env("GIT_AUTHOR_NAME", "Test")
                .env("GIT_AUTHOR_EMAIL", "test@test.com")
                .env("GIT_COMMITTER_NAME", "Test")
                .env("GIT_COMMITTER_EMAIL", "test@test.com")
                .output()
                .expect("git command failed")
        };
        run(&["init"]);
        run(&["config", "user.name", "Test"]);
        run(&["config", "user.email", "test@test.com"]);

        std::fs::write(dir.path().join("test.txt"), "line1\nline2\nline3\n").unwrap();
        run(&["add", "test.txt"]);
        run(&["commit", "-m", "first"]);

        // Second commit modifies line 2
        std::fs::write(dir.path().join("test.txt"), "line1\nmodified\nline3\n").unwrap();
        run(&["add", "test.txt"]);
        run(&["commit", "-m", "second"]);

        dir
    }

    #[test]
    fn test_blame_basic() {
        let dir = create_test_repo();
        let repo = RawRepo::discover(dir.path()).unwrap();
        let hunks = blame_file(&repo, "test.txt").unwrap();
        assert!(!hunks.is_empty());
        let line2_hunk = hunks
            .iter()
            .find(|h| h.orig_start_line <= 2 && h.orig_start_line + h.num_lines > 2)
            .expect("no hunk covers line 2");
        let line1_hunk = hunks
            .iter()
            .find(|h| h.orig_start_line <= 1 && h.orig_start_line + h.num_lines > 1)
            .expect("no hunk covers line 1");
        assert_ne!(
            line2_hunk.commit, line1_hunk.commit,
            "modified line should be a different commit"
        );
    }

    #[test]
    fn test_blame_all_lines_covered() {
        let dir = create_test_repo();
        let repo = RawRepo::discover(dir.path()).unwrap();
        let hunks = blame_file(&repo, "test.txt").unwrap();
        let total_lines: u32 = hunks.iter().map(|h| h.num_lines).sum();
        assert_eq!(total_lines, 3);
    }
}
