//! Token N-gram model for code surprisal analysis
//!
//! Builds a statistical model of "how this project writes code" from token sequences.
//! Lines/functions with high surprisal (low probability under the model) are flagged
//! as unusual — possibly AI-generated, buggy, or inconsistent with project style.
//!
//! Based on: "On the Naturalness of Buggy Code" (Ray & Hellendoorn, 2015)
//! Key insight: buggy lines have significantly higher entropy than correct code.

use std::collections::HashMap;

/// Order of the n-gram model (trigrams balance precision vs sparsity)
const DEFAULT_ORDER: usize = 3;

/// Minimum token count before the model is considered reliable
const MIN_TOKENS_FOR_CONFIDENCE: usize = 5000;

/// A token n-gram language model that learns project coding patterns.
/// Uses simple smoothed n-gram counts — no ML, no external deps.
#[derive(Debug, Clone)]
pub struct NgramModel {
    /// N-gram order (3 = trigrams)
    order: usize,
    /// Counts: ngram_str -> count
    counts: HashMap<String, u32>,
    /// Context counts: (n-1)-gram prefix -> total count
    context_counts: HashMap<String, u32>,
    /// Unigram counts for backoff
    unigram_counts: HashMap<String, u32>,
    /// Total tokens seen
    total_tokens: usize,
    /// Vocabulary size (unique tokens)
    vocab_size: usize,
    /// Whether model has enough data to be useful
    confident: bool,
}

impl NgramModel {
    pub fn new() -> Self {
        Self {
            order: DEFAULT_ORDER,
            counts: HashMap::new(),
            context_counts: HashMap::new(),
            unigram_counts: HashMap::new(),
            total_tokens: 0,
            vocab_size: 0,
            confident: false,
        }
    }

    /// Feed a source file's tokens into the model. Call this for each file during calibration.
    pub fn train_on_tokens(&mut self, tokens: &[String]) {
        if tokens.len() < self.order {
            return;
        }

        for token in tokens {
            *self.unigram_counts.entry(token.clone()).or_insert(0) += 1;
        }

        // Build n-gram and (n-1)-gram counts
        for window in tokens.windows(self.order) {
            let ngram = window.join(" ");
            let context = window[..self.order - 1].join(" ");

            *self.counts.entry(ngram).or_insert(0) += 1;
            *self.context_counts.entry(context).or_insert(0) += 1;
        }

        self.total_tokens += tokens.len();
        self.vocab_size = self.unigram_counts.len();
        self.confident = self.total_tokens >= MIN_TOKENS_FOR_CONFIDENCE;
    }

    /// Tokenize a source line into abstract tokens.
    /// Normalizes identifiers to reduce sparsity while keeping structure.
    pub fn tokenize_line(line: &str) -> Vec<String> {
        let trimmed = line.trim();
        if trimmed.is_empty() || trimmed.starts_with("//") || trimmed.starts_with('#') {
            return vec![];
        }

        let mut tokens = Vec::new();
        let mut chars = trimmed.chars().peekable();

        while let Some(&ch) = chars.peek() {
            match ch {
                // Whitespace — skip
                ' ' | '\t' => { chars.next(); }

                // String literals → normalize to <STR>
                '"' | '\'' | '`' => {
                    let quote = ch;
                    chars.next();
                    while let Some(&c) = chars.peek() {
                        chars.next();
                        if c == quote { break; }
                        if c == '\\' { chars.next(); } // skip escaped
                    }
                    tokens.push("<STR>".to_string());
                }

                // Numbers → normalize to <NUM>
                '0'..='9' => {
                    while chars.peek().map_or(false, |c| c.is_ascii_alphanumeric() || *c == '.' || *c == 'x' || *c == '_') {
                        chars.next();
                    }
                    tokens.push("<NUM>".to_string());
                }

                // Identifiers and keywords
                'a'..='z' | 'A'..='Z' | '_' => {
                    let mut word = String::new();
                    while chars.peek().map_or(false, |c| c.is_ascii_alphanumeric() || *c == '_') {
                        if let Some(c) = chars.next() {
                            word.push(c);
                        }
                    }
                    // Keep keywords as-is, normalize identifiers by pattern
                    if is_keyword(&word) {
                        tokens.push(word);
                    } else if word.chars().all(|c| c.is_uppercase() || c == '_') {
                        tokens.push("<CONST>".to_string()); // SCREAMING_CASE constant
                    } else if word.starts_with(|c: char| c.is_uppercase()) {
                        tokens.push("<TYPE>".to_string()); // PascalCase type
                    } else {
                        tokens.push("<ID>".to_string()); // snake_case / camelCase identifier
                    }
                }

                // Operators and punctuation — keep as-is (they carry structure)
                _ => {
                    tokens.push(consume_operator(&mut chars));
                }
            }
        }

        tokens
    }

