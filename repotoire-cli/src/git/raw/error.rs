use super::oid::Oid;

#[derive(Debug, thiserror::Error)]
pub enum GitError {
    #[error("object not found: {0}")]
    ObjectNotFound(Oid),
    #[error("corrupt object at {path}: {detail}")]
    CorruptObject { path: String, detail: String },
    #[error("invalid pack file: {0}")]
    InvalidPack(String),
    #[error("ref not found: {0}")]
    RefNotFound(String),
    #[error("not a git repository: {0}")]
    NotAGitRepo(String),
    #[error("decompression failed: {0}")]
    DecompressError(String),
    #[error("delta chain too deep (>{0} levels)")]
    DeltaChainTooDeep(usize),
    #[error(transparent)]
    Io(#[from] std::io::Error),
}
