//! Minimal dense matrix for TurboQuant rotation operations.
//! Replaces nalgebra — we only need: construct, QR decomposition, matrix×vector, transpose.

/// Row-major dense matrix of f64.
#[derive(Debug, Clone)]
pub struct DenseMatrix {
    pub rows: usize,
    pub cols: usize,
    /// Row-major storage: data[i * cols + j]
    pub data: Vec<f64>,
}

impl DenseMatrix {
    /// Create from column-major data (matches nalgebra's DMatrix::from_vec layout).
    pub fn from_col_major(rows: usize, cols: usize, col_major: Vec<f64>) -> Self {
        assert_eq!(col_major.len(), rows * cols);
        let mut data = vec![0.0; rows * cols];
        for c in 0..cols {
            for r in 0..rows {
                data[r * cols + c] = col_major[c * rows + r];
            }
        }
        Self { rows, cols, data }
    }

    #[inline]
    pub fn get(&self, r: usize, c: usize) -> f64 {
        self.data[r * self.cols + c]
    }

    #[inline]
    pub fn set(&mut self, r: usize, c: usize, val: f64) {
        self.data[r * self.cols + c] = val;
    }

    /// Transpose.
    pub fn transpose(&self) -> Self {
        let mut data = vec![0.0; self.rows * self.cols];
        for r in 0..self.rows {
            for c in 0..self.cols {
                data[c * self.rows + r] = self.data[r * self.cols + c];
            }
        }
        Self {
            rows: self.cols,
            cols: self.rows,
            data,
        }
    }

    /// Matrix × vector. Returns a Vec<f64> of length self.rows.
    pub fn mul_vec(&self, v: &[f64]) -> Vec<f64> {
        assert_eq!(v.len(), self.cols);
        let mut out = vec![0.0; self.rows];
        for r in 0..self.rows {
            let row_start = r * self.cols;
            let mut sum = 0.0;
            for c in 0..self.cols {
                sum += self.data[row_start + c] * v[c];
            }
            out[r] = sum;
        }
        out
    }

    /// Householder QR decomposition. Returns the orthogonal Q matrix.
    /// Only Q is needed for TurboQuant (rotation matrix from random Gaussian).
    /// Householder QR decomposition. Returns the orthogonal Q matrix.
    ///
    /// Uses the numerically stable Householder reflection formulation:
    /// v = x + sign(x₀)·‖x‖·e₁, then H = I - 2·v·vᵀ/‖v‖².
    /// The reflection factor τ = 2/‖v‖² is computed without normalizing v,
    /// avoiding the catastrophic cancellation that occurs when x₀ ≈ -‖x‖.
    pub fn qr_q(&self) -> Self {
        assert_eq!(self.rows, self.cols, "QR only implemented for square matrices");
        let n = self.rows;

        // Work on a copy (will become R)
        let mut r = self.data.clone();
        // Q starts as identity, accumulates Householder reflections
        let mut q = vec![0.0; n * n];
        for i in 0..n {
            q[i * n + i] = 1.0;
        }

        for k in 0..n {
            // Extract column k below diagonal
            let m = n - k;
            let mut v = vec![0.0; m];
            for i in 0..m {
                v[i] = r[(i + k) * n + k];
            }

            // Compute ‖x‖ using Kahan-style compensated summation for precision
            let mut sigma = 0.0f64;
            let mut comp = 0.0f64;
            for i in 1..m {
                let y = v[i] * v[i] - comp;
                let t = sigma + y;
                comp = (t - sigma) - y;
                sigma = t;
            }
            // sigma = Σ x[i]² for i>0 (tail norm squared)

            if sigma.abs() < 1e-30 && v[0].abs() < 1e-30 {
                continue; // zero column, skip
            }

            let x_norm = (v[0] * v[0] + sigma).sqrt();

            // Standard Householder: v = x + sign(x₀)·‖x‖·e₁
            // This avoids cancellation when x₀ and ‖x‖ have opposite signs.
            if v[0] >= 0.0 {
                v[0] += x_norm;
            } else {
                v[0] -= x_norm;
            }

            // τ = 2 / (vᵀv) — no normalization needed, just compute the factor
            let v_norm_sq: f64 = v.iter().map(|x| x * x).sum();
            if v_norm_sq < 1e-30 {
                continue;
            }
            let tau = 2.0 / v_norm_sq;

            // Apply H = I - τ·v·vᵀ to R columns k..n
            for j in k..n {
                let mut dot = 0.0;
                for i in 0..m {
                    dot += v[i] * r[(i + k) * n + j];
                }
                let scale = tau * dot;
                for i in 0..m {
                    r[(i + k) * n + j] -= scale * v[i];
                }
            }

            // Apply H to Q (Q = Q · H): for each row of Q, update columns k..k+m
            for j in 0..n {
                let mut dot = 0.0;
                for i in 0..m {
                    dot += v[i] * q[j * n + (i + k)];
                }
                let scale = tau * dot;
                for i in 0..m {
                    q[j * n + (i + k)] -= scale * v[i];
                }
            }
        }

        Self {
            rows: n,
            cols: n,
            data: q,
        }
    }

