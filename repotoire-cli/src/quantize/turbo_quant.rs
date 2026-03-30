//! TurboQuant: near-optimal vector quantization via random rotation + scalar quantization.
//!
//! Algorithm: rotate by random orthogonal matrix, quantize each coordinate
//! independently using a Lloyd-Max codebook optimized for the Beta distribution
//! of unit-sphere coordinates.
//!
//! Reference: Zandieh et al. 2025, "TurboQuant: Online Vector Quantization
//! with Near-optimal Distortion Rate" (arXiv:2504.19874)

use nalgebra::DMatrix;
use rand::Rng;
use rand::SeedableRng;
use rand_chacha::ChaCha8Rng;

/// Configuration for TurboQuant quantizer.
#[derive(Debug, Clone)]
pub struct TurboQuantConfig {
    pub dim: usize,
    pub bits: usize,
    pub seed: u64,
}

impl Default for TurboQuantConfig {
    fn default() -> Self {
        Self { dim: 128, bits: 4, seed: 42 }
    }
}

/// Precomputed quantization state: rotation matrix + codebook.
pub struct TurboQuantCodebook {
    pub(crate) rotation: DMatrix<f64>,
    pub(crate) rotation_t: DMatrix<f64>,
    centroids: Vec<f64>,
    boundaries: Vec<f64>,
    dim: usize,
    bits: usize,
    num_levels: usize,
}

/// A quantized vector: packed codebook indices + original norm.
#[derive(Debug, Clone)]
pub struct QuantizedVector {
    pub indices: Vec<u8>,
    pub norm: f64,
}

/// Precomputed ADC distance table for a single query.
pub struct DistanceTable {
    table: Vec<f64>,
    num_levels: usize,
}

// ============================================================================
// CODEBOOK
// ============================================================================

/// Lloyd-Max optimal centroids for N(0, 1/d) at 4-bit (16 levels).
fn lloyd_max_codebook_4bit(dim: usize) -> (Vec<f64>, Vec<f64>) {
    let std_centroids = [
        -2.7326, -2.0690, -1.6180, -1.2562,
        -0.9424, -0.6568, -0.3882, -0.1284,
         0.1284,  0.3882,  0.6568,  0.9424,
         1.2562,  1.6180,  2.0690,  2.7326,
    ];
    let scale = 1.0 / (dim as f64).sqrt();
    let centroids: Vec<f64> = std_centroids.iter().map(|&c| c * scale).collect();
    let boundaries: Vec<f64> = centroids.windows(2).map(|w| (w[0] + w[1]) / 2.0).collect();
    (centroids, boundaries)
}

/// Naive uniform scalar quantizer for baseline comparison.
pub(crate) fn uniform_codebook_4bit(dim: usize) -> (Vec<f64>, Vec<f64>) {
    let range = 3.0 / (dim as f64).sqrt();
    let num_levels = 16usize;
    let step = 2.0 * range / num_levels as f64;
    let centroids: Vec<f64> = (0..num_levels)
        .map(|i| -range + step * (i as f64 + 0.5))
        .collect();
    let boundaries: Vec<f64> = centroids.windows(2).map(|w| (w[0] + w[1]) / 2.0).collect();
    (centroids, boundaries)
}

/// Find the nearest centroid index for a scalar value.
pub(crate) fn quantize_scalar(value: f64, boundaries: &[f64]) -> u8 {
    match boundaries.binary_search_by(|b| b.partial_cmp(&value).unwrap()) {
        Ok(i) => i as u8 + 1,
        Err(i) => i as u8,
    }
}

// ============================================================================
// BIT PACKING
// ============================================================================

/// Pack 4-bit indices (0-15) into bytes, two per byte, lower nibble first.
pub fn pack_4bit(indices: &[u8]) -> Vec<u8> {
    assert!(indices.len() % 2 == 0, "indices length must be even");
    indices.chunks_exact(2).map(|pair| (pair[0] & 0x0F) | (pair[1] << 4)).collect()
}

/// Unpack bytes into 4-bit indices.
pub fn unpack_4bit(packed: &[u8], dim: usize) -> Vec<u8> {
    assert_eq!(packed.len(), dim / 2);
    let mut out = Vec::with_capacity(dim);
    for &byte in packed {
        out.push(byte & 0x0F);
        out.push(byte >> 4);
    }
    out
}

// ============================================================================
// CORE: NEW, QUANTIZE, RECONSTRUCT, ADC, KNN
// ============================================================================

