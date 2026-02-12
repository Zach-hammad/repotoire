//! Cache path utilities - uses ~/.cache/repotoire/<repo-hash>/ instead of .repotoire/

use std::path::{Path, PathBuf};

/// Get the cache directory for a repository.
/// Uses ~/.cache/repotoire/<repo-hash>/ on Unix, %LOCALAPPDATA%/repotoire/<repo-hash>/ on Windows.
pub fn get_cache_dir(repo_path: &Path) -> PathBuf {
    let repo_hash = hash_path(repo_path);

    let base = if cfg!(windows) {
        std::env::var("LOCALAPPDATA")
            .map(PathBuf::from)
            .unwrap_or_else(|_| dirs::cache_dir().unwrap_or_else(|| PathBuf::from(".")))
    } else {
        dirs::cache_dir().unwrap_or_else(|| {
            // Fallback to ~/.cache
            dirs::home_dir()
                .map(|h| h.join(".cache"))
                .unwrap_or_else(|| PathBuf::from("."))
        })
    };

    base.join("repotoire").join(&repo_hash)
}

/// Get the git cache file path for a repository.
pub fn get_git_cache_path(repo_path: &Path) -> PathBuf {
    get_cache_dir(repo_path).join("git_cache.json")
}

/// Get the findings cache file path for a repository.
pub fn get_findings_cache_path(repo_path: &Path) -> PathBuf {
    get_cache_dir(repo_path).join("last_findings.json")
}

/// Get the graph database path for a repository.
pub fn get_graph_db_path(repo_path: &Path) -> PathBuf {
    get_cache_dir(repo_path).join("graph_db")
}

/// Get the graph stats cache file path for a repository.
pub fn get_graph_stats_path(repo_path: &Path) -> PathBuf {
    get_cache_dir(repo_path).join("graph_stats.json")
}

/// Hash a path to create a unique but deterministic directory name.
/// Uses the canonical path to ensure consistency.
fn hash_path(path: &Path) -> String {
    use std::collections::hash_map::DefaultHasher;
    use std::hash::{Hash, Hasher};

    let canonical = path.canonicalize().unwrap_or_else(|_| path.to_path_buf());
    let path_str = canonical.to_string_lossy();

    let mut hasher = DefaultHasher::new();
    path_str.hash(&mut hasher);
    let hash = hasher.finish();

    // Use canonical path's file_name for consistent naming (important when path is ".")
    let repo_name = canonical
        .file_name()
        .and_then(|n| n.to_str())
        .unwrap_or("repo")
        .chars()
        .filter(|c| c.is_alphanumeric() || *c == '-' || *c == '_')
        .take(20)
        .collect::<String>();

    format!("{}-{:012x}", repo_name, hash)
}

/// Ensure the cache directory exists.
pub fn ensure_cache_dir(repo_path: &Path) -> std::io::Result<PathBuf> {
    let cache_dir = get_cache_dir(repo_path);
    std::fs::create_dir_all(&cache_dir)?;
    Ok(cache_dir)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_hash_path_deterministic() {
        let path = Path::new("/tmp/test-repo");
        let hash1 = hash_path(path);
        let hash2 = hash_path(path);
        assert_eq!(hash1, hash2);
    }

    #[test]
    fn test_cache_dir_format() {
        let path = Path::new("/home/user/my-project");
        let cache = get_cache_dir(path);
        assert!(cache.to_string_lossy().contains("repotoire"));
        assert!(cache.to_string_lossy().contains("my-project"));
    }
}