    /// Frobenius norm (for tests).
    #[cfg(test)]
    pub fn frobenius_norm(&self) -> f64 {
        self.data.iter().map(|v| v * v).sum::<f64>().sqrt()
    }

    /// Subtract another matrix (for tests).
    #[cfg(test)]
    pub fn sub(&self, other: &Self) -> Self {
        assert_eq!(self.rows, other.rows);
        assert_eq!(self.cols, other.cols);
        Self {
            rows: self.rows,
            cols: self.cols,
            data: self
                .data
                .iter()
                .zip(&other.data)
                .map(|(a, b)| a - b)
                .collect(),
        }
    }

    /// Identity matrix (for tests).
    #[cfg(test)]
    pub fn identity(n: usize) -> Self {
        let mut data = vec![0.0; n * n];
        for i in 0..n {
            data[i * n + i] = 1.0;
        }
        Self {
            rows: n,
            cols: n,
            data,
        }
    }

    /// Matrix multiply (for tests: Q^T * Q should be identity).
    #[cfg(test)]
    pub fn mul_mat(&self, other: &Self) -> Self {
        assert_eq!(self.cols, other.rows);
        let mut data = vec![0.0; self.rows * other.cols];
        for r in 0..self.rows {
            for c in 0..other.cols {
                let mut sum = 0.0;
                for k in 0..self.cols {
                    sum += self.get(r, k) * other.get(k, c);
                }
                data[r * other.cols + c] = sum;
            }
        }
        Self {
            rows: self.rows,
            cols: other.cols,
            data,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_qr_orthogonal_small() {
        let data = vec![1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 10.0];
        let m = DenseMatrix::from_col_major(3, 3, data);
        let q = m.qr_q();
        let qt = q.transpose();
        let product = qt.mul_mat(&q);
        let identity = DenseMatrix::identity(3);
        let diff = product.sub(&identity).frobenius_norm();
        assert!(diff < 1e-10, "Q^T * Q should be identity, diff = {diff}");
    }

    #[test]
    fn test_qr_orthogonal_128() {
        use rand::Rng;
        use rand::SeedableRng;
        use rand_chacha::ChaCha8Rng;
        let mut rng = ChaCha8Rng::seed_from_u64(42);
        let data: Vec<f64> = (0..128 * 128).map(|_| rng.random::<f64>() - 0.5).collect();
        let m = DenseMatrix::from_col_major(128, 128, data);
        let q = m.qr_q();
        let qt = q.transpose();
        let product = qt.mul_mat(&q);
        let identity = DenseMatrix::identity(128);
        let diff = product.sub(&identity).frobenius_norm();
        assert!(diff < 1e-8, "Q^T * Q should be identity, diff = {diff}");
    }

    #[test]
    fn test_mul_vec() {
        let m = DenseMatrix {
            rows: 2,
            cols: 2,
            data: vec![1.0, 2.0, 3.0, 4.0],
        };
        let result = m.mul_vec(&[5.0, 6.0]);
        assert!((result[0] - 17.0).abs() < 1e-10);
        assert!((result[1] - 39.0).abs() < 1e-10);
    }

    #[test]
    fn test_transpose() {
        let m = DenseMatrix {
            rows: 2,
            cols: 3,
            data: vec![1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
        };
        let t = m.transpose();
        assert_eq!(t.rows, 3);
        assert_eq!(t.cols, 2);
        assert!((t.get(0, 0) - 1.0).abs() < 1e-10);
        assert!((t.get(1, 0) - 2.0).abs() < 1e-10);
        assert!((t.get(0, 1) - 4.0).abs() < 1e-10);
    }
}
