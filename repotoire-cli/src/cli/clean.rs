//! Clean command - remove cache directories

use anyhow::Result;
use std::path::Path;

pub fn run(path: &Path, dry_run: bool) -> Result<()> {
    let repo_path = path.canonicalize().unwrap_or_else(|_| path.to_path_buf());

    let mut to_remove = Vec::new();

    // 1. Check central cache directory
    let cache_dir = crate::cache::get_cache_dir(&repo_path);
    if cache_dir.exists() {
        to_remove.push(("Central cache".to_string(), cache_dir));
    }

    // 2. Find any legacy .repotoire directories in repo
    for entry in ignore::WalkBuilder::new(path)
        .hidden(false)
        .git_ignore(false)
        .build()
        .filter_map(|e| e.ok())
    {
        if entry.file_type().map(|ft| ft.is_dir()).unwrap_or(false)
            && entry.file_name() == ".repotoire"
        {
            to_remove.push(("Legacy".to_string(), entry.path().to_path_buf()));
        }
    }

    if to_remove.is_empty() {
        println!("No cache directories found.");
        return Ok(());
    }

    println!(
        "Found {} cache director{}:",
        to_remove.len(),
        if to_remove.len() == 1 { "y" } else { "ies" }
    );

    for (kind, dir) in &to_remove {
        println!("  [{}] {}", kind, dir.display());
    }

    if dry_run {
        println!("\nDry run - nothing removed. Run without --dry-run to delete.");
        return Ok(());
    }

    println!();
    let mut removed = 0;
    for (kind, dir) in &to_remove {
        // For legacy .repotoire dirs, preserve style-profile.json
        if kind == "Legacy" {
            let profile = dir.join("style-profile.json");
            let profile_backup = profile
                .exists()
                .then(|| std::fs::read(&profile).ok())
                .flatten();
            if let Err(e) = std::fs::remove_dir_all(dir) {
                eprintln!("Failed to remove {}: {}", dir.display(), e);
                continue;
            }
            // Restore style profile if it existed
            if let Some(data) = profile_backup {
                let _ = std::fs::create_dir_all(dir);
                let _ = std::fs::write(&profile, data);
                println!("Removed: {} (preserved style-profile.json)", dir.display());
            } else {
                println!("Removed: {}", dir.display());
            }
        } else {
            if let Err(e) = std::fs::remove_dir_all(dir) {
                eprintln!("Failed to remove {}: {}", dir.display(), e);
                continue;
            }
            println!("Removed: {}", dir.display());
        }
        removed += 1;
    }

    println!(
        "\nCleaned {} director{}.",
        removed,
        if removed == 1 { "y" } else { "ies" }
    );

    Ok(())
}
