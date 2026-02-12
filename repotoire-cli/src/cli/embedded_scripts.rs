//! Embedded agent scripts for TUI fix functionality
//!
//! These scripts are embedded in the binary and extracted at runtime
//! to avoid requiring users to have the scripts in their repo.

use anyhow::Result;
use std::path::PathBuf;

/// Ollama agent script (Python)
pub const OLLAMA_AGENT: &str = include_str!("../../scripts/fix_agent_ollama.py");

/// Claude Agent SDK script (Python)  
pub const CLAUDE_AGENT: &str = include_str!("../../scripts/fix_agent.py");

/// Get the directory where we store extracted scripts
pub fn get_scripts_dir() -> Result<PathBuf> {
    let dir = dirs::data_local_dir()
        .unwrap_or_else(|| PathBuf::from("."))
        .join("repotoire")
        .join("scripts");
    std::fs::create_dir_all(&dir)?;
    Ok(dir)
}

/// Extract embedded scripts to the local data directory
/// Returns paths to (ollama_script, claude_script)
pub fn extract_scripts() -> Result<(PathBuf, PathBuf)> {
    let dir = get_scripts_dir()?;

    let ollama_path = dir.join("fix_agent_ollama.py");
    let claude_path = dir.join("fix_agent.py");

    // Always overwrite to ensure latest version
    std::fs::write(&ollama_path, OLLAMA_AGENT)?;
    std::fs::write(&claude_path, CLAUDE_AGENT)?;

    // Make executable on Unix
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let perms = std::fs::Permissions::from_mode(0o755);
        std::fs::set_permissions(&ollama_path, perms.clone())?;
        std::fs::set_permissions(&claude_path, perms)?;
    }

    Ok((ollama_path, claude_path))
}

/// Get paths to agent scripts, extracting if needed
/// Prefers repo-local scripts if they exist (for development)
pub fn get_script_paths(repo_path: &std::path::Path) -> Result<(PathBuf, PathBuf)> {
    // Check for repo-local scripts first (development mode)
    let local_ollama = repo_path.join("scripts/fix_agent_ollama.py");
    let local_claude = repo_path.join("scripts/fix_agent.py");

    if local_ollama.exists() && local_claude.exists() {
        return Ok((local_ollama, local_claude));
    }

    // Extract embedded scripts
    extract_scripts()
}
