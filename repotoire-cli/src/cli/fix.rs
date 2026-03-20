//! Fix command implementation
//!
//! Generates fixes for code findings.
//! Uses AI when available, falls back to rule-based suggestions.

use anyhow::{Context, Result};
use console::{style, Term};
use std::fs;
use std::io::{self, Write};
use std::path::Path;

use crate::ai::{AiClient, FixGenerator, LlmBackend};
use crate::fixes::{generate_rule_fix, RuleFix};
use crate::models::Finding;

/// Options for fix execution
#[derive(Clone, Copy)]
pub struct FixOptions {
    pub apply: bool,
    pub no_ai: bool,
    pub dry_run: bool,
    pub auto: bool,
}

/// Simple yes/no confirmation prompt
fn display_patch_preview(term: &console::Term, patch: &str) -> Result<()> {
    for line in patch.lines().take(15) {
        let styled = if line.starts_with('+') && !line.starts_with("+++") {
            format!("  {}", style(line).green())
        } else if line.starts_with('-') && !line.starts_with("---") {
            format!("  {}", style(line).red())
        } else if line.starts_with("@@") {
            format!("  {}", style(line).cyan())
        } else {
            continue;
        };
        term.write_line(&styled)?;
    }
    Ok(())
}

fn confirm(prompt: &str) -> Result<bool> {
    print!("{} [Y/n] ", prompt);
    io::stdout().flush()?;
    let mut input = String::new();
    io::stdin().read_line(&mut input)?;
    let input = input.trim().to_lowercase();
    Ok(input.is_empty() || input == "y" || input == "yes")
}

/// Display a single finding in dry-run mode with colored patch or manual steps
fn display_dry_run_finding(term: &Term, idx: usize, finding: &Finding, rule_fix: &RuleFix) -> Result<()> {
    term.write_line(&format!(
        "{}",
        style(format!("═══ Finding #{} ═══", idx)).cyan().bold()
    ))?;
    term.write_line(&format!(
        "  {} {}",
        style("Detector:").bold(),
        finding.detector
    ))?;
    term.write_line(&format!("  {} {}", style("Title:").bold(), finding.title))?;
    if let Some(file) = finding.affected_files.first() {
        term.write_line(&format!("  {} {}", style("File:").bold(), file.display()))?;
    }
    term.write_line(&format!(
        "  {} {}",
        style("Fix:").green().bold(),
        rule_fix.title
    ))?;

    if let Some(ref patch) = rule_fix.patch {
        term.write_line(&format!("\n  {}:", style("Changes").bold()))?;
        for line in patch.lines() {
            let colored = colorize_diff_line(line);
            term.write_line(&format!("    {}", colored))?;
        }
    } else {
        term.write_line(&format!(
            "\n  {} (no auto-patch available)",
            style("Manual fix required").yellow()
        ))?;
        for step in rule_fix
            .steps
            .iter()
            .filter(|s| !s.is_empty() && !s.starts_with("  ") && !s.starts_with("```"))
        {
            term.write_line(&format!("    • {}", step))?;
        }
    }
    term.write_line("")?;
    Ok(())
}

/// Colorize a single diff line for terminal output
fn colorize_diff_line(line: &str) -> String {
    match line.as_bytes().first() {
        Some(b'+') if !line.starts_with("+++") => style(line).green().to_string(),
        Some(b'-') if !line.starts_with("---") => style(line).red().to_string(),
        Some(b'@') => style(line).cyan().to_string(),
        _ => line.to_string(),
    }
}

/// Display the result of applying a rule fix
fn display_apply_result(term: &Term, result: Result<()>) -> Result<()> {
    match result {
        Ok(()) => {
            term.write_line(&format!(
                "{} Fix applied successfully!",
                style("✓").green().bold()
            ))?;
            term.write_line(&format!(
                "   Re-run analysis to verify: {}\n",
                style("repotoire analyze").cyan()
            ))?;
        }
        Err(e) => {
            term.write_line(&format!(
                "{} Failed to apply fix: {}",
                style("✗").red().bold(),
                e
            ))?;
            term.write_line("Please apply the changes manually.\n")?;
        }
    }
    Ok(())
}

