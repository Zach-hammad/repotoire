//! Fix command implementation
//!
//! Generates AI-powered fixes for code findings.

use anyhow::{Context, Result};
use console::{style, Term};
use std::fs;
use std::path::Path;

use crate::ai::{AiClient, FixGenerator, LlmBackend};
use crate::models::Finding;

/// Run the fix command
pub fn run(path: &Path, index: usize, apply: bool) -> Result<()> {
    // Load findings from last analysis
    let findings_path = path.join(".repotoire/last_findings.json");
    if !findings_path.exists() {
        anyhow::bail!(
            "No findings found. Run `repotoire analyze` first.\n\
             Looking for: {}",
            findings_path.display()
        );
    }

    let findings_json = fs::read_to_string(&findings_path)
        .context("Failed to read findings file")?;
    
    // Parse the wrapped format { "findings": [...] }
    let parsed: serde_json::Value = serde_json::from_str(&findings_json)
        .context("Failed to parse findings file")?;
    let findings: Vec<Finding> = serde_json::from_value(
        parsed.get("findings").cloned().unwrap_or(serde_json::json!([]))
    ).context("Failed to parse findings array")?;

    if index == 0 || index > findings.len() {
        anyhow::bail!(
            "Invalid finding index: {}. Valid range: 1-{}",
            index,
            findings.len()
        );
    }

    let finding = &findings[index - 1];

    // Try to create AI client (check for API key) - in order of preference
    let backends = [
        LlmBackend::Anthropic,
        LlmBackend::OpenAi,
        LlmBackend::Deepinfra,
        LlmBackend::OpenRouter,
    ];
    
    // Try cloud providers first
    let client = backends
        .iter()
        .find_map(|&b| AiClient::from_env(b).ok())
        // Then try Ollama if running locally
        .or_else(|| {
            if AiClient::ollama_available() {
                AiClient::from_env(LlmBackend::Ollama).ok()
            } else {
                None
            }
        })
        .ok_or_else(|| {
            eprintln!("{}", style("No AI provider found!").red().bold());
            eprintln!("\nOptions:");
            eprintln!("  {} - Anthropic Claude", style("ANTHROPIC_API_KEY").cyan());
            eprintln!("  {} - OpenAI GPT-4", style("OPENAI_API_KEY").cyan());
            eprintln!("  {} - Deepinfra (cheapest cloud)", style("DEEPINFRA_API_KEY").cyan());
            eprintln!("  {} - OpenRouter (any model)", style("OPENROUTER_API_KEY").cyan());
            eprintln!("  {} - Local (free!)", style("Ollama").green());
            eprintln!("\nFor local AI (free):");
            eprintln!("  1. Install Ollama: https://ollama.ai");
            eprintln!("  2. Run: ollama pull llama3.3:70b");
            eprintln!("  3. Then: repotoire fix 1");
            anyhow::anyhow!("No AI provider configured")
        })?;

    let term = Term::stderr();
    term.write_line(&format!(
        "\n{} Generating fix for finding #{}...\n",
        style("⚡").cyan(),
        index
    ))?;

    term.write_line(&format!(
        "  {} {}\n  {} {}\n  {} {:?}\n",
        style("Title:").bold(),
        finding.title,
        style("Severity:").bold(),
        finding.severity,
        style("File:").bold(),
        finding.affected_files.first().unwrap_or(&"unknown".into())
    ))?;

    term.write_line(&format!(
        "  {} {} ({})\n",
        style("Using:").dim(),
        client.model(),
        match client.backend() {
            LlmBackend::Anthropic => "Anthropic",
            LlmBackend::OpenAi => "OpenAI",
            LlmBackend::Deepinfra => "Deepinfra",
            LlmBackend::OpenRouter => "OpenRouter",
            LlmBackend::Ollama => "Ollama (local)",
        }
    ))?;

    // Generate fix using async runtime
    let rt = tokio::runtime::Runtime::new()?;
    let generator = FixGenerator::new(client);

    let fix = rt.block_on(async {
        generator.generate_fix_with_retry(finding, path, 2).await
    })?;

    // Display fix
    term.write_line(&format!(
        "{} {}\n",
        style("Fix:").green().bold(),
        fix.title
    ))?;

    term.write_line(&format!(
        "  {} {:?}\n  {} {}\n",
        style("Confidence:").bold(),
        fix.confidence,
        style("Valid syntax:").bold(),
        if fix.syntax_valid {
            style("✓").green()
        } else {
            style("✗").red()
        }
    ))?;

    term.write_line(&format!(
        "{}\n{}\n",
        style("Description:").bold(),
        fix.description
    ))?;

    term.write_line(&format!(
        "{}\n{}\n",
        style("Rationale:").bold(),
        fix.rationale
    ))?;

    // Show diff
    term.write_line(&format!("{}\n", style("Changes:").bold()))?;
    let diff = fix.diff(path);
    for line in diff.lines() {
        if line.starts_with('+') && !line.starts_with("+++") {
            term.write_line(&format!("{}", style(line).green()))?;
        } else if line.starts_with('-') && !line.starts_with("---") {
            term.write_line(&format!("{}", style(line).red()))?;
        } else if line.starts_with("@@") {
            term.write_line(&format!("{}", style(line).cyan()))?;
        } else {
            term.write_line(line)?;
        }
    }

    // Apply if requested
    if apply {
        if !fix.syntax_valid {
            term.write_line(&format!(
                "\n{} Fix has syntax errors, not applying automatically.",
                style("Warning:").yellow().bold()
            ))?;
            term.write_line("Review the changes and apply manually if appropriate.\n")?;
        } else {
            term.write_line(&format!(
                "\n{} Applying fix...",
                style("⚡").cyan()
            ))?;

            fix.apply(path)?;

            term.write_line(&format!(
                "{} Fix applied successfully!\n",
                style("✓").green().bold()
            ))?;
        }
    } else {
        term.write_line(&format!(
            "\n{} To apply this fix, run:",
            style("Tip:").cyan().bold()
        ))?;
        term.write_line(&format!(
            "  repotoire fix {} --apply\n",
            index
        ))?;
    }

    // Save fix proposal
    let fixes_dir = path.join(".repotoire/fixes");
    fs::create_dir_all(&fixes_dir)?;
    let fix_path = fixes_dir.join(format!("{}.json", fix.id));
    fs::write(&fix_path, serde_json::to_string_pretty(&fix)?)?;

    term.write_line(&format!(
        "{} Fix saved to {}\n",
        style("📁").dim(),
        fix_path.display()
    ))?;

    Ok(())
}
