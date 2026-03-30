use std::collections::HashSet;
use std::path::{Path, PathBuf};

use notify_debouncer_full::DebouncedEvent;

use crate::cli::analyze::files::SUPPORTED_EXTENSIONS;

/// File filter for watch mode. Uses the `ignore` crate to respect
/// .gitignore and .repotoireignore patterns, matching analyze's behavior.
pub struct WatchFilter {
    repo_path: PathBuf,
    matcher: ignore::gitignore::Gitignore,
}

impl WatchFilter {
    pub fn new(repo_path: &Path) -> Self {
        let repo_path = repo_path
            .canonicalize()
            .unwrap_or_else(|_| repo_path.to_path_buf());
        let mut builder = ignore::gitignore::GitignoreBuilder::new(&repo_path);

        // Add root .gitignore and .repotoireignore
        let gitignore = repo_path.join(".gitignore");
        if gitignore.exists() {
            let _ = builder.add(&gitignore);
        }
        let repotoireignore = repo_path.join(".repotoireignore");
        if repotoireignore.exists() {
            let _ = builder.add(&repotoireignore);
        }

        // Walk subdirectories for nested ignore files
        for entry in ignore::WalkBuilder::new(&repo_path)
            .hidden(false)
            .ignore(false)
            .git_ignore(false)
            .max_depth(Some(10))
            .build()
            .flatten()
        {
            let path = entry.path();
            if path == gitignore || path == repotoireignore {
                continue; // already added
            }
            if path.file_name() == Some(".gitignore".as_ref())
                || path.file_name() == Some(".repotoireignore".as_ref())
            {
                let _ = builder.add(path);
            }
        }

        let matcher = builder.build().unwrap_or_else(|_| {
            ignore::gitignore::GitignoreBuilder::new(&repo_path)
                .build()
                .expect("empty gitignore builder should never fail")
        });

        Self { repo_path, matcher }
    }

    /// Check if a path should trigger re-analysis.
    pub fn should_analyze(&self, path: &Path) -> bool {
        let has_ext = path
            .extension()
            .and_then(|e| e.to_str())
            .is_some_and(|ext| SUPPORTED_EXTENSIONS.contains(&ext));
        if !has_ext {
            return false;
        }

        // Canonicalize the path to match the canonicalized repo_path
        let abs = path.canonicalize().unwrap_or_else(|_| path.to_path_buf());
        let rel = abs.strip_prefix(&self.repo_path).unwrap_or(&abs);
        !self
            .matcher
            .matched_path_or_any_parents(rel, path.is_dir())
            .is_ignore()
            && path.is_file()
    }

    /// Collect and deduplicate changed source files from notify events.
    pub fn collect_changed(&self, events: &[DebouncedEvent]) -> Vec<PathBuf> {
        events
            .iter()
            .flat_map(|event| event.paths.iter())
            .filter(|p| self.should_analyze(p))
            .cloned()
            .collect::<HashSet<_>>()
            .into_iter()
            .collect()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use notify::EventKind;
    use notify_debouncer_full::DebouncedEvent;
    use std::fs;
    use std::time::Instant;
    use tempfile::TempDir;

    #[test]
    fn filter_extensions() {
        let tmp = TempDir::new().unwrap();
        let filter = WatchFilter::new(tmp.path());

        let rs = tmp.path().join("main.rs");
        fs::write(&rs, "fn main() {}").unwrap();
        assert!(filter.should_analyze(&rs));

        let py = tmp.path().join("app.py");
        fs::write(&py, "print('hi')").unwrap();
        assert!(filter.should_analyze(&py));

        let ts = tmp.path().join("index.ts");
        fs::write(&ts, "const x = 1;").unwrap();
        assert!(filter.should_analyze(&ts));

        let md = tmp.path().join("README.md");
        fs::write(&md, "# hello").unwrap();
        assert!(!filter.should_analyze(&md));

        let toml = tmp.path().join("Cargo.toml");
        fs::write(&toml, "[package]").unwrap();
        assert!(!filter.should_analyze(&toml));

        let lock = tmp.path().join("Cargo.lock");
        fs::write(&lock, "[[package]]").unwrap();
        assert!(!filter.should_analyze(&lock));
    }

    #[test]
    fn filter_gitignore() {
        let tmp = TempDir::new().unwrap();
        fs::write(tmp.path().join(".gitignore"), "target/\n*.generated.rs\n").unwrap();
        let filter = WatchFilter::new(tmp.path());

        let target_dir = tmp.path().join("target");
        fs::create_dir_all(&target_dir).unwrap();
        let target_file = target_dir.join("debug.rs");
        fs::write(&target_file, "").unwrap();
        assert!(!filter.should_analyze(&target_file));

        let generated = tmp.path().join("output.generated.rs");
        fs::write(&generated, "").unwrap();
        assert!(!filter.should_analyze(&generated));

        let src = tmp.path().join("src.rs");
        fs::write(&src, "fn main() {}").unwrap();
        assert!(filter.should_analyze(&src));
    }

    #[test]
    fn filter_repotoireignore() {
        let tmp = TempDir::new().unwrap();
        fs::write(tmp.path().join(".repotoireignore"), "vendor/\n").unwrap();
        let filter = WatchFilter::new(tmp.path());

        let vendor_dir = tmp.path().join("vendor");
        fs::create_dir_all(&vendor_dir).unwrap();
        let vendor_file = vendor_dir.join("lib.rs");
        fs::write(&vendor_file, "").unwrap();
        assert!(!filter.should_analyze(&vendor_file));

        let src = tmp.path().join("lib.rs");
        fs::write(&src, "").unwrap();
        assert!(filter.should_analyze(&src));
    }

    #[test]
    fn filter_no_ignore_files() {
        let tmp = TempDir::new().unwrap();
        let filter = WatchFilter::new(tmp.path());
        let f = tmp.path().join("main.rs");
        fs::write(&f, "").unwrap();
        assert!(filter.should_analyze(&f));
    }

    #[test]
    fn filter_collect_deduplicates() {
        let tmp = TempDir::new().unwrap();
        let f = tmp.path().join("main.rs");
        fs::write(&f, "fn main() {}").unwrap();
        let filter = WatchFilter::new(tmp.path());

        // Construct two events pointing at the same file to test deduplication.
        let make_event = |path: PathBuf| {
            let mut event = notify::Event::new(EventKind::Modify(notify::event::ModifyKind::Any));
            event.paths = vec![path];
            DebouncedEvent::new(event, Instant::now())
        };

        let events = vec![make_event(f.clone()), make_event(f.clone())];
        let changed = filter.collect_changed(&events);
        assert_eq!(changed.len(), 1);
        assert_eq!(changed[0], f);
    }
}
