use super::error::GitError;
use super::oid::Oid;
use super::repo::RawRepo;
use std::collections::{HashMap, VecDeque};

/// Find the merge-base (lowest common ancestor) of two commits using BFS
/// coloring. Returns the most recent common ancestor by committer time, or
/// `None` if the histories are disjoint.
pub fn merge_base(repo: &RawRepo, oid_a: &Oid, oid_b: &Oid) -> Result<Option<Oid>, GitError> {
    if oid_a == oid_b {
        return Ok(Some(*oid_a));
    }

    const COLOR_A: u8 = 1;
    const COLOR_B: u8 = 2;
    const COLOR_BOTH: u8 = 3;

    let mut colors: HashMap<Oid, u8> = HashMap::new();
    let mut queue: VecDeque<Oid> = VecDeque::new();

    colors.insert(*oid_a, COLOR_A);
    colors.insert(*oid_b, COLOR_B);
    queue.push_back(*oid_a);
    queue.push_back(*oid_b);

    let mut best: Option<(Oid, i64)> = None;

    while let Some(oid) = queue.pop_front() {
        let color = *colors.get(&oid).unwrap_or(&0);
        if color == COLOR_BOTH {
            let commit = repo.find_commit(&oid)?;
            match best {
                None => best = Some((oid, commit.committer_time)),
                Some((_, best_time)) if commit.committer_time > best_time => {
                    best = Some((oid, commit.committer_time));
                }
                _ => {}
            }
            continue;
        }
        let commit = repo.find_commit(&oid)?;
        for parent_oid in &commit.parents {
            let parent_color = colors.entry(*parent_oid).or_insert(0);
            let new_color = *parent_color | color;
            if new_color != *parent_color {
                *parent_color = new_color;
                queue.push_back(*parent_oid);
            }
        }
    }
    Ok(best.map(|(oid, _)| oid))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::Path;
    use std::process::Command;

    /// Helper: run a git command in the given directory, returning stdout.
    fn git(dir: &Path, args: &[&str]) -> String {
        let out = Command::new("git")
            .args(args)
            .current_dir(dir)
            .env("GIT_AUTHOR_NAME", "Test")
            .env("GIT_AUTHOR_EMAIL", "t@t.com")
            .env("GIT_COMMITTER_NAME", "Test")
            .env("GIT_COMMITTER_EMAIL", "t@t.com")
            .output()
            .expect("git command failed");
        String::from_utf8_lossy(&out.stdout).trim().to_string()
    }

    /// Initialize a temp repo with one commit and return (tempdir, repo, HEAD oid).
    fn init_repo() -> (tempfile::TempDir, RawRepo, Oid) {
        let dir = tempfile::tempdir().unwrap();
        git(dir.path(), &["init"]);
        git(dir.path(), &["config", "user.name", "Test"]);
        git(dir.path(), &["config", "user.email", "t@t.com"]);
        std::fs::write(dir.path().join("f.txt"), "init").unwrap();
        git(dir.path(), &["add", "."]);
        git(dir.path(), &["commit", "-m", "init"]);
        let head_hex = git(dir.path(), &["rev-parse", "HEAD"]);
        let repo = RawRepo::discover(dir.path()).unwrap();
        let oid = Oid::from_hex(&head_hex).unwrap();
        (dir, repo, oid)
    }

    #[test]
    fn test_merge_base_same_commit() {
        let (_dir, repo, head) = init_repo();
        let result = merge_base(&repo, &head, &head).unwrap();
        assert_eq!(result, Some(head));
    }

    #[test]
    fn test_merge_base_linear_history() {
        let dir = tempfile::tempdir().unwrap();
        git(dir.path(), &["init"]);
        git(dir.path(), &["config", "user.name", "Test"]);
        git(dir.path(), &["config", "user.email", "t@t.com"]);

        std::fs::write(dir.path().join("f.txt"), "1").unwrap();
        git(dir.path(), &["add", "."]);
        git(dir.path(), &["commit", "-m", "c1"]);
        let c1_hex = git(dir.path(), &["rev-parse", "HEAD"]);

        std::fs::write(dir.path().join("f.txt"), "2").unwrap();
        git(dir.path(), &["add", "."]);
        git(dir.path(), &["commit", "-m", "c2"]);

        std::fs::write(dir.path().join("f.txt"), "3").unwrap();
        git(dir.path(), &["add", "."]);
        git(dir.path(), &["commit", "-m", "c3"]);
        let c3_hex = git(dir.path(), &["rev-parse", "HEAD"]);

        let repo = RawRepo::discover(dir.path()).unwrap();
        let c1 = Oid::from_hex(&c1_hex).unwrap();
        let c3 = Oid::from_hex(&c3_hex).unwrap();

        // merge-base of c1 and c3 in a linear chain should be c1
        let result = merge_base(&repo, &c1, &c3).unwrap();
        assert_eq!(result, Some(c1));
    }

    #[test]
    fn test_merge_base_diverged_branches() {
        let dir = tempfile::tempdir().unwrap();
        git(dir.path(), &["init"]);
        git(dir.path(), &["config", "user.name", "Test"]);
        git(dir.path(), &["config", "user.email", "t@t.com"]);

        // Create base commit
        std::fs::write(dir.path().join("f.txt"), "base").unwrap();
        git(dir.path(), &["add", "."]);
        git(dir.path(), &["commit", "-m", "base"]);
        let base_hex = git(dir.path(), &["rev-parse", "HEAD"]);

        // Branch A
        git(dir.path(), &["checkout", "-b", "branch-a"]);
        std::fs::write(dir.path().join("a.txt"), "a").unwrap();
        git(dir.path(), &["add", "."]);
        git(dir.path(), &["commit", "-m", "a1"]);
        let a_hex = git(dir.path(), &["rev-parse", "HEAD"]);

        // Branch B (from base)
        git(dir.path(), &["checkout", &base_hex]);
        git(dir.path(), &["checkout", "-b", "branch-b"]);
        std::fs::write(dir.path().join("b.txt"), "b").unwrap();
        git(dir.path(), &["add", "."]);
        git(dir.path(), &["commit", "-m", "b1"]);
        let b_hex = git(dir.path(), &["rev-parse", "HEAD"]);

        let repo = RawRepo::discover(dir.path()).unwrap();
        let oid_a = Oid::from_hex(&a_hex).unwrap();
        let oid_b = Oid::from_hex(&b_hex).unwrap();
        let base_oid = Oid::from_hex(&base_hex).unwrap();

        let result = merge_base(&repo, &oid_a, &oid_b).unwrap();
        assert_eq!(result, Some(base_oid));
    }
}
