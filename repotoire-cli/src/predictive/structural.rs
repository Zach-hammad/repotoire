//! L2: Structural surprise via Mahalanobis distance on function feature vectors.
//!
//! Reference: "Why is the Mahalanobis Distance Effective for Anomaly Detection?" (arXiv 2003.00402)

/// Extract a feature vector from raw function metrics.
pub fn extract_structural_features_raw(
    param_count: i64,
    complexity: i64,
    nesting_depth: i64,
    loc: u32,
    return_count: i64,
) -> Vec<f64> {
    vec![
        param_count as f64,
        complexity as f64,
        nesting_depth as f64,
        loc as f64,
        return_count as f64,
    ]
}

/// Computes Mahalanobis distance for structural anomaly detection.
pub struct StructuralScorer {
    mean: Vec<f64>,
    inv_cov: Vec<Vec<f64>>,
    dim: usize,
}

impl StructuralScorer {
    /// Build from a collection of feature vectors.
    pub fn from_features(features: &[Vec<f64>]) -> Self {
        let n = features.len();
        if n == 0 || features[0].is_empty() {
            return Self {
                mean: vec![],
                inv_cov: vec![],
                dim: 0,
            };
        }
        let dim = features[0].len();

        // Compute mean
        let mut mean = vec![0.0; dim];
        for f in features {
            for (i, v) in f.iter().enumerate() {
                mean[i] += v;
            }
        }
        for m in &mut mean {
            *m /= n as f64;
        }

        // Compute covariance matrix
        let mut cov = vec![vec![0.0; dim]; dim];
        for f in features {
            for i in 0..dim {
                for j in 0..dim {
                    cov[i][j] += (f[i] - mean[i]) * (f[j] - mean[j]);
                }
            }
        }
        for row in &mut cov {
            for v in row.iter_mut() {
                *v /= n as f64;
            }
        }

        // Regularize diagonal to ensure positive definiteness
        for i in 0..dim {
            cov[i][i] += 1e-6;
        }

        let inv_cov = invert_matrix(&cov);

        Self {
            mean,
            inv_cov,
            dim,
        }
    }

    /// Compute Mahalanobis distance of a point from the distribution.
    pub fn mahalanobis_distance(&self, point: &[f64]) -> f64 {
        if self.dim == 0 || point.len() != self.dim {
            return 0.0;
        }
        let diff: Vec<f64> = point.iter().zip(&self.mean).map(|(p, m)| p - m).collect();
        let mut result = 0.0;
        for i in 0..self.dim {
            for j in 0..self.dim {
                result += diff[i] * self.inv_cov[i][j] * diff[j];
            }
        }
        result.max(0.0).sqrt()
    }
}

