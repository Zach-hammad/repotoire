//! Doctor command - verify environment and detect issues (#37)

use anyhow::Result;

pub fn run() -> Result<()> {
    println!("🩺 Repotoire Doctor\n");

    let mut issues = 0;

    // Check 1: Tree-sitter parsers actually load
    match check_tree_sitter() {
        Ok(langs) => println!("✓ Tree-sitter parsers: {} languages available", langs),
        Err(e) => {
            println!("✗ Tree-sitter parsers: {}", e);
            issues += 1;
        }
    }

    // Check 2: Git available
    match std::process::Command::new("git").arg("--version").output() {
        Ok(output) if output.status.success() => {
            let version = String::from_utf8_lossy(&output.stdout).trim().to_string();
            println!("✓ Git: {}", version);
        }
        _ => {
            println!("✗ Git: not found (required for --since and incremental mode)");
            issues += 1;
        }
    }

    // Check 3: Current directory is a valid repo
    let cwd = std::env::current_dir().unwrap_or_default();
    let repotoire_dir = cwd.join(".repotoire");
    if repotoire_dir.exists() {
        println!("✓ Project: initialized (.repotoire/ exists)");

        // Check cache health
        let cache_dir = repotoire_dir.join("incremental");
        if cache_dir.exists() {
            let cache_files: usize = std::fs::read_dir(&cache_dir)
                .map(|d| d.count())
                .unwrap_or(0);
            println!("  Cache: {} files in .repotoire/incremental/", cache_files);
        }
    } else {
        println!("○ Project: not initialized (run `repotoire analyze` to start)");
    }

    // Check 4: Config file
    let config_names = [
        "repotoire.toml",
        ".repotoire.json",
        ".repotoire.yaml",
        ".repotoire.yml",
    ];
    let config_found = config_names.iter().any(|name| cwd.join(name).exists());
    if config_found {
        // Safe: config_found is true, so at least one name matches
        if let Some(found) = config_names.iter().find(|name| cwd.join(name).exists()) {
            println!("✓ Config: {} found", found);
        }
    } else {
        println!("○ Config: none (using defaults)");
    }

    // Check 5: AI providers (all optional - BYOK)
    let has_openai = std::env::var("OPENAI_API_KEY").is_ok();
    let has_anthropic = std::env::var("ANTHROPIC_API_KEY").is_ok();
    let has_deepseek = std::env::var("DEEPSEEK_API_KEY").is_ok();
    let has_ollama = std::process::Command::new("ollama")
        .arg("list")
        .output()
        .map(|o| o.status.success())
        .unwrap_or(false);

    if has_openai || has_anthropic || has_deepseek || has_ollama {
        let mut providers = Vec::new();
        if has_openai {
            providers.push("OpenAI");
        }
        if has_anthropic {
            providers.push("Anthropic");
        }
        if has_deepseek {
            providers.push("DeepSeek");
        }
        if has_ollama {
            providers.push("Ollama");
        }
        println!(
            "✓ AI providers: {} (AI fixes enabled)",
            providers.join(", ")
        );
    } else {
        println!("○ AI providers: none configured");
        println!("  Set OPENAI_API_KEY, ANTHROPIC_API_KEY, DEEPSEEK_API_KEY, or install Ollama for AI fixes");
    }

    // Check 6: Disk space
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        if let Ok(meta) = std::fs::metadata("/") {
            let _ = meta.dev(); // Just verify fs access works
            println!("✓ Filesystem: accessible");
        }
    }

    // Summary
    println!();
    if issues == 0 {
        println!("✅ All checks passed!");
    } else {
        println!("⚠️  {} issue(s) found", issues);
    }

    Ok(())
}

fn check_tree_sitter() -> Result<usize, String> {
    // Actually try to create parsers for each supported language
    let languages = [
        ("Python", tree_sitter_python::LANGUAGE),
        ("JavaScript", tree_sitter_javascript::LANGUAGE),
        ("TypeScript", tree_sitter_typescript::LANGUAGE_TYPESCRIPT),
        ("Go", tree_sitter_go::LANGUAGE),
        ("Rust", tree_sitter_rust::LANGUAGE),
        ("Java", tree_sitter_java::LANGUAGE),
        ("C", tree_sitter_c::LANGUAGE),
        ("C++", tree_sitter_cpp::LANGUAGE),
    ];

    let mut count = 0;
    let mut failures = Vec::new();

    for (name, lang) in &languages {
        let mut parser = tree_sitter::Parser::new();
        match parser.set_language(&(*lang).into()) {
            Ok(()) => count += 1,
            Err(e) => failures.push(format!("{}: {}", name, e)),
        }
    }

    if failures.is_empty() {
        Ok(count)
    } else {
        Err(format!("Failed: {}", failures.join(", ")))
    }
}
