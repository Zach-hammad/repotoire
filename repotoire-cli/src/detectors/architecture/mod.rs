//! Architecture detectors — coupling, dependencies, graph topology.

mod architectural_bottleneck;
mod circular_dependency;
mod community_misplacement;
mod degree_centrality;
mod hidden_coupling;
mod module_cohesion;
mod mutual_recursion;
mod pagerank_drift;
mod shotgun_surgery;
mod single_point_of_failure;
mod structural_bridge_risk;
mod temporal_bottleneck;

pub use architectural_bottleneck::ArchitecturalBottleneckDetector;
pub use circular_dependency::CircularDependencyDetector;
pub use community_misplacement::CommunityMisplacementDetector;
pub use degree_centrality::DegreeCentralityDetector;
pub use hidden_coupling::HiddenCouplingDetector;
pub use module_cohesion::ModuleCohesionDetector;
pub use mutual_recursion::MutualRecursionDetector;
pub use pagerank_drift::PageRankDriftDetector;
pub use shotgun_surgery::ShotgunSurgeryDetector;
pub use single_point_of_failure::SinglePointOfFailureDetector;
pub use structural_bridge_risk::StructuralBridgeRiskDetector;
pub use temporal_bottleneck::TemporalBottleneckDetector;
