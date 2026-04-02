use std::collections::BTreeMap;

use super::error::GitError;
use super::oid::Oid;
use super::repo::RawRepo;
use super::tree::TreeEntry;

// ── Myers diff ──────────────────────────────────────────────────────────────

#[derive(Debug, Clone)]
pub struct DiffHunk {
    pub old_start: usize,
    pub old_lines: usize,
    pub new_start: usize,
    pub new_lines: usize,
}

#[derive(Debug, Clone, Default)]
pub struct DiffStats {
    pub files_changed: usize,
    pub insertions: usize,
    pub deletions: usize,
}

/// Split bytes into lines for diffing.
fn split_lines(data: &[u8]) -> Vec<&[u8]> {
    if data.is_empty() {
        return Vec::new();
    }
    data.split(|&b| b == b'\n')
        .collect::<Vec<_>>()
        .into_iter()
        .filter(|l| !l.is_empty() || data.last() != Some(&b'\n'))
        .collect()
}

/// Myers O(ND) diff algorithm. Returns hunks with zero context.
pub fn diff_blobs(old: &[u8], new: &[u8]) -> Vec<DiffHunk> {
    let old_lines = split_lines(old);
    let new_lines = split_lines(new);
    diff_lines(&old_lines, &new_lines)
}

fn diff_lines(old: &[&[u8]], new: &[&[u8]]) -> Vec<DiffHunk> {
    let n = old.len();
    let m = new.len();

    if n == 0 && m == 0 {
        return Vec::new();
    }
    if n == 0 {
        return vec![DiffHunk {
            old_start: 1,
            old_lines: 0,
            new_start: 1,
            new_lines: m,
        }];
    }
    if m == 0 {
        return vec![DiffHunk {
            old_start: 1,
            old_lines: n,
            new_start: 1,
            new_lines: 0,
        }];
    }

    // Myers O(ND) forward algorithm with full trace
    let max_d = n + m;
    let offset = max_d as i64;
    let size = 2 * max_d + 1;
    let mut v = vec![0usize; size];
    let mut trace: Vec<Vec<usize>> = Vec::new();

    for d in 0..=max_d {
        trace.push(v.clone());
        for k in (-(d as i64)..=(d as i64)).step_by(2) {
            let idx = (k + offset) as usize;
            let mut x = if k == -(d as i64) || (k != d as i64 && v[idx - 1] < v[idx + 1]) {
                v[idx + 1]
            } else {
                v[idx - 1] + 1
            };
            let mut y = (x as i64 - k) as usize;

            while x < n && y < m && old[x] == new[y] {
                x += 1;
                y += 1;
            }

            v[idx] = x;

            if x >= n && y >= m {
                // Backtrack to build edit script
                let edits = backtrack_myers(&trace, n, m, d, offset);
                return edits_to_hunks(&edits, n, m);
            }
        }
    }

    // Fallback: treat everything as changed
    vec![DiffHunk {
        old_start: 1,
        old_lines: n,
        new_start: 1,
        new_lines: m,
    }]
}

#[derive(Debug, Clone, Copy, PartialEq)]
enum Edit {
    Equal,
    Insert,
    Delete,
}

fn backtrack_myers(trace: &[Vec<usize>], n: usize, m: usize, d_final: usize, offset: i64) -> Vec<Edit> {
    let mut edits = Vec::new();
    let mut x = n;
    let mut y = m;

    for d in (0..=d_final).rev() {
        let v = &trace[d];
        let k = x as i64 - y as i64;

        let prev_k = if k == -(d as i64) || (k != d as i64 && v[(k - 1 + offset) as usize] < v[(k + 1 + offset) as usize]) {
            k + 1  // came from above (insert)
        } else {
            k - 1  // came from left (delete)
        };

        let prev_x = v[(prev_k + offset) as usize];
        let prev_y = (prev_x as i64 - prev_k) as usize;

        // Diagonal (equal) moves after the edit
        while x > prev_x && y > prev_y {
            edits.push(Edit::Equal);
            x -= 1;
            y -= 1;
        }

        // The actual edit
        if d > 0 {
            if prev_k > k {
                // Came from k+1: insert (y increased)
                edits.push(Edit::Insert);
                y -= 1;
            } else {
                // Came from k-1: delete (x increased)
                edits.push(Edit::Delete);
                x -= 1;
            }
        }
    }

    // Any remaining diagonals at the start
    while x > 0 && y > 0 {
        edits.push(Edit::Equal);
        x -= 1;
        y -= 1;
    }

    edits.reverse();
    edits
}

