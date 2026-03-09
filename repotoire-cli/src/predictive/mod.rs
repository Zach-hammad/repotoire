//! Hierarchical Predictive Coding Engine
//!
//! Applies Friston's hierarchical predictive coding theory to code analysis.
//! Five hierarchy levels independently model "what's normal" and compute
//! prediction errors (z-scores). Concordance across levels drives severity.

pub mod architectural;
pub mod compound;
pub mod dependency_chain;
pub mod embeddings;
pub mod relational;
pub mod structural;
pub mod token_level;

use crate::models::Severity;

/// Prediction error at a single hierarchy level for a single entity.
#[derive(Debug, Clone)]
pub struct LevelScore {
    pub level: Level,
    pub z_score: f64,
    pub threshold: f64,
    pub is_surprising: bool,
}

/// The 5 hierarchy levels.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum Level {
    Token,           // L1
    Structural,      // L2
    DependencyChain, // L1.5
    Relational,      // L3
    Architectural,   // L4
}

impl Level {
    pub fn label(&self) -> &'static str {
        match self {
            Level::Token => "L1 Token",
            Level::Structural => "L2 Structural",
            Level::DependencyChain => "L1.5 Dependency",
            Level::Relational => "L3 Relational",
            Level::Architectural => "L4 Architectural",
        }
    }
}

/// Per-entity compound prediction score across all hierarchy levels.
#[derive(Debug, Clone)]
pub struct CompoundScore {
    pub level_scores: Vec<LevelScore>,
    pub concordance: usize,
    pub compound_surprise: f64,
    pub severity: Severity,
}
