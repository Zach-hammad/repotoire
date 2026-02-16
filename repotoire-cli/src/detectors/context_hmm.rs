//! Hidden Markov Model for function context classification
//!
//! Learns codebase-specific patterns to classify functions into roles:
//! - UTILITY: High fan-in expected, skip coupling warnings
//! - HANDLER: Callbacks/dispatch, skip dead code warnings  
//! - CORE: Main business logic, apply all detectors
//! - INTERNAL: Private helpers, lenient thresholds
//! - TEST: Test functions, skip most detectors
//!
//! The HMM is trained per-codebase using self-supervised learning from
//! call graph patterns and naming conventions.
//!
//! ## Research Notes
//!
//! Our implementation follows best practices from HMM literature:
//!
//! 1. **Gaussian Emissions**: We use continuous Gaussian emissions rather than
//!    discrete emissions because our features (fan-in ratio, complexity ratio)
//!    are naturally continuous. This avoids information loss from discretization.
//!
//! 2. **Viterbi Decoding**: For sequence classification, Viterbi finds the most
//!    likely state sequence in O(T*N²) time where T=sequence length, N=states.
//!
//! 3. **Bootstrap + Incremental Learning**: We initialize from heuristics (prior
//!    knowledge) then refine with Baum-Welch style updates. This is more robust
//!    than random initialization, especially for small codebases.
//!
//! 4. **Log-space Computation**: All probabilities computed in log-space to
//!    prevent numerical underflow with long sequences.
//!
//! ### Alternatives Considered
//!
//! - **CRF (Conditional Random Fields)**: Discriminative model, often better for
//!   classification. However, requires labeled training data. HMM can be trained
//!   unsupervised from call graph patterns.
//!
//! - **Neural Networks**: Could learn complex patterns but requires more data
//!   and adds heavy dependencies. HMM is lightweight and interpretable.
//!
//! - **Per-class HMMs**: Train separate HMM for each context type, classify by
//!   comparing likelihoods. We use single HMM for simplicity and to model
//!   transitions between contexts (e.g., test file → all functions are TEST).

use std::collections::HashMap;
use serde::{Deserialize, Serialize};

/// Function context/role classification
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub enum FunctionContext {
    /// Utility function - designed to be called from many places
    Utility,
    /// Handler/callback - called via dispatch tables or function pointers
    Handler,
    /// Core business logic - main functionality
    Core,
    /// Internal helper - private implementation details
    Internal,
    /// Test function - testing code
    Test,
}

impl FunctionContext {
    /// All possible states
    pub const ALL: [FunctionContext; 5] = [
        FunctionContext::Utility,
        FunctionContext::Handler,
        FunctionContext::Core,
        FunctionContext::Internal,
        FunctionContext::Test,
    ];

    /// Index for matrix operations
    pub fn index(&self) -> usize {
        match self {
            FunctionContext::Utility => 0,
            FunctionContext::Handler => 1,
            FunctionContext::Core => 2,
            FunctionContext::Internal => 3,
            FunctionContext::Test => 4,
        }
    }

    /// From index
    pub fn from_index(i: usize) -> Self {
        match i {
            0 => FunctionContext::Utility,
            1 => FunctionContext::Handler,
            2 => FunctionContext::Core,
            3 => FunctionContext::Internal,
            _ => FunctionContext::Test,
        }
    }

    /// Should skip coupling analysis for this context?
    pub fn skip_coupling(&self) -> bool {
        matches!(self, FunctionContext::Utility | FunctionContext::Handler | FunctionContext::Test)
    }

    /// Should skip dead code analysis for this context?
    pub fn skip_dead_code(&self) -> bool {
        matches!(self, FunctionContext::Handler | FunctionContext::Test)
    }

    /// Coupling threshold multiplier
    pub fn coupling_multiplier(&self) -> f64 {
        match self {
            FunctionContext::Utility => 3.0,   // Very lenient
            FunctionContext::Handler => 2.5,   // Lenient
            FunctionContext::Core => 1.0,      // Normal
            FunctionContext::Internal => 1.5,  // Slightly lenient
            FunctionContext::Test => 5.0,      // Very lenient (tests touch everything)
        }
    }
}

/// File-level context for hierarchical classification
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Default)]
pub enum FileContext {
    TestFile,      // test_*, *_test.*, *.test.*, *.spec.*
    UtilFile,      // utils/*, helpers/*, common/*
    HandlerFile,   // handlers/*, callbacks/*, hooks/*
    InternalFile,  // internal/*, _*, private/*
    #[default]
    SourceFile,    // Regular source file
}

impl FileContext {
    /// Classify a file based on its path
    pub fn from_path(path: &str) -> Self {
        let path_lower = path.to_lowercase();
        
        // Test files
        if path_lower.contains("/test")
            || path_lower.contains("_test.")
            || path_lower.contains(".test.")
            || path_lower.contains(".spec.")
            || path_lower.contains("/__tests__")
            || path_lower.contains("/__mocks__")
        {
            return FileContext::TestFile;
        }
        
        // Util/helper files
        if path_lower.contains("/util")
            || path_lower.contains("/utils")
            || path_lower.contains("/helper")
            || path_lower.contains("/helpers")
            || path_lower.contains("/common")
            || path_lower.contains("/shared")
            || path_lower.contains("/lib/")
        {
            return FileContext::UtilFile;
        }
        
        // Handler files
        if path_lower.contains("/handler")
            || path_lower.contains("/callback")
            || path_lower.contains("/hook")
            || path_lower.contains("/events")
        {
            return FileContext::HandlerFile;
        }
        
        // Internal files
        if path_lower.contains("/internal")
            || path_lower.contains("/private")
            || path_lower.contains("/_")
            || path_lower.contains("/pkg/")
        {
            return FileContext::InternalFile;
        }
        
        FileContext::SourceFile
    }
    