fn edits_to_hunks(edits: &[Edit], _n: usize, _m: usize) -> Vec<DiffHunk> {
    let mut hunks = Vec::new();
    let mut old_pos = 1usize;
    let mut new_pos = 1usize;
    let mut i = 0;

    while i < edits.len() {
        match edits[i] {
            Edit::Equal => {
                old_pos += 1;
                new_pos += 1;
                i += 1;
            }
            Edit::Insert | Edit::Delete => {
                let hunk_old_start = old_pos;
                let hunk_new_start = new_pos;
                let mut old_count = 0;
                let mut new_count = 0;

                while i < edits.len() && edits[i] != Edit::Equal {
                    match edits[i] {
                        Edit::Delete => {
                            old_count += 1;
                            old_pos += 1;
                        }
                        Edit::Insert => {
                            new_count += 1;
                            new_pos += 1;
                        }
                        Edit::Equal => unreachable!(),
                    }
                    i += 1;
                }

                hunks.push(DiffHunk {
                    old_start: hunk_old_start,
                    old_lines: old_count,
                    new_start: hunk_new_start,
                    new_lines: new_count,
                });
            }
        }
    }

    hunks
}

pub fn compute_stats(hunks: &[DiffHunk]) -> DiffStats {
    let mut stats = DiffStats::default();
    for hunk in hunks {
        stats.insertions += hunk.new_lines;
        stats.deletions += hunk.old_lines;
    }
    stats
}

// ── Tree-to-tree diff ───────────────────────────────────────────────────────

