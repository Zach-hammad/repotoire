//! Clean command - remove .repotoire directories

use anyhow::Result;
use std::path::Path;
use walkdir::WalkDir;

pub fn run(path: &Path, dry_run: bool) -> Result<()> {
    let mut found = Vec::new();
    
    // Find all .repotoire directories
    for entry in WalkDir::new(path)
        .follow_links(false)
        .into_iter()
        .filter_map(|e| e.ok())
    {
        if entry.file_type().is_dir() && entry.file_name() == ".repotoire" {
            found.push(entry.path().to_path_buf());
        }
    }
    
    if found.is_empty() {
        println!("No .repotoire directories found.");
        return Ok(());
    }
    
    println!("Found {} .repotoire director{}:", 
        found.len(), 
        if found.len() == 1 { "y" } else { "ies" }
    );
    
    for dir in &found {
        println!("  {}", dir.display());
    }
    
    if dry_run {
        println!("\nDry run - nothing removed. Run without --dry-run to delete.");
        return Ok(());
    }
    
    println!();
    for dir in &found {
        match std::fs::remove_dir_all(dir) {
            Ok(_) => println!("Removed: {}", dir.display()),
            Err(e) => eprintln!("Failed to remove {}: {}", dir.display(), e),
        }
    }
    
    println!("\nCleaned {} director{}.", 
        found.len(),
        if found.len() == 1 { "y" } else { "ies" }
    );
    
    Ok(())
}