/// Run the fix command
pub fn run(
    path: &Path,
    index: Option<usize>,
    apply: bool,
    no_ai: bool,
    dry_run: bool,
    auto: bool,
    telemetry: &crate::telemetry::Telemetry,
) -> Result<()> {
    let options = FixOptions {
        apply,
        no_ai,
        dry_run,
        auto,
    };

    // Load findings from last analysis
    let findings_path = crate::cache::findings_cache_path(path);
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

    if findings.is_empty() {
        println!("{} No findings to fix!", style("✓").green().bold());
        return Ok(());
    }

    match index {
        Some(idx) => {
            // Single finding mode
            if idx == 0 || idx > findings.len() {
                anyhow::bail!(
                    "Invalid finding index: {}. Valid range: 1-{}",
                    idx,
                    findings.len()
                );
            }
            let finding = &findings[idx - 1];
            run_single_fix(path, finding, idx, options, telemetry)
        }
        None => {
            // Batch mode - fix all fixable findings
            run_batch_fix(path, &findings, options, telemetry)
        }
    }
}

/// Send a fix_applied telemetry event
fn send_fix_applied_telemetry(
    path: &Path,
    finding: &Finding,
    ai: bool,
    accepted: bool,
    ai_provider: Option<String>,
    telemetry: &crate::telemetry::Telemetry,
) {
    if let crate::telemetry::Telemetry::Active(ref state) = *telemetry {
        if let Some(distinct_id) = &state.distinct_id {
            let event = crate::telemetry::events::FixApplied {
                repo_id: crate::telemetry::config::compute_repo_id(path),
                detector: finding.detector.clone(),
                fix_type: if ai { "ai".into() } else { "rule".into() },
                accepted,
                language: String::new(),
                ai_provider,
                version: env!("CARGO_PKG_VERSION").to_string(),
            };
            let props = serde_json::to_value(&event).unwrap_or_default();
            crate::telemetry::posthog::capture_queued("fix_applied", distinct_id, props);
        }
    }
}

/// Run fixes for all fixable findings
fn run_batch_fix(path: &Path, findings: &[Finding], options: FixOptions, telemetry: &crate::telemetry::Telemetry) -> Result<()> {
    let term = Term::stderr();

    // Find all findings that have rule-based fixes available
    let fixable: Vec<(usize, &Finding, RuleFix)> = findings
        .iter()
        .enumerate()
        .filter_map(|(i, f)| generate_rule_fix(f, path).map(|rule_fix| (i + 1, f, rule_fix)))
        .collect();

    if fixable.is_empty() {
        term.write_line(&format!(
            "\n{} No auto-fixable findings detected.",
            style("ℹ️").cyan()
        ))?;
        term.write_line(
            "Run `repotoire fix <index>` to see suggestions for individual findings.\n",
        )?;
        return Ok(());
    }

    // Count how many have patches (auto-applicable)
    let auto_applicable: Vec<_> = fixable
        .iter()
        .filter(|(_, _, rf)| rf.auto_applicable && rf.patch.is_some())
        .collect();

    term.write_line(&format!(
        "\n{} Found {} fixable findings ({} auto-applicable)\n",
        style("📋").cyan(),
        fixable.len(),
        auto_applicable.len()
    ))?;

    // Summary table
    term.write_line(&format!("{}", style("Fixable findings:").bold()))?;
    for (idx, finding, rule_fix) in &fixable {
        let auto_badge = if rule_fix.auto_applicable {
            style("[auto]").green()
        } else {
            style("[manual]").yellow()
        };
        term.write_line(&format!(
            "  {} #{}: {} - {}",
            auto_badge,
            idx,
            style(&finding.detector).dim(),
            finding.title.chars().take(60).collect::<String>()
        ))?;
    }
    term.write_line("")?;

    if options.dry_run {
        // Dry run mode - show what would be fixed
        term.write_line(&format!(
            "{} Dry run mode - showing what would be fixed:\n",
            style("🔍").cyan()
        ))?;

        for (idx, finding, rule_fix) in &fixable {
            display_dry_run_finding(&term, *idx, finding, rule_fix)?;
        }

        term.write_line(&format!(
            "{} To apply fixes, run: {}",
            style("💡").cyan(),
            style("repotoire fix --auto").cyan()
        ))?;
        term.write_line("")?;
        return Ok(());
    }

    // Apply mode
    if !options.apply && !options.auto {
        // Just show summary without applying
        term.write_line(&format!(
            "{} To preview changes:   {}",
            style("💡").cyan(),
            style("repotoire fix --dry-run").cyan()
        ))?;
        term.write_line(&format!(
            "   To apply all fixes:  {}",
            style("repotoire fix --auto").cyan()
        ))?;
        term.write_line(&format!(
            "   To fix one finding:  {}\n",
            style("repotoire fix <index> --apply").cyan()
        ))?;
        return Ok(());
    }

    // Auto mode - apply all fixes
    let mut applied = 0;
    let mut skipped = 0;
    let mut failed = 0;

    for (idx, finding, rule_fix) in &fixable {
        if !rule_fix.auto_applicable || rule_fix.patch.is_none() {
            skipped += 1;
            continue;
        }

        let file_path = match finding.affected_files.first() {
            Some(f) => f,
            None => {
                skipped += 1;
                continue;
            }
        };

        // Ask for confirmation unless --auto is set
        if !options.auto {
            term.write_line(&format!(
                "\n{} #{}: {} in {}",
                style("Fix").cyan().bold(),
                idx,
                rule_fix.title,
                file_path.display()
            ))?;

            if let Some(ref patch) = rule_fix.patch {
                display_patch_preview(&term, patch)?;
            }

            if !confirm("Apply this fix?")? {
                skipped += 1;
                continue;
            }
        }

        // Apply the fix
        match apply_rule_fix(path, file_path, finding, rule_fix) {
            Ok(()) => {
                applied += 1;
                send_fix_applied_telemetry(path, finding, false, true, None, telemetry);
                term.write_line(&format!(
                    "{} Applied fix #{}: {}",
                    style("✓").green(),
                    idx,
                    rule_fix.title
                ))?;
            }
            Err(e) => {
                failed += 1;
                send_fix_applied_telemetry(path, finding, false, false, None, telemetry);
                term.write_line(&format!(
                    "{} Failed to apply fix #{}: {}",
                    style("✗").red(),
                    idx,
                    e
                ))?;
            }
        }
    }

    term.write_line("")?;
    term.write_line(&format!(
        "{} {} applied, {} skipped, {} failed",
        style("Summary:").bold(),
        style(applied).green(),
        style(skipped).yellow(),
        if failed > 0 {
            style(failed).red().to_string()
        } else {
            style(failed).dim().to_string()
        }
    ))?;

    if applied > 0 {
        term.write_line(&format!(
            "\n{} Re-run analysis to verify: {}",
            style("💡").cyan(),
            style("repotoire analyze").cyan()
        ))?;
    }
    term.write_line("")?;

    Ok(())
}

