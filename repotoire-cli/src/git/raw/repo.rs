use std::collections::HashSet;
use std::path::{Path, PathBuf};
use std::sync::Mutex;

use super::commit::RawCommit;
use super::error::GitError;
use super::object::{self, ObjectType};
use super::oid::Oid;
use super::pack::Packfile;
use super::pack_index::PackIndex;
use super::tree::{self, TreeEntry};

const MAX_SYMREF_DEPTH: usize = 10;
const CACHE_CAPACITY: usize = 256;

/// A pure-Rust read-only git repository handle.
///
/// Supports loose objects, packfiles, packed-refs, worktrees,
/// alternates, and shallow clones. Thread-safe via `Mutex<LruCache>`.
pub struct RawRepo {
    git_dir: PathBuf,
    common_dir: PathBuf,
    workdir: PathBuf,
    pack_stores: Vec<(PackIndex, Packfile)>,
    packed_refs: Vec<(String, Oid)>,
    shallow_oids: HashSet<Oid>,
    cache: Mutex<LruCache>,
}

struct LruCache {
    entries: Vec<(Oid, ObjectType, Vec<u8>)>,
    total_bytes: usize,
    max_bytes: usize,
}

impl LruCache {
    fn new(max_bytes: usize) -> Self {
        Self {
            entries: Vec::new(),
            total_bytes: 0,
            max_bytes,
        }
    }

    fn get(&mut self, oid: &Oid) -> Option<(ObjectType, Vec<u8>)> {
        if let Some(pos) = self.entries.iter().position(|(o, _, _)| o == oid) {
            let entry = self.entries.remove(pos);
            let result = (entry.1, entry.2.clone());
            self.entries.push(entry);
            Some(result)
        } else {
            None
        }
    }

    fn insert(&mut self, oid: Oid, obj_type: ObjectType, data: Vec<u8>) {
        let size = data.len();
        // Evict oldest entries if needed
        while self.total_bytes + size > self.max_bytes && !self.entries.is_empty() {
            let evicted = self.entries.remove(0);
            self.total_bytes -= evicted.2.len();
        }
        if self.entries.len() >= CACHE_CAPACITY {
            let evicted = self.entries.remove(0);
            self.total_bytes -= evicted.2.len();
        }
        self.total_bytes += size;
        self.entries.push((oid, obj_type, data));
    }
}

impl RawRepo {
    /// Discover a git repository by walking up from the given path.
    pub fn discover(start: &Path) -> Result<Self, GitError> {
        let start = start
            .canonicalize()
            .map_err(|e| GitError::NotAGitRepo(format!("{}: {e}", start.display())))?;
        let mut dir = start.as_path();

        loop {
            let git_path = dir.join(".git");
            if git_path.is_dir() {
                return Self::open_git_dir(git_path, dir.to_path_buf());
            }
            if git_path.is_file() {
                // Worktree: .git file contains "gitdir: <path>"
                let content = std::fs::read_to_string(&git_path).map_err(GitError::Io)?;
                let gitdir = content
                    .strip_prefix("gitdir: ")
                    .ok_or_else(|| {
                        GitError::NotAGitRepo(format!("invalid .git file: {}", git_path.display()))
                    })?
                    .trim();
                let git_dir = if Path::new(gitdir).is_absolute() {
                    PathBuf::from(gitdir)
                } else {
                    dir.join(gitdir)
                };
                return Self::open_git_dir(git_dir, dir.to_path_buf());
            }
            if let Some(parent) = dir.parent() {
                dir = parent;
            } else {
                return Err(GitError::NotAGitRepo(format!(
                    "no .git found from {}",
                    start.display()
                )));
            }
        }
    }

