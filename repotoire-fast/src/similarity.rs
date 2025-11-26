use rayon::prelude::*;
fn dot_product(a: &[f32], b: &[f32]) -> f32 {
    a.iter().zip(b.iter()).map(|(x, y)| x * y).sum()
}

fn norm(v: &[f32]) -> f32 {
    dot_product(v, v).sqrt()
}

pub fn cosine_similarity(a: &[f32], b: &[f32]) -> f32 {
    let dot = dot_product(a, b);
    let norm_a = norm(a);
    let norm_b = norm(b);

    if norm_a == 0.0 || norm_b == 0.0 {
        return 0.0;
    }

    dot / (norm_a * norm_b)
}

pub fn batch_cosine_similarity(query: &[f32], matrix: &[&[f32]]) -> Vec<f32> {
    matrix
        .par_iter()
        .map(|row| cosine_similarity(query,row))
        .collect()
}

pub fn find_top_k(query: &[f32], matrix: &[&[f32]], k: usize) -> Vec<(usize, f32)> {
    let mut scores: Vec<(usize, f32)> = matrix
        .par_iter()
        .enumerate()
        .map(|(i, row)| (i, cosine_similarity(query, row)))
        .collect();

    scores.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap());
    scores.truncate(k);
    scores
}