/// Apply a rule-based fix to a file
fn apply_rule_fix(
    repo_path: &Path,
    file_path: &Path,
    finding: &Finding,
    _rule_fix: &RuleFix,
) -> Result<()> {
    let full_path = repo_path.join(file_path);
    let content = fs::read_to_string(&full_path)
        .with_context(|| format!("Failed to read {}", full_path.display()))?;

    let lines: Vec<&str> = content.lines().collect();

    let line_start = finding
        .line_start
        .ok_or_else(|| anyhow::anyhow!("Finding has no line number"))?
        as usize;
    let line_end = finding.line_end.unwrap_or(finding.line_start.unwrap_or(0)) as usize;

    if line_start == 0 || line_start > lines.len() {
        anyhow::bail!("Invalid line range: {}-{}", line_start, line_end);
    }

    // For unused imports and similar - just remove the lines
    let detector = finding.detector.as_str();
    let new_content = match detector {
        "UnusedImportsDetector" => {
            // Remove the import lines
            lines.iter().enumerate()
                .filter(|(i, _)| { let n = i + 1; n < line_start || n > line_end })
                .map(|(_, line)| *line)
                .collect::<Vec<_>>()
                .join("\n")
        }
        _ => {
            // For other detectors, we'd need more sophisticated patching
            anyhow::bail!(
                "Auto-apply not implemented for detector: {}. Please apply manually.",
                detector
            );
        }
    };

    // Write back with original line ending style
    let has_trailing_newline = content.ends_with('\n');
    let final_content = if has_trailing_newline && !new_content.ends_with('\n') {
        format!("{}\n", new_content)
    } else {
        new_content
    };

    fs::write(&full_path, final_content)
        .with_context(|| format!("Failed to write {}", full_path.display()))?;

    Ok(())
}

/// Run fix for a single finding
fn run_single_fix(path: &Path, finding: &Finding, index: usize, options: FixOptions, telemetry: &crate::telemetry::Telemetry) -> Result<()> {
    // If --no-ai flag is set, use rule-based fixes directly
    if options.no_ai {
        return run_rule_fix(path, finding, index, options, telemetry);
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
        Some(client) => run_ai_fix(path, finding, index, options, client, telemetry),
        None => run_rule_fix(path, finding, index, options, telemetry),
    }
}

