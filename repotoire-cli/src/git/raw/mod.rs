pub mod blame;
pub mod commit;
pub mod deflate;
pub mod diff;
pub mod error;
pub mod merge_base;
pub mod object;
pub mod oid;
pub mod pack;
pub mod pack_index;
pub mod repo;
pub mod revwalk;
pub mod sha1;
pub mod tree;

pub use blame::{blame_file, BlameHunk};
pub use commit::RawCommit;
pub use diff::{
    compute_stats, diff_blobs, diff_trees, diff_trees_with_stats, DiffDelta, DiffHunk, DiffStats,
    DiffStatus,
};
pub use error::GitError;
pub use object::ObjectType;
pub use oid::Oid;
pub use repo::RawRepo;
pub use revwalk::RevWalk;
pub use tree::{parse_tree, TreeEntry};

/// Shared test helpers for all raw git submodules.
#[cfg(test)]
pub(crate) mod tests {
    use std::path::PathBuf;

    /// Walk up from CARGO_MANIFEST_DIR to find the nearest `.git/` directory.
    pub fn find_repo_git_dir() -> PathBuf {
        let mut dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
        loop {
            let git = dir.join(".git");
            if git.is_dir() {
                return git;
            }
            if !dir.pop() {
                panic!("not in a git repo");
            }
        }
    }
}
