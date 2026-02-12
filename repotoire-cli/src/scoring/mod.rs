//! Graph-Aware Health Scoring System
//!
//! This module calculates codebase health scores using graph analysis,
//! not just finding counts. The score reflects both problems found AND
//! positive architectural qualities.
//!
//! # Scoring Formula
//!
//! ```text
//! Overall Score = Structure × W₁ + Quality × W₂ + Architecture × W₃
//!
//! Where each pillar score:
//!   Pillar = 100 × (1 - penalty_ratio) × (1 + bonus_ratio)
//!          = 100 × (1 - findings_impact) × (1 + graph_bonus)
//! ```
//!
//! # Graph Bonuses (Positive Signals)
//!
//! - **Modularity** (0-10%): Low coupling between modules
//! - **Cohesion** (0-5%): Functions in same module call each other
//! - **Clean Dependencies** (0-10%): No circular import/call cycles
//! - **Complexity Distribution** (0-5%): Most functions are simple
//! - **Test Coverage Signal** (0-5%): Test files exist
//!
//! # Finding Penalties
//!
//! - Critical: 10 points (scaled by codebase size)
//! - High: 5 points
//! - Medium: 1.5 points
//! - Low: 0.3 points
//! - Security findings: 3x multiplier (configurable)
//!
//! # Example
//!
//! A codebase with:
//! - 5 High findings → -25 base penalty
//! - Good modularity → +8% bonus
//! - No cycles → +10% bonus
//!
//! Quality = 100 × (1 - 25/100) × (1 + 0.18) = 88.5

mod graph_scorer;

pub use graph_scorer::{GraphScorer, PillarBreakdown, ScoreBreakdown};
