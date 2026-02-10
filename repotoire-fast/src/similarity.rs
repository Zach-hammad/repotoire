use rayon::prelude::*;

/// Epsilon for floating-point comparisons to avoid division by near-zero values
const EPSILON: f32 = 1e-10;

/// SIMD lane width for manual loop unrolling (helps LLVM auto-vectorize)
const SIMD_LANES: usize = 8;

/// Calculate dot product of two vectors with SIMD-friendly loop unrolling.
/// Processes 8 elements at a time to enable auto-vectorization.
#[must_use]
#[inline(always)]
fn dot_product_simd(a: &[f32], b: &[f32]) -> f32 {
    let len = a.len().min(b.len());
    let chunks = len / SIMD_LANES;
    let remainder = len % SIMD_LANES;

    // Process 8 elements at a time (enables AVX/AVX2 vectorization)
    let mut sum0: f32 = 0.0;
    let mut sum1: f32 = 0.0;
    let mut sum2: f32 = 0.0;
    let mut sum3: f32 = 0.0;
    let mut sum4: f32 = 0.0;
    let mut sum5: f32 = 0.0;
    let mut sum6: f32 = 0.0;
    let mut sum7: f32 = 0.0;

    for i in 0..chunks {
        let base = i * SIMD_LANES;
        // SAFETY: bounds checked above
        unsafe {
            sum0 += a.get_unchecked(base) * b.get_unchecked(base);
            sum1 += a.get_unchecked(base + 1) * b.get_unchecked(base + 1);
            sum2 += a.get_unchecked(base + 2) * b.get_unchecked(base + 2);
            sum3 += a.get_unchecked(base + 3) * b.get_unchecked(base + 3);
            sum4 += a.get_unchecked(base + 4) * b.get_unchecked(base + 4);
            sum5 += a.get_unchecked(base + 5) * b.get_unchecked(base + 5);
            sum6 += a.get_unchecked(base + 6) * b.get_unchecked(base + 6);
            sum7 += a.get_unchecked(base + 7) * b.get_unchecked(base + 7);
        }
    }

    // Handle remainder
    let base = chunks * SIMD_LANES;
    for i in 0..remainder {
        unsafe {
            sum0 += a.get_unchecked(base + i) * b.get_unchecked(base + i);
        }
    }

    sum0 + sum1 + sum2 + sum3 + sum4 + sum5 + sum6 + sum7
}

/// Calculate dot product of two vectors (scalar fallback)
#[must_use]
fn dot_product(a: &[f32], b: &[f32]) -> f32 {
    a.iter().zip(b.iter()).map(|(x, y)| x * y).sum()
}

/// Calculate L2 norm of a vector
#[must_use]
fn norm(v: &[f32]) -> f32 {
    dot_product(v, v).sqrt()
}

