//! File change detection between analysis runs.

use std::collections::HashMap;
use std::path::{Path, PathBuf};

use super::stages::collect::CollectOutput;

/// Diff between current and previous file state.
pub(crate) struct FileChanges {
    pub changed: Vec<PathBuf>,
    pub added: Vec<PathBuf>,
    pub removed: Vec<PathBuf>,
}

impl FileChanges {
    /// Returns true if nothing changed (no adds, removes, or modifications).
    pub fn nothing_changed(&self) -> bool {
        self.changed.is_empty() && self.added.is_empty() && self.removed.is_empty()
    }

    /// Returns true if there are any changes at all.
    pub fn is_delta(&self) -> bool {
        !self.nothing_changed()
    }

    /// Returns all changed and added file paths (files that need re-parsing).
    pub fn changed_and_added(&self) -> Vec<PathBuf> {
        self.changed
            .iter()
            .chain(self.added.iter())
            .cloned()
            .collect()
    }

    /// Compute diff from previous hashes and current collect output.
    pub fn compute(prev_hashes: &HashMap<PathBuf, u64>, current: &CollectOutput) -> Self {
        let mut changed = Vec::new();
        let mut added = Vec::new();
        let current_map: HashMap<&Path, u64> = current
            .files
            .iter()
            .map(|f| (f.path.as_path(), f.content_hash))
            .collect();

        for sf in &current.files {
            match prev_hashes.get(&sf.path) {
                Some(&old_hash) if old_hash != sf.content_hash => {
                    changed.push(sf.path.clone());
                }
                None => added.push(sf.path.clone()),
                _ => {}
            }
        }

        let removed: Vec<PathBuf> = prev_hashes
            .keys()
            .filter(|p| !current_map.contains_key(p.as_path()))
            .cloned()
            .collect();

        Self {
            changed,
            added,
            removed,
        }
    }

    /// Compute for cold run (no previous state — all files are "added").
    pub fn cold(current: &CollectOutput) -> Self {
        Self {
            changed: Vec::new(),
            added: current.files.iter().map(|f| f.path.clone()).collect(),
            removed: Vec::new(),
        }
    }
}