    /// Tokenize an entire source file into a flat token sequence with line boundaries.
    pub fn tokenize_file(content: &str) -> Vec<String> {
        let mut tokens = Vec::new();
        for line in content.lines() {
            let line_tokens = Self::tokenize_line(line);
            if !line_tokens.is_empty() {
                tokens.extend(line_tokens);
                tokens.push("<EOL>".to_string()); // line boundary marker
            }
        }
        tokens
    }

    /// Calculate the surprisal (negative log probability) of a token sequence.
    /// Higher surprisal = more "surprising" / unusual code.
    /// Uses Kneser-Ney-style backoff with add-k smoothing.
    pub fn surprisal(&self, tokens: &[String]) -> f64 {
        if !self.confident || tokens.len() < self.order {
            return 0.0; // Not enough data to judge
        }

        let k = 0.1; // Smoothing constant
        let v = self.vocab_size.max(1) as f64;
        let mut total_surprisal = 0.0;
        let mut count = 0;

        for window in tokens.windows(self.order) {
            let ngram = window.join(" ");
            let context = window[..self.order - 1].join(" ");

            let ngram_count = *self.counts.get(&ngram).unwrap_or(&0) as f64;
            let context_count = *self.context_counts.get(&context).unwrap_or(&0) as f64;

            // Smoothed probability with backoff
            let prob = if context_count > 0.0 {
                (ngram_count + k) / (context_count + k * v)
            } else {
                // Backoff to unigram
                let target = &window[self.order - 1];
                let uni_count = *self.unigram_counts.get(target).unwrap_or(&0) as f64;
                (uni_count + k) / (self.total_tokens as f64 + k * v)
            };

            total_surprisal += -prob.log2();
            count += 1;
        }

        if count > 0 {
            total_surprisal / count as f64 // Average bits per token
        } else {
            0.0
        }
    }

    /// Score a single line's surprisal against the model.
    pub fn line_surprisal(&self, line: &str) -> f64 {
        let tokens = Self::tokenize_line(line);
        if tokens.len() < self.order {
            return 0.0;
        }
        self.surprisal(&tokens)
    }

    /// Score a function's token sequence. Returns (avg_surprisal, max_line_surprisal, peak_line).
    pub fn function_surprisal(&self, lines: &[&str]) -> (f64, f64, usize) {
        let mut total = 0.0;
        let mut max_surprisal = 0.0f64;
        let mut max_line = 0;
        let mut scored_lines = 0;

        for (i, line) in lines.iter().enumerate() {
            let s = self.line_surprisal(line);
            if s > 0.0 {
                total += s;
                scored_lines += 1;
                if s > max_surprisal {
                    max_surprisal = s;
                    max_line = i;
                }
            }
        }

        let avg = if scored_lines > 0 { total / scored_lines as f64 } else { 0.0 };
        (avg, max_surprisal, max_line)
    }

    /// Get the model's baseline stats: mean and stddev of per-line surprisal across all training data.
    pub fn baseline_stats(&self) -> (f64, f64) {
        // We don't store per-line scores during training, so this needs to be computed
        // after training by scoring a sample. For now, return estimates.
        // A well-fitted n-gram model on code typically has mean ~3-5 bits, stddev ~1-2 bits.
        (0.0, 0.0) // Placeholder — computed externally
    }

    pub fn is_confident(&self) -> bool {
        self.confident
    }

    pub fn total_tokens(&self) -> usize {
        self.total_tokens
    }

    pub fn vocab_size(&self) -> usize {
        self.vocab_size
    }

    /// Save model stats (not the full model — that's too large) to a JSON-compatible struct
    pub fn stats_json(&self) -> serde_json::Value {
        serde_json::json!({
            "order": self.order,
            "total_tokens": self.total_tokens,
            "vocab_size": self.vocab_size,
            "ngram_count": self.counts.len(),
            "confident": self.confident,
        })
    }
}

impl Default for NgramModel {
    fn default() -> Self {
        Self::new()
    }
}

/// Greedily consume a multi-char operator from the char stream.
fn consume_operator(chars: &mut std::iter::Peekable<std::str::Chars>) -> String {
    let mut op = String::new();
    let Some(first) = chars.next() else {
        return op;
    };
    op.push(first);

    let Some(&next) = chars.peek() else { return op };
    let two = format!("{}{}", op, next);
    if !matches!(two.as_str(), "==" | "!=" | ">=" | "<=" | "&&" | "||"
        | "->" | "=>" | "::" | "+=" | "-=" | "*=" | "/=" | ".." | "<<" | ">>") {
        return op;
    }
    chars.next();
    op = two;

    let Some(&third) = chars.peek() else { return op };
    let three = format!("{}{}", op, third);
    if matches!(three.as_str(), "===" | "!==" | "..." | ">>>" | "<<=" | ">>=") {
        chars.next();
        op = three;
    }
    op
}