    /// Bias toward a function context based on file context
    pub fn function_bias(&self) -> Option<FunctionContext> {
        match self {
            FileContext::TestFile => Some(FunctionContext::Test),
            FileContext::UtilFile => None,  // Don't force, let features decide
            FileContext::HandlerFile => None,
            FileContext::InternalFile => None,
            FileContext::SourceFile => None,
        }
    }
}

/// Observable features extracted from a function
#[derive(Debug, Clone, Default)]
pub struct FunctionFeatures {
    // Naming features
    pub has_short_prefix: bool,      // 2-4 char prefix + underscore (C-style)
    pub has_test_prefix: bool,       // test_, spec_, Test, it_, etc.
    pub has_handler_suffix: bool,    // _cb, _handler, _hook, Handler, Callback
    pub has_internal_prefix: bool,   // _, __, internal_, lowercase (Go)
    pub is_capitalized: bool,        // PascalCase (exported in Go)
    
    // Language-specific utility indicators
    pub is_go_exported: bool,        // Go: PascalCase = exported
    pub is_go_internal: bool,        // Go: lowercase = internal
    pub is_js_export: bool,          // JS: export function, module.exports
    pub is_js_arrow_handler: bool,   // JS: arrow function callback pattern
    pub is_python_dunder: bool,      // Python: __init__, __call__, etc.
    pub is_python_private: bool,     // Python: _private, __mangled
    
    // Path features
    pub in_test_path: bool,          // /tests/, /test/, _test., .test., .spec.
    pub in_util_path: bool,          // /util/, /utils/, /common/, /helpers/
    pub in_handler_path: bool,       // /handlers/, /callbacks/, /hooks/
    pub in_internal_path: bool,      // /internal/, /private/, /src/
    
    // Call graph features (normalized 0-1)
    pub fan_in_ratio: f64,           // fan_in / max_fan_in
    pub fan_out_ratio: f64,          // fan_out / max_fan_out
    pub caller_file_spread: f64,     // unique_caller_files / total_callers
    
    // Code features
    pub complexity_ratio: f64,       // complexity / avg_complexity
    pub loc_ratio: f64,              // loc / avg_loc
    pub param_count_ratio: f64,      // params / avg_params
    
    // Address-taken (callback indicator)
    pub address_taken: bool,
    
    // High fan-in indicator (direct utility signal)
    pub is_high_fan_in: bool,        // fan_in > 10
    
    // Hierarchical: file-level context
    pub file_context: FileContext,
}