/// Calculate L2 norm using SIMD-optimized dot product
#[must_use]
#[inline(always)]
fn norm_simd(v: &[f32]) -> f32 {
    dot_product_simd(v, v).sqrt()
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

/// SIMD-optimized cosine similarity using unrolled loop for auto-vectorization
#[must_use]
#[inline(always)]
pub fn cosine_similarity_simd(a: &[f32], b: &[f32]) -> f32 {
    let dot = dot_product_simd(a, b);
    let norm_a = norm_simd(a);
    let norm_b = norm_simd(b);

    if norm_a < EPSILON || norm_b < EPSILON {
        return 0.0;
    }

    let result = dot / (norm_a * norm_b);
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

/// SIMD-optimized batch cosine similarity using parallel processing + SIMD dot products.
/// Combines Rayon parallelism with SIMD-friendly loop unrolling for maximum throughput.
#[must_use]
pub fn batch_cosine_similarity_simd(query: &[f32], matrix: &[&[f32]]) -> Vec<f32> {
    // Pre-compute query norm once (avoids redundant computation)
    let query_norm = norm_simd(query);
    if query_norm < EPSILON {
        return vec![0.0; matrix.len()];
    }

    matrix
        .par_iter()
        .map(|row| {
            let dot = dot_product_simd(query, row);
            let row_norm = norm_simd(row);

            if row_norm < EPSILON {
                0.0
            } else {
                (dot / (query_norm * row_norm)).clamp(-1.0, 1.0)
            }
        })
        .collect()
}

/// SIMD-optimized batch similarity with row-major matrix format.
/// Takes a flat matrix with known dimensions for cache-friendly access.
#[must_use]
pub fn batch_cosine_similarity_simd_flat(
    query: &[f32],
    matrix: &[f32],
    num_rows: usize,
    dim: usize,
) -> Vec<f32> {
    if query.len() != dim || matrix.len() != num_rows * dim {
        return vec![0.0; num_rows];
    }

    let query_norm = norm_simd(query);
    if query_norm < EPSILON {
        return vec![0.0; num_rows];
    }

    (0..num_rows)
        .into_par_iter()
        .map(|i| {
            let row_start = i * dim;
            let row = &matrix[row_start..row_start + dim];
            let dot = dot_product_simd(query, row);
            let row_norm = norm_simd(row);

            if row_norm < EPSILON {
                0.0
            } else {
                (dot / (query_norm * row_norm)).clamp(-1.0, 1.0)
            }
        })
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

/// SIMD-optimized find top-k with flat matrix format
#[must_use]
pub fn find_top_k_simd(
    query: &[f32],
    matrix: &[f32],
    num_rows: usize,
    dim: usize,
    k: usize,
) -> Vec<(usize, f32)> {
    if query.len() != dim || matrix.len() != num_rows * dim {
        return vec![];
    }

    let scores = batch_cosine_similarity_simd_flat(query, matrix, num_rows, dim);

    let mut indexed_scores: Vec<(usize, f32)> = scores.into_iter().enumerate().collect();
    indexed_scores.sort_by(|a, b| b.1.total_cmp(&a.1));
    indexed_scores.truncate(k);
    indexed_scores
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

    #[test]
    fn test_simd_cosine_similarity_matches_scalar() {
        let a = vec![1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0];
        let b = vec![10.0, 9.0, 8.0, 7.0, 6.0, 5.0, 4.0, 3.0, 2.0, 1.0];

        let scalar = cosine_similarity(&a, &b);
        let simd = cosine_similarity_simd(&a, &b);

        assert!((scalar - simd).abs() < 1e-5, "scalar={}, simd={}", scalar, simd);
    }

    #[test]
    fn test_batch_simd_matches_regular() {
        let query = vec![1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0];
        let row1 = vec![8.0, 7.0, 6.0, 5.0, 4.0, 3.0, 2.0, 1.0];
        let row2 = vec![1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0];
        let row3 = vec![0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0];
        let matrix: Vec<&[f32]> = vec![&row1, &row2, &row3];

        let regular = batch_cosine_similarity(&query, &matrix);
        let simd = batch_cosine_similarity_simd(&query, &matrix);

        assert_eq!(regular.len(), simd.len());
        for (r, s) in regular.iter().zip(simd.iter()) {
            assert!((r - s).abs() < 1e-5, "regular={}, simd={}", r, s);
        }
    }

    #[test]
    fn test_flat_matrix_batch() {
        let query = vec![1.0, 0.0, 0.0, 0.0];
        let matrix = vec![
            1.0, 0.0, 0.0, 0.0, // row 0: identical
            0.0, 1.0, 0.0, 0.0, // row 1: orthogonal
            0.5, 0.5, 0.0, 0.0, // row 2: partial match
        ];

        let results = batch_cosine_similarity_simd_flat(&query, &matrix, 3, 4);
        assert_eq!(results.len(), 3);
        assert!((results[0] - 1.0).abs() < 1e-5); // identical
        assert!(results[1].abs() < 1e-5); // orthogonal
        assert!(results[2] > 0.5 && results[2] < 1.0); // partial
    }

    #[test]
    fn test_find_top_k_simd() {
        let query = vec![1.0, 0.0, 0.0, 0.0];
        let matrix = vec![
            0.0, 1.0, 0.0, 0.0, // row 0: orthogonal (score 0)
            1.0, 0.0, 0.0, 0.0, // row 1: identical (score 1)
            0.5, 0.5, 0.0, 0.0, // row 2: partial
        ];

        let results = find_top_k_simd(&query, &matrix, 3, 4, 2);
        assert_eq!(results.len(), 2);
        assert_eq!(results[0].0, 1); // row 1 should be first (highest score)
        assert!((results[0].1 - 1.0).abs() < 1e-5);
    }
}