/// Gauss-Jordan matrix inversion for small matrices (dim <= 6).
fn invert_matrix(matrix: &[Vec<f64>]) -> Vec<Vec<f64>> {
    let n = matrix.len();
    let mut aug = vec![vec![0.0; 2 * n]; n];
    for i in 0..n {
        for j in 0..n {
            aug[i][j] = matrix[i][j];
        }
        aug[i][n + i] = 1.0;
    }
    for col in 0..n {
        // Partial pivoting
        let mut max_row = col;
        for row in (col + 1)..n {
            if aug[row][col].abs() > aug[max_row][col].abs() {
                max_row = row;
            }
        }
        aug.swap(col, max_row);

        let pivot = aug[col][col];
        if pivot.abs() < 1e-12 {
            // Singular matrix fallback: return identity
            return (0..n)
                .map(|i| {
                    let mut row = vec![0.0; n];
                    row[i] = 1.0;
                    row
                })
                .collect();
        }

        for j in 0..(2 * n) {
            aug[col][j] /= pivot;
        }

        for row in 0..n {
            if row != col {
                let factor = aug[row][col];
                for j in 0..(2 * n) {
                    aug[row][j] -= factor * aug[col][j];
                }
            }
        }
    }
    aug.iter().map(|row| row[n..].to_vec()).collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_mahalanobis_near_mean_is_small() {
        // Build a scorer from a cluster of similar points
        let features: Vec<Vec<f64>> = (0..50)
            .map(|i| {
                let offset = (i as f64) * 0.1 - 2.5;
                vec![
                    3.0 + offset,
                    5.0 + offset * 0.5,
                    2.0 + offset * 0.3,
                    20.0 + offset * 2.0,
                    1.0 + offset * 0.1,
                ]
            })
            .collect();

        let scorer = StructuralScorer::from_features(&features);

        // A point very close to the mean should have a small distance
        let near_mean = vec![3.0, 5.0, 2.0, 20.0, 1.0];
        let dist = scorer.mahalanobis_distance(&near_mean);
        assert!(
            dist < 2.0,
            "Point near mean should have small Mahalanobis distance, got {dist}"
        );
    }

    #[test]
    fn test_outlier_has_high_distance() {
        // 100 identical points (with regularization they form a tight cluster)
        let normal_point = vec![3.0, 5.0, 2.0, 20.0, 1.0];
        let mut features: Vec<Vec<f64>> = (0..100).map(|_| normal_point.clone()).collect();

        // Add one outlier to the training set so scorer sees some variation
        let outlier = vec![30.0, 50.0, 20.0, 200.0, 10.0];
        features.push(outlier.clone());

        let scorer = StructuralScorer::from_features(&features);

        let dist_normal = scorer.mahalanobis_distance(&normal_point);
        let dist_outlier = scorer.mahalanobis_distance(&outlier);

        assert!(
            dist_outlier > dist_normal,
            "Outlier distance ({dist_outlier}) should be greater than normal distance ({dist_normal})"
        );
        assert!(
            dist_outlier > 5.0,
            "Outlier should have a clearly high distance, got {dist_outlier}"
        );
    }

    #[test]
    fn test_feature_extraction() {
        let features = extract_structural_features_raw(3, 10, 4, 150, 2);
        assert_eq!(features.len(), 5);
        assert!((features[0] - 3.0).abs() < f64::EPSILON);
        assert!((features[1] - 10.0).abs() < f64::EPSILON);
        assert!((features[2] - 4.0).abs() < f64::EPSILON);
        assert!((features[3] - 150.0).abs() < f64::EPSILON);
        assert!((features[4] - 2.0).abs() < f64::EPSILON);
    }

    #[test]
    fn test_empty_features() {
        // Empty input
        let scorer = StructuralScorer::from_features(&[]);
        assert_eq!(scorer.dim, 0);
        assert_eq!(scorer.mahalanobis_distance(&[1.0, 2.0]), 0.0);

        // Empty feature vectors
        let scorer = StructuralScorer::from_features(&[vec![]]);
        assert_eq!(scorer.dim, 0);
        assert_eq!(scorer.mahalanobis_distance(&[]), 0.0);

        // Dimension mismatch returns 0
        let features = vec![vec![1.0, 2.0], vec![3.0, 4.0]];
        let scorer = StructuralScorer::from_features(&features);
        assert_eq!(scorer.mahalanobis_distance(&[1.0, 2.0, 3.0]), 0.0);
    }

    #[test]
    fn test_invert_identity() {
        // 3x3 identity matrix should invert to itself
        let identity = vec![
            vec![1.0, 0.0, 0.0],
            vec![0.0, 1.0, 0.0],
            vec![0.0, 0.0, 1.0],
        ];
        let inv = invert_matrix(&identity);

        for i in 0..3 {
            for j in 0..3 {
                let expected = if i == j { 1.0 } else { 0.0 };
                assert!(
                    (inv[i][j] - expected).abs() < 1e-10,
                    "inv[{i}][{j}] = {}, expected {expected}",
                    inv[i][j]
                );
            }
        }
    }

    #[test]
    fn test_invert_known_matrix() {
        // 2x2 matrix [[2, 1], [1, 3]] has known inverse [[3/5, -1/5], [-1/5, 2/5]]
        let matrix = vec![vec![2.0, 1.0], vec![1.0, 3.0]];
        let inv = invert_matrix(&matrix);

        assert!((inv[0][0] - 0.6).abs() < 1e-10, "inv[0][0] = {}", inv[0][0]);
        assert!(
            (inv[0][1] - (-0.2)).abs() < 1e-10,
            "inv[0][1] = {}",
            inv[0][1]
        );
        assert!(
            (inv[1][0] - (-0.2)).abs() < 1e-10,
            "inv[1][0] = {}",
            inv[1][0]
        );
        assert!((inv[1][1] - 0.4).abs() < 1e-10, "inv[1][1] = {}", inv[1][1]);
    }

    #[test]
    fn test_mahalanobis_with_correlated_features() {
        // Create features where params and complexity are strongly correlated
        // but LOC is independent. Points deviating from the correlation
        // should have higher Mahalanobis distance than points deviating
        // along the correlation axis.
        let features: Vec<Vec<f64>> = (0..100)
            .map(|i| {
                let x = (i as f64) * 0.1;
                vec![x, x * 2.0] // perfectly correlated: complexity = 2 * params
            })
            .collect();

        let scorer = StructuralScorer::from_features(&features);

        // Point along the correlation (high but consistent)
        let along = vec![15.0, 30.0]; // follows pattern
        // Point breaking the correlation
        let breaking = vec![5.0, 30.0]; // params low but complexity high

        let dist_along = scorer.mahalanobis_distance(&along);
        let dist_breaking = scorer.mahalanobis_distance(&breaking);

        assert!(
            dist_breaking > dist_along,
            "Breaking correlation ({dist_breaking}) should give higher distance than following it ({dist_along})"
        );
    }

    #[test]
    fn test_distance_at_mean_is_zero() {
        let features = vec![
            vec![1.0, 2.0, 3.0],
            vec![3.0, 4.0, 5.0],
            vec![5.0, 6.0, 7.0],
        ];
        let scorer = StructuralScorer::from_features(&features);

        // The mean is [3.0, 4.0, 5.0]
        let dist = scorer.mahalanobis_distance(&[3.0, 4.0, 5.0]);
        assert!(
            dist < 1e-6,
            "Distance at the mean should be ~0, got {dist}"
        );
    }
}