impl FunctionFeatures {
    /// Extract features from function metadata
    pub fn extract(
        name: &str,
        file_path: &str,
        fan_in: usize,
        fan_out: usize,
        max_fan_in: usize,
        max_fan_out: usize,
        caller_files: usize,
        complexity: Option<i64>,
        avg_complexity: f64,
        loc: u32,
        avg_loc: f64,
        param_count: usize,
        avg_params: f64,
        address_taken: bool,
    ) -> Self {
        let name_lower = name.to_lowercase();
        let path_lower = file_path.to_lowercase();
        
        // Detect language from file extension
        let is_go = path_lower.ends_with(".go");
        let is_js = path_lower.ends_with(".js") || path_lower.ends_with(".jsx") 
            || path_lower.ends_with(".ts") || path_lower.ends_with(".tsx");
        let is_python = path_lower.ends_with(".py");
        let is_c = path_lower.ends_with(".c") || path_lower.ends_with(".h")
            || path_lower.ends_with(".cpp") || path_lower.ends_with(".hpp");
        
        // Go: PascalCase = exported, lowercase = internal
        let first_char = name.chars().next();
        let is_go_exported = is_go && first_char.map(|c| c.is_uppercase()).unwrap_or(false);
        let is_go_internal = is_go && first_char.map(|c| c.is_lowercase()).unwrap_or(false);
        
        // JS: Common patterns
        let is_js_handler = is_js && (
            name_lower.starts_with("on") ||  // onClick, onSubmit
            name_lower.starts_with("handle") ||  // handleClick
            name_lower.ends_with("handler") ||
            name_lower.ends_with("callback") ||
            name_lower.ends_with("listener")
        );
        
        // Python: Dunder and private methods
        let is_python_dunder = is_python && name.starts_with("__") && name.ends_with("__");
        let is_python_private = is_python && name.starts_with('_') && !name.starts_with("__");
        
        // Test patterns (language-aware)
        let has_test_prefix = name_lower.starts_with("test_") 
            || name_lower.starts_with("test")  // Go: TestFoo
            || name_lower.starts_with("spec_")
            || name_lower.starts_with("it_")
            || (is_go && name.starts_with("Test"))  // Go convention
            || (is_js && (name_lower.starts_with("it(") || name_lower.starts_with("describe(")));
        
        // Handler patterns (language-aware)
        let has_handler_suffix = name_lower.ends_with("_cb")
            || name_lower.ends_with("_callback")
            || name_lower.ends_with("_handler")
            || name_lower.ends_with("_hook")
            || name_lower.ends_with("_fn")
            || (is_go && name.ends_with("Handler"))  // Go: FooHandler
            || (is_go && name.ends_with("Func"))     // Go: FooFunc
            || is_js_handler;
            
        // Utility path detection (more comprehensive)
        let in_util_path = path_lower.contains("/util")
            || path_lower.contains("/utils")
            || path_lower.contains("/common")
            || path_lower.contains("/helper")
            || path_lower.contains("/helpers")
            || path_lower.contains("/lib/")
            || path_lower.contains("/shared")
            || path_lower.contains("/core/")
            || (is_js && path_lower.contains("/src/"))  // JS: src often has utils
            || path_lower.contains("utils.")
            || path_lower.contains("helpers.");
        
        // Test path detection (more comprehensive)
        let in_test_path = path_lower.contains("/test")
            || path_lower.contains("/tests")
            || path_lower.contains("_test.")
            || path_lower.contains(".test.")
            || path_lower.contains(".spec.")
            || path_lower.contains("/spec")
            || path_lower.contains("/__tests__")  // Jest convention
            || path_lower.contains("/__mocks__"); // Jest mocks
        
        Self {
            // Naming features
            has_short_prefix: is_c && Self::has_short_prefix(name),  // Only for C
            has_test_prefix,
            has_handler_suffix,
            has_internal_prefix: name.starts_with('_') && !name.starts_with("__"),
            is_capitalized: first_char.map(|c| c.is_uppercase()).unwrap_or(false),
            
            // Language-specific
            is_go_exported,
            is_go_internal,
            is_js_export: is_js && in_util_path,  // Approximate
            is_js_arrow_handler: is_js_handler,
            is_python_dunder,
            is_python_private,
            
            // Path features
            in_test_path,
            in_util_path,
            in_handler_path: path_lower.contains("/handler")
                || path_lower.contains("/callback")
                || path_lower.contains("/hook")
                || path_lower.contains("/hooks")
                || path_lower.contains("/events"),
            in_internal_path: path_lower.contains("/internal")
                || path_lower.contains("/private")
                || path_lower.contains("/_")
                || (is_go && path_lower.contains("/pkg/")),  // Go internal convention
            
            // Call graph features
            fan_in_ratio: if max_fan_in > 0 { fan_in as f64 / max_fan_in as f64 } else { 0.0 },
            fan_out_ratio: if max_fan_out > 0 { fan_out as f64 / max_fan_out as f64 } else { 0.0 },
            caller_file_spread: if fan_in > 0 { caller_files as f64 / fan_in as f64 } else { 0.0 },
            
            // Code features
            complexity_ratio: complexity.map(|c| c as f64 / avg_complexity.max(1.0)).unwrap_or(1.0),
            loc_ratio: loc as f64 / avg_loc.max(1.0),
            param_count_ratio: param_count as f64 / avg_params.max(1.0),
            
            address_taken,
            is_high_fan_in: fan_in > 10,
            
            // Hierarchical context
            file_context: FileContext::from_path(file_path),
        }
    }
    
    /// Check for short prefix pattern (2-4 chars + underscore)
    fn has_short_prefix(name: &str) -> bool {
        if let Some(underscore_pos) = name.find('_') {
            if (2..=4).contains(&underscore_pos) {
                let prefix = &name[..underscore_pos];
                if prefix.chars().all(|c| c.is_alphanumeric()) {
                    let prefix_lower = prefix.to_lowercase();
                    const COMMON_WORDS: &[&str] = &[
                        "get", "set", "is", "do", "can", "has", "new", "old", "add", "del",
                        "pop", "put", "run", "try", "end", "use", "for", "the", "and", "not",
                        "dead", "live", "test", "mock", "fake", "stub", "temp", "tmp", "foo",
                        "bar", "baz", "qux", "call", "read", "load", "save", "send", "recv",
                    ];
                    return !COMMON_WORDS.contains(&prefix_lower.as_str());
                }
            }
        }
        false
    }
    
    /// Convert to feature vector for HMM (20 features)
    pub fn to_vector(&self) -> [f64; 20] {
        [
            // Naming (5)
            self.has_short_prefix as u8 as f64,
            self.has_test_prefix as u8 as f64,
            self.has_handler_suffix as u8 as f64,
            self.has_internal_prefix as u8 as f64,
            self.is_capitalized as u8 as f64,
            // Language-specific (6)
            self.is_go_exported as u8 as f64,
            self.is_go_internal as u8 as f64,
            self.is_js_export as u8 as f64,
            self.is_js_arrow_handler as u8 as f64,
            self.is_python_dunder as u8 as f64,
            self.is_python_private as u8 as f64,
            // Paths (4)
            self.in_test_path as u8 as f64,
            self.in_util_path as u8 as f64,
            self.in_handler_path as u8 as f64,
            self.in_internal_path as u8 as f64,
            // Call graph (3)
            self.fan_in_ratio,
            self.fan_out_ratio,
            self.caller_file_spread,
            // Metadata (2)
            self.address_taken as u8 as f64,
            self.is_high_fan_in as u8 as f64,
        ]
    }
    
