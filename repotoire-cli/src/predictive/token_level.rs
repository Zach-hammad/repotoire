//! L1: Per-language token surprisal using n-gram models.
//!
//! Wraps `NgramModel` with per-language separation. Research shows that code
//! naturalness is language-specific — a pattern normal in Python may be unusual
//! in Rust. This scorer trains one n-gram model per detected language (keyed by
//! normalized file extension) so surprisal scores reflect language-local norms.

use crate::calibrate::NgramModel;
use std::collections::HashMap;

/// Per-language token surprisal scorer.
///
/// Maintains a separate `NgramModel` for each programming language encountered
/// during training. At scoring time, the model for the target language is used
/// so that surprisal reflects how unusual code is *within that language*.
pub struct TokenLevelScorer {
    pub models: HashMap<String, NgramModel>,
}

impl TokenLevelScorer {
    pub fn new() -> Self {
        Self {
            models: HashMap::new(),
        }
    }

    /// Feed source content into the per-language model.
    ///
    /// The `extension` should be the file extension without the leading dot
    /// (e.g. `"rs"`, `"py"`, `"tsx"`). Extensions are normalized so that
    /// related variants (ts/tsx, js/jsx, cc/cpp/cxx/hpp) share a single model.
    pub fn train_file(&mut self, content: &str, extension: &str) {
        let lang = normalize_extension(extension);
        let model = self.models.entry(lang).or_insert_with(NgramModel::new);
        let tokens = NgramModel::tokenize_file(content);
        model.train_on_tokens(&tokens);
    }

    /// Score a function's lines. Returns average surprisal in bits.
    ///
    /// Returns `0.0` if no model exists for the language or if the model is not
    /// yet confident (has seen fewer than 5 000 tokens).
    pub fn score_function(&self, lines: &[&str], extension: &str) -> f64 {
        let lang = normalize_extension(extension);
        let Some(model) = self.models.get(&lang) else {
            return 0.0;
        };
        if !model.is_confident() {
            return 0.0;
        }
        let (avg, _, _) = model.function_surprisal(lines);
        avg
    }

    /// Check if the model has enough training data for a given language.
    pub fn is_confident(&self, extension: &str) -> bool {
        let lang = normalize_extension(extension);
        self.models.get(&lang).map_or(false, |m| m.is_confident())
    }
}

impl Default for TokenLevelScorer {
    fn default() -> Self {
        Self::new()
    }
}

/// Map file extensions to a canonical language key so that related variants
/// share a single n-gram model.
fn normalize_extension(ext: &str) -> String {
    match ext {
        "ts" | "tsx" => "ts".to_string(),
        "js" | "jsx" => "js".to_string(),
        "cc" | "cpp" | "cxx" | "hpp" => "cpp".to_string(),
        "h" => "c".to_string(),
        other => other.to_string(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Helper: generate a chunk of repetitive Rust-like token source that,
    /// when tokenized, produces enough tokens to cross the 5 000-token
    /// confidence threshold.
    fn rust_training_source() -> String {
        // Each line yields ~6 tokens + EOL marker = ~7 tokens.
        // 800 lines * 7 = 5 600 tokens, comfortably above the 5 000 minimum.
        (0..800)
            .map(|i| format!("let mut count_{i} = {i};"))
            .collect::<Vec<_>>()
            .join("\n")
    }

    /// Helper: generate a chunk of repetitive Python-like source.
    fn python_training_source() -> String {
        (0..800)
            .map(|i| format!("result_{i} = process(value_{i})"))
            .collect::<Vec<_>>()
            .join("\n")
    }

    #[test]
    fn test_per_language_models_trained_separately() {
        let mut scorer = TokenLevelScorer::new();
        scorer.train_file(&rust_training_source(), "rs");
        scorer.train_file(&python_training_source(), "py");

        assert_eq!(scorer.models.len(), 2, "Expected 2 separate models (rs + py)");
        assert!(scorer.models.contains_key("rs"), "Missing Rust model");
        assert!(scorer.models.contains_key("py"), "Missing Python model");

        // Each model should have received tokens independently.
        assert!(scorer.models["rs"].total_tokens() > 0);
        assert!(scorer.models["py"].total_tokens() > 0);
    }

    #[test]
    fn test_score_function_returns_zero_without_confidence() {
        let scorer = TokenLevelScorer::new(); // no training at all
        let lines = vec!["let x = 42;", "println!(x);"];
        let score = scorer.score_function(&lines, "rs");
        assert_eq!(score, 0.0, "Empty model should return 0.0");
    }

    #[test]
    fn test_score_function_returns_zero_for_low_confidence() {
        let mut scorer = TokenLevelScorer::new();
        // Train with very little data (well below 5 000 tokens).
        scorer.train_file("let x = 1;\nlet y = 2;\n", "rs");
        assert!(!scorer.is_confident("rs"), "Model should not be confident with so few tokens");

        let lines = vec!["let x = 42;"];
        let score = scorer.score_function(&lines, "rs");
        assert_eq!(score, 0.0, "Under-trained model should return 0.0");
    }

    #[test]
    fn test_normalize_extensions() {
        assert_eq!(normalize_extension("tsx"), "ts");
        assert_eq!(normalize_extension("ts"), "ts");
        assert_eq!(normalize_extension("jsx"), "js");
        assert_eq!(normalize_extension("js"), "js");
        assert_eq!(normalize_extension("cc"), "cpp");
        assert_eq!(normalize_extension("cpp"), "cpp");
        assert_eq!(normalize_extension("cxx"), "cpp");
        assert_eq!(normalize_extension("hpp"), "cpp");
        assert_eq!(normalize_extension("h"), "c");
        assert_eq!(normalize_extension("rs"), "rs");
        assert_eq!(normalize_extension("py"), "py");
        assert_eq!(normalize_extension("go"), "go");
    }

    #[test]
    fn test_is_confident_after_sufficient_training() {
        let mut scorer = TokenLevelScorer::new();
        assert!(!scorer.is_confident("rs"), "Should not be confident before training");

        scorer.train_file(&rust_training_source(), "rs");
        assert!(
            scorer.is_confident("rs"),
            "Should be confident after training with {} tokens",
            scorer.models["rs"].total_tokens()
        );
    }

    #[test]
    fn test_extension_variants_share_model() {
        let mut scorer = TokenLevelScorer::new();
        scorer.train_file("const x: number = 1;\n", "ts");
        scorer.train_file("const y: string = 'hi';\n", "tsx");

        assert_eq!(
            scorer.models.len(),
            1,
            "ts and tsx should share a single model"
        );
        assert!(scorer.models.contains_key("ts"));
    }

    #[test]
    fn test_score_function_produces_nonzero_for_confident_model() {
        let mut scorer = TokenLevelScorer::new();
        scorer.train_file(&rust_training_source(), "rs");
        assert!(scorer.is_confident("rs"));

        // Score lines that are unlike the training data.
        let lines = vec![
            "unsafe { std::ptr::write(addr, value) }",
            "extern \"C\" fn callback(ptr: *mut u8) -> i32 {",
        ];
        let score = scorer.score_function(&lines, "rs");
        assert!(
            score > 0.0,
            "Confident model should produce non-zero surprisal for unusual code, got {}",
            score
        );
    }

    #[test]
    fn test_unknown_language_returns_zero() {
        let scorer = TokenLevelScorer::new();
        let lines = vec!["some code here"];
        assert_eq!(
            scorer.score_function(&lines, "zig"),
            0.0,
            "Unknown language should return 0.0"
        );
        assert!(!scorer.is_confident("zig"));
    }
}
