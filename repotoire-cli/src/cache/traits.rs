//! Unified cache trait for coordinated invalidation
//!
//! All cache layers should implement this trait to ensure
//! consistent invalidation when source files change.

use std::path::Path;

/// Common interface for cache layers
///
/// Ensures all caches can be invalidated consistently when
/// source files change, preventing stale data divergence.
pub trait CacheLayer: Send + Sync {
    /// Name of this cache layer (for logging)
    fn name(&self) -> &str;
    
    /// Check if this cache has any data
    fn is_populated(&self) -> bool;
    
    /// Invalidate cache entries for the given files
    fn invalidate_files(&mut self, changed_files: &[&Path]);
    
    /// Invalidate all cached data
    fn invalidate_all(&mut self);
}

/// Coordinates invalidation across multiple cache layers
pub struct CacheCoordinator {
    layers: Vec<Box<dyn CacheLayer>>,
}

impl CacheCoordinator {
    pub fn new() -> Self {
        Self { layers: Vec::new() }
    }
    
    pub fn register(&mut self, layer: Box<dyn CacheLayer>) {
        tracing::debug!("Registered cache layer: {}", layer.name());
        self.layers.push(layer);
    }
    
    /// Invalidate specific files across all cache layers
    pub fn invalidate_files(&mut self, changed_files: &[&Path]) {
        for layer in &mut self.layers {
            layer.invalidate_files(changed_files);
            tracing::debug!(
                "Invalidated {} files in cache layer: {}",
                changed_files.len(),
                layer.name()
            );
        }
    }
    
    /// Invalidate all data across all cache layers
    pub fn invalidate_all(&mut self) {
        for layer in &mut self.layers {
            layer.invalidate_all();
            tracing::debug!("Invalidated all data in cache layer: {}", layer.name());
        }
    }
    
    /// Check if all layers are populated (warm cache)
    pub fn all_populated(&self) -> bool {
        self.layers.iter().all(|l| l.is_populated())
    }
}

impl Default for CacheCoordinator {
    fn default() -> Self {
        Self::new()
    }
}