    /// Quick check if this looks like a utility function (any language)
    #[allow(clippy::nonminimal_bool)]
    pub fn looks_like_utility(&self) -> bool {
        // C-style: short prefix + high fan-in
        (self.has_short_prefix && self.is_high_fan_in)
        // Go: exported function with high fan-in (cross-package utility)
        || (self.is_go_exported && self.is_high_fan_in)
        // Go: exported in util/common/helpers path
        || (self.is_go_exported && self.in_util_path)
        // Any language: in util path with high fan-in
        || (self.in_util_path && self.is_high_fan_in)
        // High fan-in with spread callers (universal pattern)
        || (self.fan_in_ratio > 0.3 && self.caller_file_spread > 0.5)
        // Very high fan-in alone (top 20% callers)
        || self.fan_in_ratio > 0.2
    }
    
    /// Quick check if this looks like a handler/callback (any language)
    pub fn looks_like_handler(&self) -> bool {
        self.has_handler_suffix
        || self.is_js_arrow_handler
        || self.address_taken
        || self.in_handler_path
    }
    
    /// Quick check if this looks like a test function (any language)
    pub fn looks_like_test(&self) -> bool {
        self.has_test_prefix 
            || self.in_test_path
            || matches!(self.file_context, FileContext::TestFile)
    }
    
    /// Quick check if this looks like internal/private (any language)
    pub fn looks_like_internal(&self) -> bool {
        self.has_internal_prefix
        || self.is_go_internal
        || self.is_python_private
        || self.in_internal_path
    }
}

/// Number of features in the model
const NUM_FEATURES: usize = 20;

/// Hidden Markov Model for function context classification
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ContextHMM {
    /// Initial state probabilities (5 states)
    pub initial: [f64; 5],
    
    /// Transition probabilities (5x5 matrix)
    /// transition[i][j] = P(state_j | state_i)
    pub transition: [[f64; 5]; 5],
    
    /// Emission parameters for each state
    /// Each state has mean and variance for each of NUM_FEATURES features
    pub emission_mean: [[f64; NUM_FEATURES]; 5],
    pub emission_var: [[f64; NUM_FEATURES]; 5],
}

impl Default for ContextHMM {
    fn default() -> Self {
        Self::new()
    }
}

impl ContextHMM {
    /// Create a new HMM with default (heuristic-based) parameters
    pub fn new() -> Self {
        // Initial probabilities (prior)
        let initial = [0.15, 0.10, 0.50, 0.20, 0.05];  // Utility, Handler, Core, Internal, Test
        
        // Transition matrix (functions in same file tend to have similar roles)
        let transition = [
            // To:    Util   Hand   Core   Int    Test
            /* Util */ [0.60, 0.10, 0.15, 0.10, 0.05],
            /* Hand */ [0.10, 0.50, 0.20, 0.15, 0.05],
            /* Core */ [0.10, 0.10, 0.55, 0.20, 0.05],
            /* Int  */ [0.15, 0.10, 0.25, 0.45, 0.05],
            /* Test */ [0.05, 0.05, 0.10, 0.05, 0.75],
        ];
        
        // Emission means for each feature per state (20 features)
        // [short_prefix, test_prefix, handler_suffix, internal_prefix, capitalized,
        //  go_exported, go_internal, js_export, js_handler, py_dunder, py_private,
        //  test_path, util_path, handler_path, internal_path,
        //  fan_in_ratio, fan_out_ratio, caller_spread, address_taken, high_fan_in]
        let emission_mean = [
            // Utility: high fan-in, spread callers, util path, exported
            [0.5, 0.0, 0.1, 0.1, 0.5, 0.6, 0.2, 0.4, 0.1, 0.1, 0.1, 0.0, 0.7, 0.0, 0.1, 0.7, 0.3, 0.7, 0.2, 0.8],
            // Handler: handler suffix/path, address taken, js handlers
            [0.2, 0.0, 0.8, 0.1, 0.3, 0.3, 0.3, 0.2, 0.8, 0.1, 0.1, 0.0, 0.1, 0.8, 0.1, 0.3, 0.4, 0.4, 0.8, 0.3],
            // Core: normal everything, moderate fan-in
            [0.1, 0.0, 0.1, 0.1, 0.4, 0.4, 0.4, 0.3, 0.1, 0.1, 0.1, 0.0, 0.1, 0.1, 0.1, 0.3, 0.4, 0.4, 0.1, 0.3],
            // Internal: internal prefix, low fan-in, internal path
            [0.1, 0.0, 0.1, 0.7, 0.2, 0.1, 0.7, 0.1, 0.1, 0.1, 0.6, 0.0, 0.1, 0.0, 0.7, 0.1, 0.3, 0.3, 0.1, 0.1],
            // Test: test prefix/path
            [0.0, 0.9, 0.0, 0.0, 0.3, 0.3, 0.3, 0.1, 0.1, 0.1, 0.1, 0.9, 0.0, 0.0, 0.0, 0.1, 0.5, 0.2, 0.0, 0.1],
        ];
        
        // Emission variances (per-state tuning for better discrimination)
        // Lower variance = feature is more discriminative for that state
        let emission_var = [
            // Utility: tight variance on fan_in, util_path, high_fan_in
            [0.3, 0.3, 0.3, 0.3, 0.2, 0.2, 0.3, 0.2, 0.3, 0.3, 0.3, 0.3, 0.1, 0.3, 0.3, 0.1, 0.2, 0.1, 0.2, 0.1],
            // Handler: tight on handler_suffix, js_handler, address_taken
            [0.3, 0.3, 0.1, 0.3, 0.3, 0.3, 0.3, 0.3, 0.1, 0.3, 0.3, 0.3, 0.3, 0.1, 0.3, 0.2, 0.2, 0.2, 0.1, 0.2],
            // Core: loose variance (catch-all category)
            [0.3, 0.3, 0.3, 0.3, 0.3, 0.3, 0.3, 0.3, 0.3, 0.3, 0.3, 0.3, 0.3, 0.3, 0.3, 0.3, 0.3, 0.3, 0.3, 0.3],
            // Internal: tight on internal_prefix, go_internal, py_private, internal_path
            [0.3, 0.3, 0.3, 0.1, 0.3, 0.3, 0.1, 0.3, 0.3, 0.3, 0.1, 0.3, 0.3, 0.3, 0.1, 0.2, 0.2, 0.2, 0.2, 0.2],
            // Test: very tight on test_prefix, test_path
            [0.3, 0.05, 0.3, 0.3, 0.3, 0.3, 0.3, 0.3, 0.3, 0.3, 0.3, 0.05, 0.3, 0.3, 0.3, 0.2, 0.2, 0.2, 0.2, 0.2],
        ];
        
        Self {
            initial,
            transition,
            emission_mean,
            emission_var,
        }
    }
    
