pub mod error;
pub mod oid;
pub mod sha1;

pub use error::GitError;
pub use oid::Oid;

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