    fn open_git_dir(git_dir: PathBuf, workdir: PathBuf) -> Result<Self, GitError> {
        // Resolve common dir (for worktrees)
        let common_dir = {
            let commondir_file = git_dir.join("commondir");
            if commondir_file.exists() {
                let content = std::fs::read_to_string(&commondir_file).map_err(GitError::Io)?;
                let path = content.trim();
                if Path::new(path).is_absolute() {
                    PathBuf::from(path)
                } else {
                    git_dir.join(path)
                }
            } else {
                git_dir.clone()
            }
        };

        // Check for SHA-256 extension
        let config_path = common_dir.join("config");
        if config_path.exists() {
            let config = std::fs::read_to_string(&config_path).unwrap_or_default();
            if config.contains("objectFormat = sha256") || config.contains("objectformat = sha256")
            {
                return Err(GitError::NotAGitRepo(
                    "SHA-256 repositories are not supported".into(),
                ));
            }
        }

        // Load pack stores
        let pack_dir = common_dir.join("objects/pack");
        let mut pack_stores = Vec::new();
        if pack_dir.is_dir() {
            for entry in std::fs::read_dir(&pack_dir)
                .map_err(GitError::Io)?
                .flatten()
            {
                let path = entry.path();
                if path.extension().and_then(|e| e.to_str()) == Some("idx") {
                    let pack_path = path.with_extension("pack");
                    if pack_path.exists() {
                        let idx = PackIndex::open(&path)?;
                        let pack = Packfile::open(&pack_path)?;
                        pack_stores.push((idx, pack));
                    }
                }
            }
        }

        // Load alternates
        let alternates_path = common_dir.join("objects/info/alternates");
        if alternates_path.exists() {
            let content = std::fs::read_to_string(&alternates_path).unwrap_or_default();
            for line in content.lines() {
                let alt_dir = if Path::new(line).is_absolute() {
                    PathBuf::from(line)
                } else {
                    common_dir.join("objects").join(line)
                };
                let alt_pack_dir = alt_dir.join("pack");
                if alt_pack_dir.is_dir() {
                    for entry in std::fs::read_dir(&alt_pack_dir)
                        .map_err(GitError::Io)?
                        .flatten()
                    {
                        let path = entry.path();
                        if path.extension().and_then(|e| e.to_str()) == Some("idx") {
                            let pack_path = path.with_extension("pack");
                            if pack_path.exists() {
                                let idx = PackIndex::open(&path)?;
                                let pack = Packfile::open(&pack_path)?;
                                pack_stores.push((idx, pack));
                            }
                        }
                    }
                }
            }
        }

        // Parse packed-refs
        let mut packed_refs = Vec::new();
        let packed_refs_path = common_dir.join("packed-refs");
        if packed_refs_path.exists() {
            let content = std::fs::read_to_string(&packed_refs_path).unwrap_or_default();
            for line in content.lines() {
                if line.starts_with('#') || line.starts_with('^') {
                    continue;
                }
                if let Some((hex, refname)) = line.split_once(' ') {
                    if hex.len() == 40 {
                        if let Ok(oid) = Oid::from_hex(hex) {
                            packed_refs.push((refname.to_string(), oid));
                        }
                    }
                }
            }
        }

        // Parse shallow commits
        let mut shallow_oids = HashSet::new();
        let shallow_path = common_dir.join("shallow");
        if shallow_path.exists() {
            let content = std::fs::read_to_string(&shallow_path).unwrap_or_default();
            for line in content.lines() {
                if let Ok(oid) = Oid::from_hex(line.trim()) {
                    shallow_oids.insert(oid);
                }
            }
        }

        Ok(Self {
            git_dir,
            common_dir,
            workdir,
            pack_stores,
            packed_refs,
            shallow_oids,
            cache: Mutex::new(LruCache::new(64 * 1024 * 1024)), // 64MB
        })
    }

    pub fn workdir(&self) -> &Path {
        &self.workdir
    }

    pub fn git_dir(&self) -> &Path {
        &self.git_dir
    }

    pub fn common_dir(&self) -> &Path {
        &self.common_dir
    }

    /// Resolve HEAD to an OID.
    pub fn resolve_head(&self) -> Result<Oid, GitError> {
        let head_path = self.git_dir.join("HEAD");
        let content = std::fs::read_to_string(&head_path).map_err(GitError::Io)?;
        let content = content.trim();

        if let Some(refname) = content.strip_prefix("ref: ") {
            self.resolve_ref(refname)
        } else {
            Oid::from_hex(content)
        }
    }

    /// Resolve a ref name (e.g., "refs/heads/main") to an OID.
    pub fn resolve_ref(&self, refname: &str) -> Result<Oid, GitError> {
        self.resolve_ref_recursive(refname, 0)
    }

