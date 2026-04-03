//! Lightweight mutable weighted adjacency list for Phase B graph algorithms.
//!
//! Used during GraphPrimitives computation for the co-change weighted overlay.
//! Not used at query time — only lives during freeze().

/// Mutable weighted directed graph backed by adjacency lists.
pub struct WeightedOverlay {
    adj: Vec<Vec<(u32, f32)>>,
}

impl WeightedOverlay {
    pub fn new(node_count: usize) -> Self {
        Self {
            adj: vec![Vec::new(); node_count],
        }
    }

    pub fn add_edge(&mut self, from: u32, to: u32, weight: f32) {
        self.adj[from as usize].push((to, weight));
    }

    pub fn neighbors(&self, v: u32) -> impl Iterator<Item = (u32, f32)> + '_ {
        self.adj[v as usize].iter().copied()
    }

    pub fn degree(&self, v: u32) -> usize {
        self.adj[v as usize].len()
    }

    pub fn node_count(&self) -> usize {
        self.adj.len()
    }

    pub fn node_indices(&self) -> impl Iterator<Item = u32> {
        0..self.adj.len() as u32
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_overlay_add_edge() {
        let mut g = WeightedOverlay::new(3);
        g.add_edge(0, 1, 0.5);
        g.add_edge(0, 2, 1.0);
        let neighbors: Vec<_> = g.neighbors(0).collect();
        assert_eq!(neighbors.len(), 2);
    }

    #[test]
    fn test_overlay_empty_node() {
        let g = WeightedOverlay::new(3);
        assert_eq!(g.neighbors(1).count(), 0);
    }

    #[test]
    fn test_overlay_node_count() {
        let g = WeightedOverlay::new(5);
        assert_eq!(g.node_count(), 5);
    }

    #[test]
    fn test_overlay_degree() {
        let mut g = WeightedOverlay::new(3);
        g.add_edge(0, 1, 1.0);
        g.add_edge(0, 2, 1.0);
        assert_eq!(g.degree(0), 2);
        assert_eq!(g.degree(1), 0);
    }
}
