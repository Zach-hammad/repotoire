use rayon::prelude::*;

/// Epsilon for floating-point comparisons to avoid division by near-zero values
const EPSILON: f32 = 1e-10;

/// Calculate dot product of two vectors
#[must_use]
fn dot_product(a: &[f32], b: &[f32]) -> f32 {
    a.iter().zip(b.iter()).map(|(x, y)| x * y).sum()
}

/// Calculate L2 norm of a vector
#[must_use]
fn norm(v: &[f32]) -> f32 {
    dot_product(v, v).sqrt()
}

/// Calculate cosine similarity between two vectors
/// Returns a value in [-1.0, 1.0], or 0.0 if either vector has near-zero norm
#[must_use]
pub fn cosine_similarity(a: &[f32], b: &[f32]) -> f32 {
    let dot = dot_product(a, b);
    let norm_a = norm(a);
    let norm_b = norm(b);

    // Use epsilon comparison to handle floating-point precision issues
    if norm_a < EPSILON || norm_b < EPSILON {
        return 0.0;
    }

    let result = dot / (norm_a * norm_b);
    // Clamp to valid cosine similarity range (handles floating-point errors)
    result.clamp(-1.0, 1.0)
}

/// Compute cosine similarity for multiple vectors in parallel
#[must_use]
pub fn batch_cosine_similarity(query: &[f32], matrix: &[&[f32]]) -> Vec<f32> {
    matrix
        .par_iter()
        .map(|row| cosine_similarity(query, row))
        .collect()
}

/// Find top-k most similar vectors by cosine similarity
/// Uses total_cmp for NaN-safe sorting (panics are avoided)
#[must_use]
pub fn find_top_k(query: &[f32], matrix: &[&[f32]], k: usize) -> Vec<(usize, f32)> {
    let mut scores: Vec<(usize, f32)> = matrix
        .par_iter()
        .enumerate()
        .map(|(i, row)| (i, cosine_similarity(query, row)))
        .collect();

    // Use total_cmp for NaN-safe sorting (Rust 1.62+)
    // This prevents panics when NaN values are present in embeddings
    scores.sort_by(|a, b| b.1.total_cmp(&a.1));
    scores.truncate(k);
    scores
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_cosine_similarity_identical() {
        let a = vec![1.0, 2.0, 3.0];
        let b = vec![1.0, 2.0, 3.0];
        let sim = cosine_similarity(&a, &b);
        assert!((sim - 1.0).abs() < 1e-6);
    }

    #[test]
    fn test_cosine_similarity_orthogonal() {
        let a = vec![1.0, 0.0];
        let b = vec![0.0, 1.0];
        let sim = cosine_similarity(&a, &b);
        assert!(sim.abs() < 1e-6);
    }

    #[test]
    fn test_cosine_similarity_zero_vector() {
        let a = vec![0.0, 0.0, 0.0];
        let b = vec![1.0, 2.0, 3.0];
        let sim = cosine_similarity(&a, &b);
        assert_eq!(sim, 0.0);
    }

    #[test]
    fn test_cosine_similarity_near_zero_vector() {
        let a = vec![1e-15, 1e-15, 1e-15];
        let b = vec![1.0, 2.0, 3.0];
        let sim = cosine_similarity(&a, &b);
        assert_eq!(sim, 0.0); // Should handle near-zero gracefully
    }

    #[test]
    fn test_find_top_k_with_nan() {
        // This should not panic even with NaN values
        let query = vec![1.0, 2.0, 3.0];
        let row1 = vec![1.0, 2.0, 3.0];
        let row2 = vec![f32::NAN, 2.0, 3.0]; // Contains NaN
        let row3 = vec![0.5, 1.0, 1.5];
        let matrix: Vec<&[f32]> = vec![&row1, &row2, &row3];

        // This should not panic
        let result = find_top_k(&query, &matrix, 2);
        assert_eq!(result.len(), 2);
    }

    #[test]
    fn test_batch_cosine_similarity() {
        let query = vec![1.0, 0.0];
        let row1 = vec![1.0, 0.0];
        let row2 = vec![0.0, 1.0];
        let matrix: Vec<&[f32]> = vec![&row1, &row2];

        let results = batch_cosine_similarity(&query, &matrix);
        assert_eq!(results.len(), 2);
        assert!((results[0] - 1.0).abs() < 1e-6);
        assert!(results[1].abs() < 1e-6);
    }
}
