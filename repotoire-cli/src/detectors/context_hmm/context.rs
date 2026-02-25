//! Function and file context classification types.

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
        matches!(
            self,
            FunctionContext::Utility | FunctionContext::Handler | FunctionContext::Test
        )
    }

    /// Should skip dead code analysis for this context?
    pub fn skip_dead_code(&self) -> bool {
        matches!(self, FunctionContext::Handler | FunctionContext::Test)
    }

    /// Coupling threshold multiplier
    #[allow(dead_code)] // Public API for context-aware thresholds
    pub fn coupling_multiplier(&self) -> f64 {
        match self {
            FunctionContext::Utility => 3.0,  // Very lenient
            FunctionContext::Handler => 2.5,  // Lenient
            FunctionContext::Core => 1.0,     // Normal
            FunctionContext::Internal => 1.5, // Slightly lenient
            FunctionContext::Test => 5.0,     // Very lenient (tests touch everything)
        }
    }
}

/// File-level context for hierarchical classification
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Default)]
pub enum FileContext {
    TestFile,     // test_*, *_test.*, *.test.*, *.spec.*
    UtilFile,     // utils/*, helpers/*, common/*
    HandlerFile,  // handlers/*, callbacks/*, hooks/*
    InternalFile, // internal/*, _*, private/*
    #[default]
    SourceFile, // Regular source file
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
            FileContext::UtilFile => None, // Don't force, let features decide
            FileContext::HandlerFile => None,
            FileContext::InternalFile => None,
            FileContext::SourceFile => None,
        }
    }
}