impl TurboQuantCodebook {
    /// Create a new quantizer. Precomputes rotation matrix via QR and codebook.
    pub fn new(config: TurboQuantConfig) -> Self {
        let d = config.dim;
        let b = config.bits;
        let num_levels = 1 << b;

        let mut rng = ChaCha8Rng::seed_from_u64(config.seed);
        let data: Vec<f64> = (0..d * d).map(|_| {
            let u1: f64 = rng.random();
            let u2: f64 = rng.random();
            (-2.0 * (1.0 - u1).ln()).sqrt() * (2.0 * std::f64::consts::PI * u2).cos()
        }).collect();
        let g = DMatrix::from_vec(d, d, data);

        let qr = g.qr();
        let rotation = qr.q();
        let rotation_t = rotation.transpose();

        let (centroids, boundaries) = lloyd_max_codebook_4bit(d);

        Self { rotation, rotation_t, centroids, boundaries, dim: d, bits: b, num_levels }
    }

    /// Quantize a raw vector. Normalizes, rotates, scalar-quantizes, packs.
    pub fn quantize(&self, x: &[f64]) -> QuantizedVector {
        assert_eq!(x.len(), self.dim);
        let norm: f64 = x.iter().map(|v| v * v).sum::<f64>().sqrt();
        let inv_norm = if norm > 0.0 { 1.0 / norm } else { 1.0 };
        let x_vec = nalgebra::DVector::from_iterator(self.dim, x.iter().map(|v| v * inv_norm));
        let y = &self.rotation * &x_vec;
        let indices: Vec<u8> = (0..self.dim).map(|j| quantize_scalar(y[j], &self.boundaries)).collect();
        QuantizedVector { indices: pack_4bit(&indices), norm }
    }

    /// Reconstruct a quantized vector (lossy).
    pub fn reconstruct(&self, qv: &QuantizedVector) -> Vec<f64> {
        let indices = unpack_4bit(&qv.indices, self.dim);
        let y_hat: Vec<f64> = indices.iter().map(|&idx| self.centroids[idx as usize]).collect();
        let y_vec = nalgebra::DVector::from_vec(y_hat);
        let x_hat = &self.rotation_t * &y_vec;
        x_hat.iter().map(|v| v * qv.norm).collect()
    }

    /// Precompute ADC distance table for a query vector.
    pub fn build_distance_table(&self, query: &[f64]) -> DistanceTable {
        assert_eq!(query.len(), self.dim);
        let norm: f64 = query.iter().map(|v| v * v).sum::<f64>().sqrt();
        let inv_norm = if norm > 0.0 { 1.0 / norm } else { 1.0 };
        let q_vec = nalgebra::DVector::from_iterator(self.dim, query.iter().map(|v| v * inv_norm));
        let q_rot = &self.rotation * &q_vec;
        let mut table = Vec::with_capacity(self.dim * self.num_levels);
        for j in 0..self.dim {
            for k in 0..self.num_levels {
                let diff = q_rot[j] - self.centroids[k];
                table.push(diff * diff);
            }
        }
        DistanceTable { table, num_levels: self.num_levels }
    }

    /// Approximate squared L2 distance between normalized query and quantized vector.
    /// For cosine similarity: cos_sim ≈ 1 - adc_distance() / 2
    pub fn adc_distance(&self, table: &DistanceTable, qv: &QuantizedVector) -> f64 {
        let indices = unpack_4bit(&qv.indices, self.dim);
        let mut dist = 0.0;
        for j in 0..self.dim {
            dist += table.table[j * table.num_levels + indices[j] as usize];
        }
        dist
    }

    /// Brute-force kNN search over quantized database using ADC.
    /// Returns (index, approximate_cosine_similarity) sorted descending.
    pub fn knn_search(
        &self,
        query: &[f64],
        database: &[QuantizedVector],
        k: usize,
    ) -> Vec<(usize, f64)> {
        let table = self.build_distance_table(query);
        let mut results: Vec<(usize, f64)> = database
            .iter()
            .enumerate()
            .map(|(i, qv)| {
                let dist_sq = self.adc_distance(&table, qv);
                (i, 1.0 - dist_sq / 2.0)
            })
            .collect();
        results.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal));
        results.truncate(k);
        results
    }
}