#[derive(Debug, Clone)]
pub struct DiffDelta {
    pub old_path: String,
    pub new_path: String,
    pub status: DiffStatus,
    pub old_oid: Oid,
    pub new_oid: Oid,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DiffStatus {
    Added,
    Deleted,
    Modified,
}

/// Diff two trees, returning changed file paths.
/// Skips submodules (mode 160000). Optionally filters by pathspecs.
pub fn diff_trees(
    repo: &RawRepo,
    old_tree: &Oid,
    new_tree: &Oid,
    pathspecs: &[String],
) -> Result<Vec<DiffDelta>, GitError> {
    let mut deltas = Vec::new();
    diff_trees_recursive(repo, old_tree, new_tree, "", pathspecs, &mut deltas)?;
    Ok(deltas)
}

fn diff_trees_recursive(
    repo: &RawRepo,
    old_tree: &Oid,
    new_tree: &Oid,
    prefix: &str,
    pathspecs: &[String],
    deltas: &mut Vec<DiffDelta>,
) -> Result<(), GitError> {
    let old_entries = if *old_tree == Oid::ZERO {
        Vec::new()
    } else {
        repo.find_tree(old_tree)?
    };
    let new_entries = if *new_tree == Oid::ZERO {
        Vec::new()
    } else {
        repo.find_tree(new_tree)?
    };

    // Build maps keyed by name
    let old_map: BTreeMap<&str, &TreeEntry> = old_entries.iter().map(|e| (e.name.as_str(), e)).collect();
    let new_map: BTreeMap<&str, &TreeEntry> = new_entries.iter().map(|e| (e.name.as_str(), e)).collect();

    // Merge-walk by name
    let mut all_names: Vec<&str> = old_map.keys().chain(new_map.keys()).copied().collect();
    all_names.sort_unstable();
    all_names.dedup();

    for name in all_names {
        let path = if prefix.is_empty() {
            name.to_string()
        } else {
            format!("{prefix}/{name}")
        };

        // Skip submodules
        let old_entry = old_map.get(name);
        let new_entry = new_map.get(name);
        if old_entry.is_some_and(|e| e.is_submodule())
            || new_entry.is_some_and(|e| e.is_submodule())
        {
            continue;
        }

        // Pathspec filtering
        if !pathspecs.is_empty()
            && !pathspecs
                .iter()
                .any(|ps| path == *ps || ps.starts_with(&format!("{path}/")) || path.starts_with(&format!("{ps}/")))
        {
            continue;
        }

        match (old_entry, new_entry) {
            (None, Some(new)) => {
                if new.is_tree() {
                    diff_trees_recursive(repo, &Oid::ZERO, &new.oid, &path, pathspecs, deltas)?;
                } else {
                    deltas.push(DiffDelta {
                        old_path: path.clone(),
                        new_path: path,
                        status: DiffStatus::Added,
                        old_oid: Oid::ZERO,
                        new_oid: new.oid,
                    });
                }
            }
            (Some(old), None) => {
                if old.is_tree() {
                    diff_trees_recursive(repo, &old.oid, &Oid::ZERO, &path, pathspecs, deltas)?;
                } else {
                    deltas.push(DiffDelta {
                        old_path: path.clone(),
                        new_path: path,
                        status: DiffStatus::Deleted,
                        old_oid: old.oid,
                        new_oid: Oid::ZERO,
                    });
                }
            }
            (Some(old), Some(new)) => {
                if old.oid == new.oid {
                    continue; // OID match — no change
                }
                if old.is_tree() && new.is_tree() {
                    diff_trees_recursive(repo, &old.oid, &new.oid, &path, pathspecs, deltas)?;
                } else if old.is_tree() || new.is_tree() {
                    // Type change (tree -> blob or blob -> tree)
                    if old.is_tree() {
                        diff_trees_recursive(
                            repo,
                            &old.oid,
                            &Oid::ZERO,
                            &path,
                            pathspecs,
                            deltas,
                        )?;
                    } else {
                        deltas.push(DiffDelta {
                            old_path: path.clone(),
                            new_path: path.clone(),
                            status: DiffStatus::Deleted,
                            old_oid: old.oid,
                            new_oid: Oid::ZERO,
                        });
                    }
                    if new.is_tree() {
                        diff_trees_recursive(
                            repo,
                            &Oid::ZERO,
                            &new.oid,
                            &path,
                            pathspecs,
                            deltas,
                        )?;
                    } else {
                        deltas.push(DiffDelta {
                            old_path: path.clone(),
                            new_path: path,
                            status: DiffStatus::Added,
                            old_oid: Oid::ZERO,
                            new_oid: new.oid,
                        });
                    }
                } else {
                    // Both blobs, different OID
                    deltas.push(DiffDelta {
                        old_path: path.clone(),
                        new_path: path,
                        status: DiffStatus::Modified,
                        old_oid: old.oid,
                        new_oid: new.oid,
                    });
                }
            }
            (None, None) => unreachable!(),
        }
    }

    Ok(())
}

/// Diff two trees and also run Myers on modified blobs for insertion/deletion counts.
pub fn diff_trees_with_stats(
    repo: &RawRepo,
    old_tree: &Oid,
    new_tree: &Oid,
) -> Result<(Vec<DiffDelta>, DiffStats), GitError> {
    let deltas = diff_trees(repo, old_tree, new_tree, &[])?;
    let mut total_stats = DiffStats {
        files_changed: deltas.len(),
        ..Default::default()
    };

    for delta in &deltas {
        match delta.status {
            DiffStatus::Added => {
                let blob = repo.find_blob(&delta.new_oid)?;
                let lines = split_lines(&blob).len();
                total_stats.insertions += lines;
            }
            DiffStatus::Deleted => {
                let blob = repo.find_blob(&delta.old_oid)?;
                let lines = split_lines(&blob).len();
                total_stats.deletions += lines;
            }
            DiffStatus::Modified => {
                let old_blob = repo.find_blob(&delta.old_oid)?;
                let new_blob = repo.find_blob(&delta.new_oid)?;
                let hunks = diff_blobs(&old_blob, &new_blob);
                let stats = compute_stats(&hunks);
                total_stats.insertions += stats.insertions;
                total_stats.deletions += stats.deletions;
            }
        }
    }

    Ok((deltas, total_stats))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::Path;

    #[test]
    fn test_myers_identical() {
        let hunks = diff_blobs(b"hello\nworld\n", b"hello\nworld\n");
        assert!(hunks.is_empty());
    }

    #[test]
    fn test_myers_insertion() {
        let hunks = diff_blobs(b"a\nc\n", b"a\nb\nc\n");
        assert_eq!(hunks.len(), 1);
        assert_eq!(hunks[0].new_start, 2);
        assert_eq!(hunks[0].new_lines, 1);
        assert_eq!(hunks[0].old_lines, 0);
    }

    #[test]
    fn test_myers_deletion() {
        let hunks = diff_blobs(b"a\nb\nc\n", b"a\nc\n");
        assert_eq!(hunks.len(), 1);
        assert_eq!(hunks[0].old_start, 2);
        assert_eq!(hunks[0].old_lines, 1);
        assert_eq!(hunks[0].new_lines, 0);
    }

    #[test]
    fn test_myers_modification() {
        let hunks = diff_blobs(b"a\nb\nc\n", b"a\nB\nc\n");
        assert_eq!(hunks.len(), 1);
        assert_eq!(hunks[0].old_lines, 1);
        assert_eq!(hunks[0].new_lines, 1);
    }

    #[test]
    fn test_myers_empty_to_content() {
        let hunks = diff_blobs(b"", b"a\nb\n");
        assert_eq!(hunks.len(), 1);
        assert_eq!(hunks[0].new_lines, 2);
    }

    #[test]
    fn test_diff_stats() {
        let hunks = diff_blobs(b"a\nb\nc\n", b"a\nB\nc\nd\n");
        let stats = compute_stats(&hunks);
        assert_eq!(stats.insertions, 2); // B + d
        assert_eq!(stats.deletions, 1); // b
    }

    #[test]
    fn test_tree_diff_real_repo() {
        let repo = RawRepo::discover(Path::new(env!("CARGO_MANIFEST_DIR"))).unwrap();
        let head = repo.resolve_head().unwrap();
        let commit = repo.find_commit(&head).unwrap();
        if let Some(parent_oid) = commit.parents.first() {
            let parent = repo.find_commit(parent_oid).unwrap();
            let deltas = diff_trees(&repo, &parent.tree_oid, &commit.tree_oid, &[]).unwrap();
            for delta in &deltas {
                assert!(!delta.new_path.is_empty());
            }
        }
    }

    #[test]
    fn test_tree_diff_with_pathspec() {
        let repo = RawRepo::discover(Path::new(env!("CARGO_MANIFEST_DIR"))).unwrap();
        let head = repo.resolve_head().unwrap();
        let commit = repo.find_commit(&head).unwrap();
        if let Some(parent_oid) = commit.parents.first() {
            let parent = repo.find_commit(parent_oid).unwrap();
            let pathspecs = vec!["src/main.rs".to_string()];
            let deltas =
                diff_trees(&repo, &parent.tree_oid, &commit.tree_oid, &pathspecs).unwrap();
            for delta in &deltas {
                assert_eq!(delta.new_path, "src/main.rs");
            }
        }
    }
}
