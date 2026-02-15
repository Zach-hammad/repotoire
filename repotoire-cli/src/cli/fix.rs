//! Fix command implementation
//!
//! Generates fixes for code findings.
//! Uses AI when available, falls back to rule-based suggestions.

use anyhow::{Context, Result};
use console::{style, Term};
use std::fs;
use std::path::Path;

use crate::ai::{AiClient, FixGenerator, LlmBackend};
use crate::fixes::{generate_rule_fix, RuleFix};
use crate::models::Finding;

/// Run the fix command
pub fn run(path: &Path, index: usize, apply: bool, no_ai: bool) -> Result<()> {
    // Load findings from last analysis
    let findings_path = crate::cache::get_findings_cache_path(path);
    if !findings_path.exists() {
        anyhow::bail!(
            "No findings found. Run `repotoire analyze` first.\n\
             Looking for: {}",
            findings_path.display()
        );
    }

    let findings_json =
        fs::read_to_string(&findings_path).context("Failed to read findings file")?;

    // Parse the wrapped format { "findings": [...] }
    let parsed: serde_json::Value =
        serde_json::from_str(&findings_json).context("Failed to parse findings file")?;
    let findings: Vec<Finding> = serde_json::from_value(
        parsed
            .get("findings")
            .cloned()
            .unwrap_or(serde_json::json!([])),
    )
    .context("Failed to parse findings array")?;

    if index == 0 || index > findings.len() {
        anyhow::bail!(
            "Invalid finding index: {}. Valid range: 1-{}",
            index,
            findings.len()
        );
    }

    let finding = &findings[index - 1];

    // If --no-ai flag is set, use rule-based fixes directly
    if no_ai {
        return run_rule_fix(path, finding, index, apply);
    }

    // Try to create AI client - check all providers
    let backends = [
        LlmBackend::Anthropic,
        LlmBackend::OpenAi,
        LlmBackend::Deepinfra,
        LlmBackend::OpenRouter,
    ];

    let ai_client = backends
        .iter()
        .find_map(|&b| AiClient::from_env(b).ok())
        .or_else(|| {
            if AiClient::ollama_available() {
                AiClient::from_env(LlmBackend::Ollama).ok()
            } else {
                None
            }
        });

    // If we have AI, use it; otherwise fall back to rule-based fixes
    match ai_client {
        Some(client) => run_ai_fix(path, finding, index, apply, client),
        None => run_rule_fix(path, finding, index, apply),
    }
}