    fn resolve_ref_recursive(&self, refname: &str, depth: usize) -> Result<Oid, GitError> {
        if depth > MAX_SYMREF_DEPTH {
            return Err(GitError::RefNotFound(format!(
                "symref loop: {refname}"
            )));
        }

        // Check loose ref
        let ref_path = self.common_dir.join(refname);
        if ref_path.exists() {
            let content = std::fs::read_to_string(&ref_path).map_err(GitError::Io)?;
            let content = content.trim();
            if let Some(target) = content.strip_prefix("ref: ") {
                return self.resolve_ref_recursive(target, depth + 1);
            }
            return Oid::from_hex(content);
        }

        // Check packed-refs
        for (name, oid) in &self.packed_refs {
            if name == refname {
                return Ok(*oid);
            }
        }

        Err(GitError::RefNotFound(refname.to_string()))
    }

    /// Read a raw git object by OID.
    pub fn find_object(&self, oid: &Oid) -> Result<(ObjectType, Vec<u8>), GitError> {
        // Check cache
        {
            let mut cache = self.cache.lock().expect("cache lock poisoned");
            if let Some(result) = cache.get(oid) {
                return Ok(result);
            }
        }

        // Try loose objects
        let objects_dir = self.common_dir.join("objects");
        match object::read_loose_object(&objects_dir, oid) {
            Ok(result) => {
                self.cache_object(oid, &result);
                return Ok(result);
            }
            Err(GitError::ObjectNotFound(_)) => {}
            Err(e) => return Err(e),
        }

        // Try pack stores
        for (idx, pack) in &self.pack_stores {
            if let Some(offset) = idx.find(oid) {
                let result = pack.read_object_at(offset, idx)?;
                self.cache_object(oid, &result);
                return Ok(result);
            }
        }

        Err(GitError::ObjectNotFound(*oid))
    }

    fn cache_object(&self, oid: &Oid, result: &(ObjectType, Vec<u8>)) {
        // Only cache commits and trees (small, frequently accessed)
        if matches!(result.0, ObjectType::Commit | ObjectType::Tree) {
            let mut cache = self.cache.lock().expect("cache lock poisoned");
            cache.insert(*oid, result.0, result.1.clone());
        }
    }

    /// Parse a commit object by OID.
    pub fn find_commit(&self, oid: &Oid) -> Result<RawCommit, GitError> {
        let (obj_type, data) = self.find_object(oid)?;
        match obj_type {
            ObjectType::Commit => RawCommit::parse(&data),
            ObjectType::Tag => {
                // Peel tag to commit
                let text = std::str::from_utf8(&data).map_err(|_| GitError::CorruptObject {
                    path: String::new(),
                    detail: "non-UTF8 tag".into(),
                })?;
                for line in text.lines() {
                    if let Some(hex) = line.strip_prefix("object ") {
                        let target = Oid::from_hex(hex.trim())?;
                        return self.find_commit(&target);
                    }
                }
                Err(GitError::CorruptObject {
                    path: String::new(),
                    detail: "tag without object field".into(),
                })
            }
            _ => Err(GitError::CorruptObject {
                path: String::new(),
                detail: format!("expected commit, got {obj_type:?}"),
            }),
        }
    }

    /// Parse a tree object by OID.
    pub fn find_tree(&self, oid: &Oid) -> Result<Vec<TreeEntry>, GitError> {
        let (obj_type, data) = self.find_object(oid)?;
        if obj_type != ObjectType::Tree {
            return Err(GitError::CorruptObject {
                path: String::new(),
                detail: format!("expected tree, got {obj_type:?}"),
            });
        }
        tree::parse_tree(&data)
    }

    /// Read a blob object by OID.
    pub fn find_blob(&self, oid: &Oid) -> Result<Vec<u8>, GitError> {
        let (obj_type, data) = self.find_object(oid)?;
        if obj_type != ObjectType::Blob {
            return Err(GitError::CorruptObject {
                path: String::new(),
                detail: format!("expected blob, got {obj_type:?}"),
            });
        }
        Ok(data)
    }

    /// Resolve HEAD and return its tree entries.
    pub fn head_tree(&self) -> Result<(Oid, Vec<TreeEntry>), GitError> {
        let head = self.resolve_head()?;
        let commit = self.find_commit(&head)?;
        let entries = self.find_tree(&commit.tree_oid)?;
        Ok((commit.tree_oid, entries))
    }

    /// Walk first-parent chain from HEAD to find the root commit.
    pub fn find_root_commit(&self) -> Result<Oid, GitError> {
        let mut current = self.resolve_head()?;
        loop {
            let commit = self.find_commit(&current)?;
            if commit.parents.is_empty() || self.shallow_oids.contains(&current) {
                return Ok(current);
            }
            current = commit.parents[0];
        }
    }

