//! Repo shape detection — classifies a repository's layout into one of four
//! shapes based on workspace markers and independently-buildable roots found
//! within the first two directory levels.

#![cfg_attr(not(test), deny(clippy::unwrap_used))]

use serde::Serialize;
use std::fs;
use std::path::Path;

// ---------------------------------------------------------------------------
// Public struct
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Serialize, Default)]
pub struct RepoShapeInfo {
    /// One of "monorepo", "workspace", "multi-package", "single-package"
    pub repo_shape: String,
    pub has_workspace: bool,
    pub workspace_member_count: u32,
    pub buildable_roots: u32,
    /// Populated later by the analyze command from LOC data; defaults to 0.
    pub language_count: u32,
    /// Populated later by the analyze command from LOC data; defaults to 0.0.
    pub primary_language_ratio: f64,
}

// ---------------------------------------------------------------------------
// Detection
// ---------------------------------------------------------------------------

/// Detect the shape of the repository rooted at `path`.
///
/// Walks at most **two levels deep** (root + one child level) to keep the
/// scan cheap even on large repositories.
pub fn detect_repo_shape(path: &Path) -> RepoShapeInfo {
    let has_workspace = check_workspace_markers(path);
    let buildable_roots = count_buildable_roots(path);

    let repo_shape = classify(has_workspace, buildable_roots);

    RepoShapeInfo {
        repo_shape,
        has_workspace,
        workspace_member_count: 0, // not needed for shape classification
        buildable_roots,
        language_count: 0,
        primary_language_ratio: 0.0,
    }
}

// ---------------------------------------------------------------------------
// Internal helpers
// ---------------------------------------------------------------------------

/// Returns `true` if any workspace-marker file is found at `root`.
fn check_workspace_markers(root: &Path) -> bool {
    // Cargo workspace
    if has_cargo_workspace(root) {
        return true;
    }
    // pnpm
    if root.join("pnpm-workspace.yaml").is_file() {
        return true;
    }
    // Lerna
    if root.join("lerna.json").is_file() {
        return true;
    }
    // Go workspace
    if root.join("go.work").is_file() {
        return true;
    }
    false
}

/// Returns `true` when `Cargo.toml` at `dir` contains a `[workspace]` section.
fn has_cargo_workspace(dir: &Path) -> bool {
    let cargo_toml = dir.join("Cargo.toml");
    if !cargo_toml.is_file() {
        return false;
    }
    fs::read_to_string(&cargo_toml)
        .map(|contents| contents.contains("[workspace]"))
        .unwrap_or(false)
}

/// Count distinct directory subtrees that contain independent build files.
///
/// A subtree is considered independently buildable when it has one of:
/// - `Cargo.toml` containing `[package]`
/// - `package.json` containing `"scripts"`
/// - `go.mod`
/// - `pyproject.toml` containing `[build-system]` or `[project]`
///
/// Scanning is limited to root + one child level (depth 0 and 1).
fn count_buildable_roots(root: &Path) -> u32 {
    let mut count = 0u32;

    // Level 0: the root itself
    if is_buildable_root(root) {
        count += 1;
    }

    // Level 1: immediate children
    let read_dir = match fs::read_dir(root) {
        Ok(rd) => rd,
        Err(_) => return count,
    };

    for entry in read_dir.flatten() {
        let child = entry.path();
        if !child.is_dir() {
            continue;
        }
        if is_buildable_root(&child) {
            count += 1;
        }
    }

    count
}

/// Returns `true` if `dir` is an independently-buildable root.
fn is_buildable_root(dir: &Path) -> bool {
    // Cargo.toml with [package]
    let cargo = dir.join("Cargo.toml");
    if cargo.is_file() {
        if let Ok(contents) = fs::read_to_string(&cargo) {
            if contents.contains("[package]") {
                return true;
            }
        }
    }

    // package.json with "scripts"
    let pkg_json = dir.join("package.json");
    if pkg_json.is_file() {
        if let Ok(contents) = fs::read_to_string(&pkg_json) {
            if contents.contains("\"scripts\"") {
                return true;
            }
        }
    }

    // go.mod
    if dir.join("go.mod").is_file() {
        return true;
    }

    // pyproject.toml with [build-system] or [project]
    let pyproject = dir.join("pyproject.toml");
    if pyproject.is_file() {
        if let Ok(contents) = fs::read_to_string(&pyproject) {
            if contents.contains("[build-system]") || contents.contains("[project]") {
                return true;
            }
        }
    }

    false
}