/// Run AI-powered fix generation
fn run_ai_fix(
    path: &Path,
    finding: &Finding,
    index: usize,
    options: FixOptions,
    client: AiClient,
    telemetry: &crate::telemetry::Telemetry,
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
    // Capture AI provider name before consuming client
    let ai_provider_name = Some(match client.backend() {
        LlmBackend::Anthropic => "anthropic".to_string(),
        LlmBackend::OpenAi => "openai".to_string(),
        LlmBackend::Deepinfra => "deepinfra".to_string(),
        LlmBackend::OpenRouter => "openrouter".to_string(),
        LlmBackend::Ollama => "ollama".to_string(),
    });

    // Sync — no runtime needed (ureq)
    let generator = FixGenerator::new(client);

    let fix = generator.generate_fix_with_retry(finding, path, 2)?;

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

    // Dry run - just show, don't apply
    if options.dry_run {
        term.write_line(&format!(
            "\n{} Dry run mode - no changes applied.",
            style("🔍").cyan()
        ))?;
        term.write_line(&format!(
            "   To apply: {}\n",
            style(format!("repotoire fix {} --apply", index)).cyan()
        ))?;
        return Ok(());
    }

    // Apply if requested (--apply or --auto)
    let should_apply = options.apply || options.auto;

    if should_apply {
        if !fix.syntax_valid && !options.auto {
            term.write_line(&format!(
                "\n{} Fix has syntax errors, not applying automatically.",
                style("Warning:").yellow().bold()
            ))?;
            term.write_line("Review the changes and apply manually if appropriate.\n")?;
        } else {
            // Confirm unless --auto is set
            let confirmed = if options.auto {
                true
            } else {
                confirm("Apply this fix?")?
            };

            if confirmed {
                term.write_line(&format!("\n{} Applying fix...", style("⚡").cyan()))?;
                fix.apply(path)?;
                send_fix_applied_telemetry(path, finding, true, true, ai_provider_name, telemetry);
                term.write_line(&format!(
                    "{} Fix applied successfully!\n",
                    style("✓").green().bold()
                ))?;
            } else {
                send_fix_applied_telemetry(path, finding, true, false, ai_provider_name, telemetry);
                term.write_line(&format!("\n{} Fix not applied.\n", style("ℹ️").dim()))?;
            }
        }
    } else {
        term.write_line(&format!(
            "\n{} To apply this fix, run:",
            style("Tip:").cyan().bold()
        ))?;
        term.write_line(&format!("  repotoire fix {} --apply\n", index))?;
    }

    // Save fix proposal
    let fixes_dir = crate::cache::cache_dir(path).join("fixes");
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
fn run_rule_fix(path: &Path, finding: &Finding, index: usize, options: FixOptions, telemetry: &crate::telemetry::Telemetry) -> Result<()> {
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
        Some(rule_fix) => display_rule_fix(&term, &rule_fix, finding, index, options, path, telemetry),
        None => display_fallback_suggestion(&term, finding),
    }
}

/// Display a rule-based fix
fn display_rule_fix(
    term: &Term,
    rule_fix: &RuleFix,
    finding: &Finding,
    index: usize,
    options: FixOptions,
    path: &Path,
    telemetry: &crate::telemetry::Telemetry,
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

    // Dry run mode
    if options.dry_run {
        term.write_line(&format!(
            "{} Dry run mode - no changes applied.",
            style("🔍").cyan()
        ))?;
        if rule_fix.auto_applicable {
            term.write_line(&format!(
                "   To apply: {}\n",
                style(format!("repotoire fix {} --apply", index)).cyan()
            ))?;
        }
        return Ok(());
    }

    // Auto-apply logic
    let should_apply = options.apply || options.auto;

    if rule_fix.auto_applicable && rule_fix.patch.is_some() && !should_apply {
        term.write_line(&format!(
            "{} This fix can be auto-applied.",
            style("💡").cyan()
        ))?;
        term.write_line(&format!(
            "   Run {}\n",
            style(format!("repotoire fix {} --apply", index)).cyan()
        ))?;
    } else if rule_fix.auto_applicable && rule_fix.patch.is_some() && should_apply {
        let confirmed = if options.auto { true } else { confirm("Apply this fix?")? };

        if !confirmed {
            send_fix_applied_telemetry(path, finding, false, false, None, telemetry);
            term.write_line(&format!("\n{} Fix not applied.\n", style("ℹ️").dim()))?;
        } else {
            let file_path = finding
                .affected_files
                .first()
                .ok_or_else(|| anyhow::anyhow!("No file path in finding"))?;

            let apply_result = apply_rule_fix(path, file_path, finding, rule_fix);
            let accepted = apply_result.is_ok();
            send_fix_applied_telemetry(path, finding, false, accepted, None, telemetry);
            display_apply_result(term, apply_result)?;
        }
    } else if !should_apply {
        // AI upgrade suggestion for non-applicable fixes
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
    }

    Ok(())
}

/// Display fallback suggestion when no rule-based fix is available
fn display_fallback_suggestion(term: &Term, finding: &Finding) -> Result<()> {
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