/// Run AI-powered fix generation
fn run_ai_fix(
    path: &Path,
    finding: &Finding,
    index: usize,
    apply: bool,
    client: AiClient,
) -> Result<()> {
    let term = Term::stderr();
    term.write_line(&format!(
        "\n{} Generating AI fix for finding #{}...\n",
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

    let fix = rt.block_on(async { generator.generate_fix_with_retry(finding, path, 2).await })?;

    // Display fix
    term.write_line(&format!("{} {}\n", style("Fix:").green().bold(), fix.title))?;

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
            term.write_line(&format!("\n{} Applying fix...", style("⚡").cyan()))?;

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
        term.write_line(&format!("  repotoire fix {} --apply\n", index))?;
    }

    // Save fix proposal
    let fixes_dir = crate::cache::get_cache_dir(path).join("fixes");
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

/// Run rule-based fix generation (no AI required)
fn run_rule_fix(path: &Path, finding: &Finding, index: usize, apply: bool) -> Result<()> {
    let term = Term::stderr();

    term.write_line(&format!(
        "\n{} Generating rule-based fix for finding #{}...\n",
        style("📋").cyan(),
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
        "  {} {}\n",
        style("Mode:").dim(),
        "Rule-based (no AI API key found)"
    ))?;

    // Try to generate a rule-based fix
    match generate_rule_fix(finding, path) {
        Some(rule_fix) => display_rule_fix(&term, &rule_fix, finding, index, apply, path),
        None => display_fallback_suggestion(&term, finding, index),
    }
}

/// Display a rule-based fix
fn display_rule_fix(
    term: &Term,
    rule_fix: &RuleFix,
    finding: &Finding,
    index: usize,
    apply: bool,
    _path: &Path,
) -> Result<()> {
    term.write_line(&format!(
        "{} {}\n",
        style("Fix:").green().bold(),
        rule_fix.title
    ))?;

    term.write_line(&format!(
        "{}\n{}\n",
        style("Description:").bold(),
        rule_fix.description
    ))?;

    // Show steps
    if !rule_fix.steps.is_empty() {
        term.write_line(&format!("{}", style("Steps:").bold()))?;
        for step in &rule_fix.steps {
            if step.is_empty() {
                term.write_line("")?;
            } else if step.starts_with("  ") || step.starts_with("```") {
                term.write_line(&format!("   {}", style(step).dim()))?;
            } else {
                term.write_line(&format!("   • {}", step))?;
            }
        }
        term.write_line("")?;
    }

    // Show patch if available
    if let Some(ref patch) = rule_fix.patch {
        term.write_line(&format!("{}\n", style("Suggested changes:").bold()))?;
        for line in patch.lines() {
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
        term.write_line("")?;
    }

    // Show existing suggested_fix from finding if different
    if let Some(ref suggested) = finding.suggested_fix {
        if !rule_fix.steps.iter().any(|s| suggested.contains(s)) {
            term.write_line(&format!("{}", style("Detector suggestion:").bold()))?;
            for line in suggested.lines() {
                term.write_line(&format!("   {}", style(line).dim()))?;
            }
            term.write_line("")?;
        }
    }

    // Auto-apply notice
    if rule_fix.auto_applicable && rule_fix.patch.is_some() {
        if apply {
            term.write_line(&format!(
                "{} Auto-apply is available but not yet implemented for rule-based fixes.",
                style("Note:").yellow()
            ))?;
            term.write_line("Please apply the changes manually for now.\n")?;
        } else {
            term.write_line(&format!(
                "{} This fix can potentially be auto-applied.",
                style("💡").cyan()
            ))?;
            term.write_line(&format!(
                "   Run {} (manual review still recommended)\n",
                style(format!("repotoire fix {} --apply", index)).cyan()
            ))?;
        }
    }

    // AI upgrade suggestion
    term.write_line(&format!("{}", style("💡 Want smarter fixes?").bold()))?;
    term.write_line("   Set up an AI provider for context-aware code fixes:")?;
    term.write_line(&format!(
        "   • {} - Anthropic Claude",
        style("export ANTHROPIC_API_KEY=sk-ant-...").cyan()
    ))?;
    term.write_line(&format!(
        "   • {} - Local (free!)",
        style("ollama pull llama3.3:70b").green()
    ))?;
    term.write_line("")?;

    Ok(())
}

/// Display fallback suggestion when no rule-based fix is available
fn display_fallback_suggestion(term: &Term, finding: &Finding, index: usize) -> Result<()> {
    term.write_line(&format!(
        "{} No rule-based fix available for detector: {}\n",
        style("ℹ️").yellow(),
        finding.detector
    ))?;

    // Show the detector's own suggestion if available
    if let Some(ref suggested) = finding.suggested_fix {
        term.write_line(&format!("{}", style("Detector suggestion:").bold()))?;
        for line in suggested.lines() {
            term.write_line(&format!("   {}", line))?;
        }
        term.write_line("")?;
    }

    // Show description
    term.write_line(&format!("{}", style("Description:").bold()))?;
    for line in finding.description.lines().take(10) {
        term.write_line(&format!("   {}", line))?;
    }
    term.write_line("")?;

    // Show why it matters
    if let Some(ref why) = finding.why_it_matters {
        term.write_line(&format!("{}", style("Why it matters:").bold()))?;
        for line in why.lines() {
            term.write_line(&format!("   {}", line))?;
        }
        term.write_line("")?;
    }

    // AI suggestion
    term.write_line(&format!("{}", style("💡 For AI-powered fixes:").bold()))?;
    term.write_line("   Set ANTHROPIC_API_KEY or install Ollama for smarter context-aware fixes.")?;
    term.write_line(&format!(
        "   • {} - Anthropic Claude",
        style("export ANTHROPIC_API_KEY=sk-ant-...").cyan()
    ))?;
    term.write_line(&format!(
        "   • {} - OpenAI GPT-4",
        style("export OPENAI_API_KEY=sk-...").cyan()
    ))?;
    term.write_line(&format!(
        "   • {} - Local AI (free)",
        style("ollama pull llama3.3:70b").green()
    ))?;
    term.write_line("")?;

    Ok(())
}