/// Map `(has_workspace, buildable_roots)` → shape label.
///
/// Evaluation order — first match wins:
/// 1. `buildable_roots >= 3`  → "monorepo"
/// 2. `has_workspace && buildable_roots < 3` → "workspace"
/// 3. `buildable_roots >= 2 && !has_workspace` → "multi-package"
/// 4. Everything else → "single-package"
fn classify(has_workspace: bool, buildable_roots: u32) -> String {
    if buildable_roots >= 3 {
        "monorepo".to_string()
    } else if has_workspace && buildable_roots < 3 {
        "workspace".to_string()
    } else if buildable_roots >= 2 && !has_workspace {
        "multi-package".to_string()
    } else {
        "single-package".to_string()
    }
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use tempfile::TempDir;

    #[test]
    fn test_single_package_default() {
        let dir = TempDir::new().expect("create temp dir");
        fs::write(dir.path().join("Cargo.toml"), "[package]\nname = \"foo\"").expect("write");
        let shape = detect_repo_shape(dir.path());
        assert_eq!(shape.repo_shape, "single-package");
        assert!(!shape.has_workspace);
        assert_eq!(shape.buildable_roots, 1);
    }

    #[test]
    fn test_workspace_detected() {
        let dir = TempDir::new().expect("create temp dir");
        fs::write(
            dir.path().join("Cargo.toml"),
            "[workspace]\nmembers = [\"a\", \"b\"]",
        )
        .expect("write");
        fs::create_dir_all(dir.path().join("a")).expect("mkdir");
        fs::write(dir.path().join("a/Cargo.toml"), "[package]\nname = \"a\"").expect("write");
        let shape = detect_repo_shape(dir.path());
        assert_eq!(shape.repo_shape, "workspace");
        assert!(shape.has_workspace);
    }

    #[test]
    fn test_monorepo_by_buildable_roots() {
        let dir = TempDir::new().expect("create temp dir");
        for name in ["svc-a", "svc-b", "svc-c"] {
            let p = dir.path().join(name);
            fs::create_dir_all(&p).expect("mkdir");
            fs::write(p.join("package.json"), r#"{"scripts":{"build":"tsc"}}"#).expect("write");
        }
        let shape = detect_repo_shape(dir.path());
        assert_eq!(shape.repo_shape, "monorepo");
        assert_eq!(shape.buildable_roots, 3);
    }

    #[test]
    fn test_empty_dir() {
        let dir = TempDir::new().expect("create temp dir");
        let shape = detect_repo_shape(dir.path());
        assert_eq!(shape.repo_shape, "single-package");
        assert_eq!(shape.buildable_roots, 0);
    }

    #[test]
    fn test_multi_package_no_workspace() {
        let dir = TempDir::new().expect("create temp dir");
        for name in ["svc-a", "svc-b"] {
            let p = dir.path().join(name);
            fs::create_dir_all(&p).expect("mkdir");
            fs::write(p.join("go.mod"), "module example.com/svc").expect("write");
        }
        let shape = detect_repo_shape(dir.path());
        assert_eq!(shape.repo_shape, "multi-package");
        assert!(!shape.has_workspace);
        assert_eq!(shape.buildable_roots, 2);
    }

    #[test]
    fn test_pnpm_workspace_marker() {
        let dir = TempDir::new().expect("create temp dir");
        fs::write(
            dir.path().join("pnpm-workspace.yaml"),
            "packages:\n  - 'packages/*'",
        )
        .expect("write");
        let shape = detect_repo_shape(dir.path());
        assert!(shape.has_workspace);
    }

    #[test]
    fn test_lerna_workspace_marker() {
        let dir = TempDir::new().expect("create temp dir");
        fs::write(dir.path().join("lerna.json"), r#"{"version":"1.0.0"}"#).expect("write");
        let shape = detect_repo_shape(dir.path());
        assert!(shape.has_workspace);
    }

    #[test]
    fn test_go_work_marker() {
        let dir = TempDir::new().expect("create temp dir");
        fs::write(dir.path().join("go.work"), "go 1.21\n").expect("write");
        let shape = detect_repo_shape(dir.path());
        assert!(shape.has_workspace);
    }

    #[test]
    fn test_pyproject_buildable_root() {
        let dir = TempDir::new().expect("create temp dir");
        fs::write(
            dir.path().join("pyproject.toml"),
            "[build-system]\nrequires = [\"setuptools\"]",
        )
        .expect("write");
        let shape = detect_repo_shape(dir.path());
        assert_eq!(shape.buildable_roots, 1);
    }

    #[test]
    fn test_defaults_language_fields() {
        let dir = TempDir::new().expect("create temp dir");
        let shape = detect_repo_shape(dir.path());
        assert_eq!(shape.language_count, 0);
        assert_eq!(shape.primary_language_ratio, 0.0);
    }

    #[test]
    fn test_classify_workspace_takes_priority_over_multi_package() {
        // Two buildable roots + workspace marker → "workspace", not "multi-package"
        let dir = TempDir::new().expect("create temp dir");
        fs::write(dir.path().join("go.work"), "go 1.21\n").expect("write");
        for name in ["svc-a", "svc-b"] {
            let p = dir.path().join(name);
            fs::create_dir_all(&p).expect("mkdir");
            fs::write(p.join("go.mod"), "module example.com/svc").expect("write");
        }
        let shape = detect_repo_shape(dir.path());
        assert_eq!(shape.repo_shape, "workspace");
        assert!(shape.has_workspace);
    }
}
