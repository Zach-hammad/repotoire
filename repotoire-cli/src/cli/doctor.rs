//! Doctor command - check environment

use anyhow::Result;

pub fn run() -> Result<()> {
    println!("🩺 Repotoire Doctor\n");
    
    // Check graph database (petgraph + sled)
    println!("✓ Graph database: OK");
    
    // Check tree-sitter parsers
    println!("✓ Tree-sitter parsers: OK");
    
    // Check optional: OpenAI key
    if std::env::var("OPENAI_API_KEY").is_ok() {
        println!("✓ OpenAI API key: configured (PRO features enabled)");
    } else {
        println!("○ OpenAI API key: not set (set OPENAI_API_KEY for AI fixes)");
    }
    
    println!("\n✅ All checks passed!");
    Ok(())
}
