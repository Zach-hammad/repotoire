// Graph algorithm error types (REPO-227)
//
// Provides proper Result types instead of silent failures for graph algorithms.
// All errors convert to Python ValueError via PyO3 for consistent API.

use thiserror::Error;

/// Errors that can occur during graph algorithm execution.
#[derive(Error, Debug, Clone)]
pub enum GraphError {
    /// Graph has no nodes
    #[error("Empty graph provided")]
    EmptyGraph,

    /// Node index references a node that doesn't exist
    #[error("Node index {0} out of bounds (max nodes: {1})")]
    NodeOutOfBounds(u32, u32),

    /// Invalid algorithm parameter
    #[error("Invalid parameter: {0}")]
    InvalidParameter(String),

    /// Iterative algorithm failed to converge
    #[error("Algorithm failed to converge after {0} iterations")]
    ConvergenceFailure(u32),
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_error_messages() {
        let err = GraphError::EmptyGraph;
        assert_eq!(err.to_string(), "Empty graph provided");

        let err = GraphError::NodeOutOfBounds(10, 5);
        assert_eq!(err.to_string(), "Node index 10 out of bounds (max nodes: 5)");

        let err = GraphError::InvalidParameter("damping must be in [0, 1]".to_string());
        assert_eq!(
            err.to_string(),
            "Invalid parameter: damping must be in [0, 1]"
        );

        let err = GraphError::ConvergenceFailure(100);
        assert_eq!(
            err.to_string(),
            "Algorithm failed to converge after 100 iterations"
        );
    }
}