    /// Classify a single function
    pub fn classify(&self, features: &FunctionFeatures) -> FunctionContext {
        let vec = features.to_vector();
        let mut best_state = FunctionContext::Core;
        let mut best_prob = f64::NEG_INFINITY;
        
        for state in FunctionContext::ALL {
            let prob = self.log_emission_prob(state, &vec) + self.initial[state.index()].ln();
            if prob > best_prob {
                best_prob = prob;
                best_state = state;
            }
        }
        
        best_state
    }
    
    /// Classify a sequence of functions using Viterbi algorithm
    pub fn classify_sequence(&self, features: &[FunctionFeatures]) -> Vec<FunctionContext> {
        if features.is_empty() {
            return vec![];
        }
        
        let n = features.len();
        let n_states = 5;
        
        // Viterbi tables
        let mut viterbi = vec![[f64::NEG_INFINITY; 5]; n];
        let mut backpointer = vec![[0usize; 5]; n];
        
        // Initialize
        let first_vec = features[0].to_vector();
        for s in 0..n_states {
            viterbi[0][s] = self.initial[s].ln() + self.log_emission_prob(FunctionContext::from_index(s), &first_vec);
        }
        
        // Forward pass
        for t in 1..n {
            let vec = features[t].to_vector();
            for s in 0..n_states {
                let emission = self.log_emission_prob(FunctionContext::from_index(s), &vec);
                
                for prev_s in 0..n_states {
                    let prob = viterbi[t-1][prev_s] + self.transition[prev_s][s].ln() + emission;
                    if prob > viterbi[t][s] {
                        viterbi[t][s] = prob;
                        backpointer[t][s] = prev_s;
                    }
                }
            }
        }
        
        // Find best final state
        let mut best_last = 0;
        for s in 1..n_states {
            if viterbi[n-1][s] > viterbi[n-1][best_last] {
                best_last = s;
            }
        }
        
        // Backtrack
        let mut path = vec![FunctionContext::Core; n];
        path[n-1] = FunctionContext::from_index(best_last);
        for t in (0..n-1).rev() {
            path[t] = FunctionContext::from_index(backpointer[t+1][path[t+1].index()]);
        }
        
        path
    }
    
    /// Log probability of observing features given state (Gaussian emission)
    fn log_emission_prob(&self, state: FunctionContext, features: &[f64; NUM_FEATURES]) -> f64 {
        let s = state.index();
        let mut log_prob = 0.0;
        
        for i in 0..NUM_FEATURES {
            let mean = self.emission_mean[s][i];
            let var = self.emission_var[s][i].max(0.01);  // Avoid div by zero
            let x = features[i];
            
            // Log of Gaussian PDF (ignoring constant)
            log_prob += -0.5 * ((x - mean).powi(2) / var + var.ln());
        }
        
        log_prob
    }
    
