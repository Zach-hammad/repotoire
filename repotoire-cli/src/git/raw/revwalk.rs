use std::collections::{BinaryHeap, HashSet};

use super::error::GitError;
use super::oid::Oid;
use super::repo::RawRepo;

/// Time-sorted commit traversal using a max-heap keyed by committer_time.
///
/// Matches git2's `Sort::TIME` behavior (sorts by committer_time, not author_time).
pub struct RevWalk<'a> {
    repo: &'a RawRepo,
    heap: BinaryHeap<(i64, Oid)>,
    seen: HashSet<Oid>,
    first_parent_only: bool,
}

impl<'a> RevWalk<'a> {
    pub fn new(repo: &'a RawRepo) -> Self {
        Self {
            repo,
            heap: BinaryHeap::new(),
            seen: HashSet::new(),
            first_parent_only: false,
        }
    }

    /// Push HEAD as the starting point.
    pub fn push_head(&mut self) -> Result<(), GitError> {
        let head = self.repo.resolve_head()?;
        self.push(head)
    }

    /// Push a specific OID as a starting point.
    pub fn push(&mut self, oid: Oid) -> Result<(), GitError> {
        if self.seen.insert(oid) {
            let commit = self.repo.find_commit(&oid)?;
            self.heap.push((commit.committer_time, oid));
        }
        Ok(())
    }

    /// Only follow first parent at each merge (linear history).
    pub fn simplify_first_parent(&mut self) {
        self.first_parent_only = true;
    }
}

impl Iterator for RevWalk<'_> {
    type Item = Result<Oid, GitError>;

    fn next(&mut self) -> Option<Self::Item> {
        let (_, oid) = self.heap.pop()?;

        let commit = match self.repo.find_commit(&oid) {
            Ok(c) => c,
            Err(e) => return Some(Err(e)),
        };

        // Skip parents of shallow commits
        if !self.repo.is_shallow(&oid) {
            let parents = if self.first_parent_only {
                &commit.parents[..commit.parents.len().min(1)]
            } else {
                &commit.parents
            };

            for parent_oid in parents {
                if self.seen.insert(*parent_oid) {
                    match self.repo.find_commit(parent_oid) {
                        Ok(parent) => {
                            self.heap.push((parent.committer_time, *parent_oid));
                        }
                        Err(e) => return Some(Err(e)),
                    }
                }
            }
        }

        Some(Ok(oid))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::Path;

    #[test]
    fn test_revwalk_head() {
        let repo = RawRepo::discover(Path::new(env!("CARGO_MANIFEST_DIR"))).unwrap();
        let mut walk = RevWalk::new(&repo);
        walk.push_head().unwrap();
        let first = walk.next().unwrap().unwrap();
        assert_eq!(first, repo.resolve_head().unwrap());
    }

    #[test]
    fn test_revwalk_time_sorted() {
        let repo = RawRepo::discover(Path::new(env!("CARGO_MANIFEST_DIR"))).unwrap();
        let mut walk = RevWalk::new(&repo);
        walk.push_head().unwrap();
        let mut prev_time = i64::MAX;
        let mut count = 0;
        while let Some(Ok(oid)) = walk.next() {
            let commit = repo.find_commit(&oid).unwrap();
            assert!(
                commit.committer_time <= prev_time,
                "commits not time-sorted"
            );
            prev_time = commit.committer_time;
            count += 1;
            if count >= 20 {
                break;
            }
        }
        assert!(count > 0);
    }

    #[test]
    fn test_revwalk_first_parent() {
        let repo = RawRepo::discover(Path::new(env!("CARGO_MANIFEST_DIR"))).unwrap();
        let mut walk = RevWalk::new(&repo);
        walk.push_head().unwrap();
        walk.simplify_first_parent();
        let mut count = 0;
        while let Some(Ok(_)) = walk.next() {
            count += 1;
            if count >= 50 {
                break;
            }
        }
        assert!(count > 0);
    }
}
