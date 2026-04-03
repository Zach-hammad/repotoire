use serde::{Deserialize, Serialize};

/// A node index into the graph's node array.
///
/// Transparent wrapper around `u32` with Copy semantics.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, PartialOrd, Ord, Serialize, Deserialize)]
#[repr(transparent)]
pub struct NodeIndex(u32);

impl NodeIndex {
    pub const INVALID: NodeIndex = NodeIndex(u32::MAX);

    #[inline]
    pub fn new(idx: u32) -> Self {
        Self(idx)
    }

    #[inline]
    pub fn index(self) -> usize {
        self.0 as usize
    }

    #[inline]
    pub fn as_u32(self) -> u32 {
        self.0
    }
}

impl From<usize> for NodeIndex {
    fn from(idx: usize) -> Self {
        Self(idx as u32)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_node_index_basics() {
        let idx = NodeIndex::new(42);
        assert_eq!(idx.index(), 42);
        assert_eq!(idx, NodeIndex::new(42));
        assert_ne!(idx, NodeIndex::new(43));
    }

    #[test]
    fn test_node_index_invalid() {
        assert_eq!(NodeIndex::INVALID.index(), u32::MAX as usize);
    }

    #[test]
    fn test_node_index_ord() {
        let a = NodeIndex::new(1);
        let b = NodeIndex::new(2);
        assert!(a < b);
    }

    #[test]
    fn test_node_index_hash() {
        use std::collections::HashSet;
        let mut set = HashSet::new();
        set.insert(NodeIndex::new(1));
        set.insert(NodeIndex::new(1));
        assert_eq!(set.len(), 1);
    }
}