    /// Update model parameters from labeled examples (simplified Baum-Welch)
    pub fn update(&mut self, examples: &[(FunctionFeatures, FunctionContext)]) {
        if examples.is_empty() {
            return;
        }
        
        // Count state occurrences and accumulate feature values
        let mut state_counts = [0.0f64; 5];
        let mut feature_sums = [[0.0f64; NUM_FEATURES]; 5];
        let mut feature_sq_sums = [[0.0f64; NUM_FEATURES]; 5];
        
        for (features, context) in examples {
            let s = context.index();
            state_counts[s] += 1.0;
            
            let vec = features.to_vector();
            for i in 0..NUM_FEATURES {
                feature_sums[s][i] += vec[i];
                feature_sq_sums[s][i] += vec[i] * vec[i];
            }
        }
        
        // Update initial probabilities
        let total: f64 = state_counts.iter().sum();
        for s in 0..5 {
            self.initial[s] = (state_counts[s] + 1.0) / (total + 5.0);  // Laplace smoothing
        }
        
        // Update emission parameters
        for s in 0..5 {
            if state_counts[s] > 0.0 {
                for i in 0..NUM_FEATURES {
                    let n = state_counts[s];
                    let mean = feature_sums[s][i] / n;
                    let var = (feature_sq_sums[s][i] / n - mean * mean).max(0.01);
                    
                    // Direct update from bootstrap labels (no smoothing)
                    self.emission_mean[s][i] = mean;
                    self.emission_var[s][i] = var;
                }
            }
        }
    }
    
    /// Bootstrap training from call graph heuristics
    pub fn bootstrap_from_graph(&mut self, function_data: &[(FunctionFeatures, usize, usize, bool)]) {
        // function_data: (features, fan_in, fan_out, address_taken)
        let mut examples = Vec::new();
        
        for (features, _fan_in, _fan_out, _address_taken) in function_data {
            // Use the new language-aware helper methods
            let context = if features.looks_like_test() {
                FunctionContext::Test
            } else if features.looks_like_handler() {
                FunctionContext::Handler
            } else if features.looks_like_utility() {
                FunctionContext::Utility
            } else if features.looks_like_internal() {
                FunctionContext::Internal
            } else {
                FunctionContext::Core
            };
            
            examples.push((features.clone(), context));
        }
        
        self.update(&examples);
        
        // EM disabled - can cause drift on certain codebases
        // self.em_refine(function_data, 1);
    }
    
    /// Semi-supervised EM refinement
    /// 
    /// E-step: Classify all functions with current model
    /// M-step: Update model parameters from confident predictions
    pub fn em_refine(&mut self, function_data: &[(FunctionFeatures, usize, usize, bool)], iterations: usize) {
        for _iter in 0..iterations {
            let mut examples = Vec::new();
            
            for (features, _, _, _) in function_data {
                // E-step: Get current prediction with confidence
                let (context, confidence) = self.classify_with_confidence(features);
                
                // Only use high-confidence predictions for M-step
                if confidence > 0.7 {
                    examples.push((features.clone(), context));
                }
            }
            
            // M-step: Update model if we have enough examples
            if examples.len() > function_data.len() / 4 {
                self.update(&examples);
            }
        }
    }
    
    /// Classify with confidence score
    pub fn classify_with_confidence(&self, features: &FunctionFeatures) -> (FunctionContext, f64) {
        let vec = features.to_vector();
        let mut log_probs = [0.0f64; 5];
        
        // Compute log probability for each state
        for (s, log_prob) in log_probs.iter_mut().enumerate() {
            *log_prob = self.initial[s].ln() + self.log_emission_prob(FunctionContext::from_index(s), &vec);
        }
        
        // Find max and compute softmax for confidence
        let max_log = log_probs.iter().cloned().fold(f64::NEG_INFINITY, f64::max);
        let sum_exp: f64 = log_probs.iter().map(|&lp| (lp - max_log).exp()).sum();
        
        let mut best_state = 0;
        let mut best_prob = f64::NEG_INFINITY;
        for (s, &lp) in log_probs.iter().enumerate() {
            if lp > best_prob {
                best_prob = lp;
                best_state = s;
            }
        }
        
        // Confidence is the softmax probability of the best state
        let confidence = (best_prob - max_log).exp() / sum_exp;
        
        (FunctionContext::from_index(best_state), confidence)
    }
}

/// CRF-style feature weights for discriminative classification
/// 
/// While we use HMM as the base model, we add CRF-style scoring
/// for better discrimination between classes.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CRFWeights {
    /// Feature weights per state (discriminative)
    pub feature_weights: [[f64; NUM_FEATURES]; 5],
    /// Transition weights (pairwise potentials)
    pub transition_weights: [[f64; 5]; 5],
}

impl Default for CRFWeights {
    fn default() -> Self {
        Self::new()
    }
}

