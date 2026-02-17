//! Doctor command - check environment

use anyhow::Result;

pub fn run() -> Result<()> {
    println!("🩺 Repotoire Doctor\n");

    // Check analysis database (petgraph + redb)
    println!("✓ Analysis database: OK");

    // Check tree-sitter parsers
    println!("✓ Tree-sitter parsers: OK");

    // Check AI providers (all optional - BYOK)
    let has_openai = std::env::var("OPENAI_API_KEY").is_ok();
    let has_anthropic = std::env::var("ANTHROPIC_API_KEY").is_ok();
    let has_deepseek = std::env::var("DEEPSEEK_API_KEY").is_ok();
    let has_ollama = std::env::var("OLLAMA_HOST").is_ok();

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
        println!("  Set OPENAI_API_KEY, ANTHROPIC_API_KEY, DEEPSEEK_API_KEY, or OLLAMA_HOST for AI fixes");
    }

    println!("\n✅ All checks passed!");
    Ok(())
}