/// Check if a token is a language keyword (kept as-is for structural signal).
/// Combined set across Rust, Python, JS/TS, Go, Java, C#, Kotlin, C/C++ — deduplicated.
fn is_keyword(word: &str) -> bool {
    matches!(word,
        // Control flow (shared across languages)
        "if" | "else" | "elif" | "for" | "while" | "do" | "loop"
        | "break" | "continue" | "return" | "yield" | "switch" | "case" | "default"
        | "match" | "when" | "select" | "range"
        // Error handling
        | "try" | "catch" | "except" | "finally" | "throw" | "throws" | "raise"
        // Declarations
        | "fn" | "func" | "def" | "function" | "let" | "var" | "val" | "const"
        | "static" | "auto" | "type" | "typedef"
        // OOP / types
        | "class" | "struct" | "enum" | "trait" | "interface" | "impl"
        | "extends" | "implements" | "abstract" | "sealed" | "final"
        | "override" | "virtual" | "explicit" | "friend" | "operator"
        | "object" | "companion" | "data"
        // Visibility
        | "pub" | "private" | "protected" | "public" | "readonly"
        // Modules / imports
        | "use" | "mod" | "import" | "export" | "from" | "package"
        | "as" | "crate" | "super" | "namespace" | "include"
        // Memory / ownership (Rust)
        | "mut" | "ref" | "move" | "dyn" | "unsafe" | "extern"
        // Async
        | "async" | "await" | "defer" | "go"
        // Literals / builtins
        | "true" | "false" | "True" | "False" | "null" | "nil" | "None"
        | "undefined" | "NaN" | "Infinity"
        | "self" | "Self" | "this" | "new" | "delete" | "del"
        // Rust specific types
        | "Box" | "Vec" | "Option" | "Result" | "Some" | "Ok" | "Err"
        // Logic operators (Python)
        | "and" | "or" | "not" | "is" | "in"
        // Python specific
        | "lambda" | "pass" | "assert" | "global" | "nonlocal" | "with"
        // JS/TS specific
        | "typeof" | "instanceof" | "void"
        // Go specific
        | "chan" | "map" | "make" | "append" | "len" | "cap"
        // Java/C# specific
        | "synchronized" | "volatile" | "transient" | "native"
        // C/C++ specific
        | "register" | "sizeof" | "union" | "goto" | "inline" | "restrict"
        | "template" | "noexcept" | "constexpr"
        // Preprocessor
        | "define" | "ifdef" | "ifndef" | "endif" | "pragma"
        // Misc
        | "where"
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_tokenize_line() {
        let tokens = NgramModel::tokenize_line("let mut count = 0;");
        assert_eq!(tokens, vec!["let", "mut", "<ID>", "=", "<NUM>", ";"]);
    }

    #[test]
    fn test_tokenize_string_literal() {
        let tokens = NgramModel::tokenize_line(r#"println!("hello world");"#);
        assert!(tokens.contains(&"<STR>".to_string()));
    }

    #[test]
    fn test_tokenize_type() {
        let tokens = NgramModel::tokenize_line("let x: HashMap<String, u32> = HashMap::new();");
        assert!(tokens.contains(&"<TYPE>".to_string()));
    }

    #[test]
    fn test_model_training() {
        let mut model = NgramModel::new();

        // Train on repetitive code (need 5000+ tokens for confidence)
        for _ in 0..800 {
            model.train_on_tokens(&vec![
                "let".to_string(), "mut".to_string(), "<ID>".to_string(),
                "=".to_string(), "<NUM>".to_string(), ";".to_string(), "<EOL>".to_string(),
            ]);
        }

        assert!(model.total_tokens() > 1000);
        assert!(model.is_confident());
    }

    #[test]
    fn test_surprisal_familiar_vs_unusual() {
        let mut model = NgramModel::new();

        // Train on a pattern
        for _ in 0..500 {
            model.train_on_tokens(&vec![
                "let".to_string(), "<ID>".to_string(), "=".to_string(),
                "<ID>".to_string(), ".".to_string(), "<ID>".to_string(),
                "(". to_string(), ")".to_string(), ";".to_string(), "<EOL>".to_string(),
            ]);
        }

        // Familiar pattern should have LOW surprisal
        let familiar = vec![
            "let".to_string(), "<ID>".to_string(), "=".to_string(),
            "<ID>".to_string(), ".".to_string(), "<ID>".to_string(),
            "(".to_string(), ")".to_string(), ";".to_string(),
        ];

        // Unusual pattern should have HIGH surprisal
        let unusual = vec![
            "unsafe".to_string(), "{".to_string(), "<ID>".to_string(),
            "::".to_string(), "<ID>".to_string(), "(".to_string(),
            "&".to_string(), "mut".to_string(), "<ID>".to_string(),
        ];

        let s_familiar = model.surprisal(&familiar);
        let s_unusual = model.surprisal(&unusual);

        assert!(s_unusual > s_familiar, "Unusual code ({:.2}) should be more surprising than familiar code ({:.2})", s_unusual, s_familiar);
    }

    #[test]
    fn test_not_confident_returns_zero() {
        let model = NgramModel::new(); // Empty model
        let tokens = vec!["let".to_string(), "<ID>".to_string(), "=".to_string()];
        assert_eq!(model.surprisal(&tokens), 0.0);
    }
}