impl CRFWeights {
    pub fn new() -> Self {
        // Initialize with discriminative weights learned from patterns
        let mut feature_weights = [[0.0; NUM_FEATURES]; 5];
        
        // Utility: strongly reward high fan-in, util path, go_exported
        feature_weights[0][12] = 3.0;  // in_util_path
        feature_weights[0][15] = 3.0;  // fan_in_ratio (key signal!)
        feature_weights[0][5] = 2.0;   // is_go_exported
        feature_weights[0][19] = 4.0;  // is_high_fan_in (strongest signal)
        feature_weights[0][0] = 2.0;   // has_short_prefix (C-style)
        feature_weights[0][17] = 2.0;  // caller_file_spread
        
        // Handler: reward handler suffix, address_taken, js_handler
        feature_weights[1][2] = 3.0;   // has_handler_suffix
        feature_weights[1][8] = 2.5;   // is_js_arrow_handler
        feature_weights[1][18] = 2.0;  // address_taken
        feature_weights[1][13] = 1.5;  // in_handler_path
        
        // Core: slight reward for capitalized (exported)
        feature_weights[2][4] = 0.5;   // is_capitalized
        
        // Internal: reward internal prefix, go_internal, python_private
        feature_weights[3][3] = 2.0;   // has_internal_prefix
        feature_weights[3][6] = 2.0;   // is_go_internal
        feature_weights[3][10] = 2.0;  // is_python_private
        feature_weights[3][14] = 1.5;  // in_internal_path
        
        // Test: strongly reward test patterns
        feature_weights[4][1] = 4.0;   // has_test_prefix
        feature_weights[4][11] = 4.0;  // in_test_path
        
        // Transition weights (encourage staying in same context)
        let mut transition_weights = [[0.0; 5]; 5];
        for i in 0..5 {
            transition_weights[i][i] = 1.0;  // Self-transition bonus
        }
        // Test functions rarely transition to non-test
        transition_weights[4][4] = 2.0;
        
        Self {
            feature_weights,
            transition_weights,
        }
    }
    
    /// CRF-style score for a classification
    pub fn score(&self, features: &FunctionFeatures, context: FunctionContext) -> f64 {
        let vec = features.to_vector();
        let s = context.index();
        
        let mut score = 0.0;
        for (i, &v) in vec.iter().enumerate() {
            score += self.feature_weights[s][i] * v;
        }
        score
    }
    
    /// Learn weights from labeled examples using perceptron
    pub fn train(&mut self, examples: &[(FunctionFeatures, FunctionContext)], learning_rate: f64) {
        for (features, true_context) in examples {
            // Predict with current weights
            let predicted = self.predict(features);
            
            if predicted != *true_context {
                // Update weights (perceptron update)
                let vec = features.to_vector();
                let true_idx = true_context.index();
                let pred_idx = predicted.index();
                
                for (i, &v) in vec.iter().enumerate() {
                    self.feature_weights[true_idx][i] += learning_rate * v;
                    self.feature_weights[pred_idx][i] -= learning_rate * v;
                }
            }
        }
    }
    
    /// Predict context using CRF weights
    pub fn predict(&self, features: &FunctionFeatures) -> FunctionContext {
        let mut best_score = f64::NEG_INFINITY;
        let mut best_context = FunctionContext::Core;
        
        for s in 0..5 {
            let context = FunctionContext::from_index(s);
            let score = self.score(features, context);
            if score > best_score {
                best_score = score;
                best_context = context;
            }
        }
        
        best_context
    }
}

/// Context classifier that combines HMM + CRF for better accuracy
pub struct ContextClassifier {
    hmm: ContextHMM,
    crf: CRFWeights,
    cache: HashMap<String, FunctionContext>,
    /// Weight for HMM vs CRF (0.0 = pure CRF, 1.0 = pure HMM)
    hmm_weight: f64,
}

impl ContextClassifier {
    pub fn new() -> Self {
        // Allow tuning via environment variable
        let hmm_weight = std::env::var("REPOTOIRE_HMM_WEIGHT")
            .ok()
            .and_then(|s| s.parse().ok())
            .unwrap_or(0.9);
        
        Self {
            hmm: ContextHMM::new(),
            crf: CRFWeights::new(),
            cache: HashMap::new(),
            hmm_weight,
        }
    }
    
    /// Load or create HMM for a codebase
    pub fn for_codebase(cache_path: Option<&std::path::Path>) -> Self {
        let hmm = if let Some(path) = cache_path {
            if path.exists() {
                std::fs::read_to_string(path)
                    .ok()
                    .and_then(|s| serde_json::from_str(&s).ok())
                    .unwrap_or_default()
            } else {
                ContextHMM::new()
            }
        } else {
            ContextHMM::new()
        };
        
        Self {
            hmm,
            crf: CRFWeights::new(),
            cache: HashMap::new(),
            hmm_weight: 0.9,
        }
    }
    
    /// Classify a function using hierarchical HMM + CRF
    pub fn classify(&mut self, name: &str, features: &FunctionFeatures) -> FunctionContext {
        if let Some(&cached) = self.cache.get(name) {
            return cached;
        }
        
        // Hierarchical: Check file-level bias first
        if let Some(file_bias) = features.file_context.function_bias() {
            // Strong file-level signal (e.g., test file → all functions are Test)
            self.cache.insert(name.to_string(), file_bias);
            return file_bias;
        }
        
        // Use ensemble if weight < 1.0, otherwise pure HMM
        let context = if self.hmm_weight < 1.0 {
            self.ensemble_classify(features)
        } else {
            self.hmm.classify(features)
        };
        self.cache.insert(name.to_string(), context);
        context
    }
    