// ============================================================================
// TESTS
// ============================================================================

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_pack_unpack_roundtrip() {
        let indices: Vec<u8> = (0..128).map(|i| (i % 16) as u8).collect();
        let packed = pack_4bit(&indices);
        assert_eq!(packed.len(), 64);
        let unpacked = unpack_4bit(&packed, 128);
        assert_eq!(unpacked, indices);
    }

    #[test]
    fn test_pack_boundary_values() {
        let indices = vec![0u8, 15, 7, 8];
        let packed = pack_4bit(&indices);
        assert_eq!(packed, vec![0xF0, 0x87]);
        let unpacked = unpack_4bit(&packed, 4);
        assert_eq!(unpacked, indices);
    }

    #[test]
    fn test_codebook_has_16_centroids() {
        let (centroids, boundaries) = lloyd_max_codebook_4bit(128);
        assert_eq!(centroids.len(), 16);
        assert_eq!(boundaries.len(), 15);
    }

    #[test]
    fn test_codebook_symmetric() {
        let (centroids, _) = lloyd_max_codebook_4bit(128);
        for i in 0..8 {
            assert!((centroids[i] + centroids[15 - i]).abs() < 1e-10);
        }
    }

    #[test]
    fn test_codebook_sorted() {
        let (centroids, boundaries) = lloyd_max_codebook_4bit(128);
        for w in centroids.windows(2) { assert!(w[0] < w[1]); }
        for w in boundaries.windows(2) { assert!(w[0] < w[1]); }
    }

    #[test]
    fn test_quantize_scalar_center() {
        let (centroids, boundaries) = lloyd_max_codebook_4bit(128);
        assert_eq!(quantize_scalar(centroids[8], &boundaries), 8);
    }

    #[test]
    fn test_quantize_scalar_extreme() {
        let (_, boundaries) = lloyd_max_codebook_4bit(128);
        assert_eq!(quantize_scalar(-1.0, &boundaries), 0);
        assert_eq!(quantize_scalar(1.0, &boundaries), 15);
    }

    #[test]
    fn test_rotation_orthogonal() {
        let cb = TurboQuantCodebook::new(TurboQuantConfig::default());
        let product = &cb.rotation_t * &cb.rotation;
        let identity = DMatrix::identity(128, 128);
        let diff = (&product - &identity).norm();
        assert!(diff < 1e-10, "R^T * R should be identity, diff = {diff}");
    }

    fn random_vec(rng: &mut ChaCha8Rng, dim: usize) -> Vec<f64> {
        (0..dim).map(|_| {
            let u1: f64 = rng.random();
            let u2: f64 = rng.random();
            (-2.0 * (1.0 - u1).ln()).sqrt() * (2.0 * std::f64::consts::PI * u2).cos()
        }).collect()
    }

    #[test]
    fn test_quantize_reconstruct_cosine() {
        let cb = TurboQuantCodebook::new(TurboQuantConfig::default());
        let mut rng = ChaCha8Rng::seed_from_u64(123);
        let x = random_vec(&mut rng, 128);
        let qv = cb.quantize(&x);
        let x_hat = cb.reconstruct(&qv);
        let dot: f64 = x.iter().zip(&x_hat).map(|(a, b)| a * b).sum();
        let norm_x = x.iter().map(|v| v * v).sum::<f64>().sqrt();
        let norm_xh = x_hat.iter().map(|v| v * v).sum::<f64>().sqrt();
        let cos_sim = dot / (norm_x * norm_xh);
        assert!(cos_sim > 0.99, "4-bit cosine should be > 0.99, got {cos_sim}");
    }

    #[test]
    fn test_quantize_preserves_norm() {
        let cb = TurboQuantCodebook::new(TurboQuantConfig::default());
        let x: Vec<f64> = (0..128).map(|i| (i as f64) * 0.1).collect();
        let qv = cb.quantize(&x);
        let x_hat = cb.reconstruct(&qv);
        let norm_x = x.iter().map(|v| v * v).sum::<f64>().sqrt();
        let norm_xh = x_hat.iter().map(|v| v * v).sum::<f64>().sqrt();
        let rel_err = (norm_x - norm_xh).abs() / norm_x;
        assert!(rel_err < 0.1, "rel_err = {rel_err}");
    }

    #[test]
    fn test_adc_matches_reconstruct() {
        let cb = TurboQuantCodebook::new(TurboQuantConfig::default());
        let mut rng = ChaCha8Rng::seed_from_u64(456);
        let query = random_vec(&mut rng, 128);
        let x = random_vec(&mut rng, 128);
        let qv = cb.quantize(&x);
        let table = cb.build_distance_table(&query);
        let adc_dist = cb.adc_distance(&table, &qv);
        let x_hat = cb.reconstruct(&qv);
        let q_norm: f64 = query.iter().map(|v| v * v).sum::<f64>().sqrt();
        let xh_norm: f64 = x_hat.iter().map(|v| v * v).sum::<f64>().sqrt();
        let q_hat: Vec<f64> = query.iter().map(|v| v / q_norm).collect();
        let xh_hat: Vec<f64> = x_hat.iter().map(|v| v / xh_norm).collect();
        let direct_dist: f64 = q_hat.iter().zip(&xh_hat).map(|(a, b)| (a - b).powi(2)).sum();
        assert!((adc_dist - direct_dist).abs() < 0.01,
            "ADC={adc_dist}, direct={direct_dist}");
    }

    #[test]
    fn test_knn_returns_k_results() {
        let cb = TurboQuantCodebook::new(TurboQuantConfig::default());
        let mut rng = ChaCha8Rng::seed_from_u64(789);
        let database: Vec<QuantizedVector> = (0..100).map(|_| cb.quantize(&random_vec(&mut rng, 128))).collect();
        let query = random_vec(&mut rng, 128);
        let results = cb.knn_search(&query, &database, 10);
        assert_eq!(results.len(), 10);
        for w in results.windows(2) {
            assert!(w[0].1 >= w[1].1, "should be sorted descending");
        }
    }
}
