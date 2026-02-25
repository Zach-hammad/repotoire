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
//!   Pillar = clamp(100 - penalty, 25, 100) + capped_bonus
//!   Penalty = severity_weight × 5.0 / kLOC  (per finding)
//!   Bonus capped at 50% of penalty (bonuses can't fully mask issues)
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
//! # Finding Penalties (severity_weight × 5.0 / kLOC per finding)
//!
//! - Critical: 8.0
//! - High: 4.0
//! - Medium: 1.0
//! - Low: 0.2
//! - Security findings: 3x multiplier (configurable)
//!
//! # Example
//!
//! A 10kLOC codebase with:
//! - 5 High findings → penalty = 5 × 4.0 × 5.0 / 10 = 10 points
//! - Good modularity → +8 pts bonus
//! - No cycles → +10 pts bonus
//! - Bonus capped at 50% of penalty → +5 pts
//!
//! Quality = (100 - 10) + 5 = 95

mod graph_scorer;

pub use graph_scorer::{escalate_compound_smells, GraphScorer, PillarBreakdown, ScoreBreakdown};