    /// Ensemble classification combining HMM (generative) and CRF (discriminative)
    fn ensemble_classify(&self, features: &FunctionFeatures) -> FunctionContext {
        let vec = features.to_vector();
        let mut scores = [0.0f64; 5];
        
        // HMM contribution (log probabilities normalized to 0-1 range)
        let mut hmm_log_probs = [0.0f64; 5];
        for s in 0..5 {
            let ctx = FunctionContext::from_index(s);
            hmm_log_probs[s] = self.hmm.initial[s].ln() + self.hmm.log_emission_prob(ctx, &vec);
        }
        let hmm_max = hmm_log_probs.iter().cloned().fold(f64::NEG_INFINITY, f64::max);
        let hmm_sum: f64 = hmm_log_probs.iter().map(|&lp| (lp - hmm_max).exp()).sum();
        
        // CRF contribution (scores normalized to 0-1 range)
        let mut crf_scores = [0.0f64; 5];
        for s in 0..5 {
            crf_scores[s] = self.crf.score(features, FunctionContext::from_index(s));
        }
        let crf_max = crf_scores.iter().cloned().fold(f64::NEG_INFINITY, f64::max);
        let crf_sum: f64 = crf_scores.iter().map(|&sc| (sc - crf_max).exp()).sum();
        
        // Combine with weighted average
        for s in 0..5 {
            let hmm_prob = (hmm_log_probs[s] - hmm_max).exp() / hmm_sum;
            let crf_prob = (crf_scores[s] - crf_max).exp() / crf_sum;
            scores[s] = self.hmm_weight * hmm_prob + (1.0 - self.hmm_weight) * crf_prob;
        }
        
        // Return highest scoring context
        let mut best_idx = 0;
        for s in 1..5 {
            if scores[s] > scores[best_idx] {
                best_idx = s;
            }
        }
        
        FunctionContext::from_index(best_idx)
    }
    
    /// Train on codebase data
    pub fn train(&mut self, function_data: &[(FunctionFeatures, usize, usize, bool)]) {
        // Train HMM with bootstrap (no EM to avoid drift)
        self.hmm.bootstrap_from_graph(function_data);
        
        // Train CRF if ensemble is enabled
        if self.hmm_weight < 1.0 {
            let examples: Vec<_> = function_data
                .iter()
                .map(|(features, _, _, _)| {
                    let ctx = self.hmm.classify(features);
                    (features.clone(), ctx)
                })
                .collect();
            
            // Perceptron training (1 epoch, low learning rate)
            self.crf.train(&examples, 0.05);
        }
        
        self.cache.clear();
    }
    
    /// Save model (both HMM and CRF)
    pub fn save(&self, path: &std::path::Path) -> std::io::Result<()> {
        // Save combined model
        let combined = serde_json::json!({
            "hmm": self.hmm,
            "crf": self.crf,
            "hmm_weight": self.hmm_weight,
        });
        let json = serde_json::to_string_pretty(&combined)?;
        std::fs::write(path, json)
    }
    
    /// Load model (both HMM and CRF)
    pub fn load(path: &std::path::Path) -> Option<Self> {
        let content = std::fs::read_to_string(path).ok()?;
        let value: serde_json::Value = serde_json::from_str(&content).ok()?;
        
        let hmm: ContextHMM = serde_json::from_value(value.get("hmm")?.clone()).ok()?;
        let crf: CRFWeights = serde_json::from_value(value.get("crf")?.clone()).ok()?;
        let hmm_weight = value.get("hmm_weight")?.as_f64()?;
        
        Some(Self {
            hmm,
            crf,
            cache: HashMap::new(),
            hmm_weight,
        })
    }
}

impl Default for ContextClassifier {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    
    #[test]
    fn test_feature_extraction() {
        let features = FunctionFeatures::extract(
            "u3r_word", "pkg/noun/retrieve.c",
            50, 10, 100, 50, 20,
            Some(15), 10.0, 30, 25.0, 2, 2.5, false,
        );
        
        assert!(features.has_short_prefix);
        assert!(!features.has_test_prefix);
        assert!(!features.has_handler_suffix);
        assert!(features.fan_in_ratio > 0.4);
    }
    
    #[test]
    fn test_classify_utility() {
        let hmm = ContextHMM::new();
        let features = FunctionFeatures {
            has_short_prefix: true,
            fan_in_ratio: 0.8,
            caller_file_spread: 0.7,
            in_util_path: true,
            ..Default::default()
        };
        
        let context = hmm.classify(&features);
        assert_eq!(context, FunctionContext::Utility);
    }
    
    #[test]
    fn test_classify_handler() {
        let hmm = ContextHMM::new();
        let features = FunctionFeatures {
            has_handler_suffix: true,
            address_taken: true,
            in_handler_path: true,
            ..Default::default()
        };
        
        let context = hmm.classify(&features);
        assert_eq!(context, FunctionContext::Handler);
    }
    
    #[test]
    fn test_classify_test() {
        let hmm = ContextHMM::new();
        let features = FunctionFeatures {
            has_test_prefix: true,
            in_test_path: true,
            ..Default::default()
        };
        
        let context = hmm.classify(&features);
        assert_eq!(context, FunctionContext::Test);
    }
    
    #[test]
    fn test_viterbi() {
        let hmm = ContextHMM::new();
        
        // Sequence of functions that should be classified as Test
        let features = vec![
            FunctionFeatures { has_test_prefix: true, in_test_path: true, ..Default::default() },
            FunctionFeatures { has_test_prefix: true, in_test_path: true, ..Default::default() },
            FunctionFeatures { has_test_prefix: true, in_test_path: true, ..Default::default() },
        ];
        
        let path = hmm.classify_sequence(&features);
        assert!(path.iter().all(|&c| c == FunctionContext::Test));
    }
}