    /// Check if a commit is a shallow boundary.
    pub fn is_shallow(&self, oid: &Oid) -> bool {
        self.shallow_oids.contains(oid)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_discover_repo() {
        let repo = RawRepo::discover(Path::new(env!("CARGO_MANIFEST_DIR"))).unwrap();
        assert!(repo.workdir().exists());
    }

    #[test]
    fn test_resolve_head() {
        let repo = RawRepo::discover(Path::new(env!("CARGO_MANIFEST_DIR"))).unwrap();
        let head_oid = repo.resolve_head().unwrap();
        assert_ne!(head_oid, Oid::ZERO);
    }

    #[test]
    fn test_find_commit() {
        let repo = RawRepo::discover(Path::new(env!("CARGO_MANIFEST_DIR"))).unwrap();
        let head_oid = repo.resolve_head().unwrap();
        let commit = repo.find_commit(&head_oid).unwrap();
        assert_ne!(commit.tree_oid, Oid::ZERO);
        assert!(!commit.author_name.is_empty());
    }

    #[test]
    fn test_find_tree() {
        let repo = RawRepo::discover(Path::new(env!("CARGO_MANIFEST_DIR"))).unwrap();
        let head_oid = repo.resolve_head().unwrap();
        let commit = repo.find_commit(&head_oid).unwrap();
        let entries = repo.find_tree(&commit.tree_oid).unwrap();
        assert!(!entries.is_empty());
    }

    #[test]
    fn test_head_tree() {
        let repo = RawRepo::discover(Path::new(env!("CARGO_MANIFEST_DIR"))).unwrap();
        let (_oid, entries) = repo.head_tree().unwrap();
        assert!(!entries.is_empty());
    }

    #[test]
    fn test_not_a_repo() {
        let result = RawRepo::discover(Path::new("/tmp"));
        assert!(result.is_err());
    }

    #[test]
    fn test_find_root_commit() {
        let repo = RawRepo::discover(Path::new(env!("CARGO_MANIFEST_DIR"))).unwrap();
        let root = repo.find_root_commit().unwrap();
        let commit = repo.find_commit(&root).unwrap();
        assert!(commit.parents.is_empty());
    }

    #[test]
    fn test_detached_head() {
        let dir = tempfile::tempdir().unwrap();
        let run = |args: &[&str]| {
            std::process::Command::new("git")
                .args(args)
                .current_dir(dir.path())
                .env("GIT_AUTHOR_NAME", "Test")
                .env("GIT_AUTHOR_EMAIL", "t@t.com")
                .env("GIT_COMMITTER_NAME", "Test")
                .env("GIT_COMMITTER_EMAIL", "t@t.com")
                .output()
                .expect("git command failed")
        };
        run(&["init"]);
        run(&["config", "user.name", "Test"]);
        run(&["config", "user.email", "t@t.com"]);
        std::fs::write(dir.path().join("f.txt"), "x").unwrap();
        run(&["add", "."]);
        run(&["commit", "-m", "init"]);
        run(&["checkout", "--detach", "HEAD"]);

        let repo = RawRepo::discover(dir.path()).unwrap();
        let head = repo.resolve_head().unwrap();
        assert_ne!(head, Oid::ZERO);
    }

    #[test]
    fn test_empty_repo() {
        let dir = tempfile::tempdir().unwrap();
        std::process::Command::new("git")
            .args(["init"])
            .current_dir(dir.path())
            .output()
            .unwrap();
        let repo = RawRepo::discover(dir.path()).unwrap();
        assert!(repo.resolve_head().is_err());
    }

    #[test]
    fn test_sha256_detection() {
        let dir = tempfile::tempdir().unwrap();
        std::process::Command::new("git")
            .args(["init"])
            .current_dir(dir.path())
            .output()
            .unwrap();
        let config_path = dir.path().join(".git/config");
        let mut config = std::fs::read_to_string(&config_path).unwrap();
        config.push_str("\n[extensions]\n\tobjectFormat = sha256\n");
        std::fs::write(&config_path, config).unwrap();
        let result = RawRepo::discover(dir.path());
        assert!(result.is_err(), "should reject SHA-256 repos");
    }
}
