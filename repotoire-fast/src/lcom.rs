use rayon::prelude::*;
use std::collections::HashSet;

/// Calculate LCOM (Lack of Cohesion of Methods) metric.
///
/// LCOM measures how well methods work together:
/// - 0.0 = high cohesion (methods share fields)
/// - 1.0 = low cohesion (methods work independently)
///
/// Algorithm: For each pair of methods, check if they share fields.
/// Return ratio of non-sharing pairs to total pairs.
pub fn calculate_lcom(method_field_pairs: &[(String, Vec<String>)]) -> f64 {
    let n = method_field_pairs.len();

    // Single method or no methods = perfectly cohesive
    if n <= 1 {
        return 0.0;
    }

    // Pre-compute field sets for each method
    let field_sets: Vec<HashSet<&str>> = method_field_pairs
        .iter()
        .map(|(_, fields)| fields.iter().map(|s| s.as_str()).collect())
        .collect();

    // Count non-sharing pairs using parallel iteration
    // We iterate over all pairs (i, j) where i < j
    let non_sharing_pairs: usize = (0..n)
        .into_par_iter()
        .map(|i| {
            let fields_i = &field_sets[i];
            let mut count = 0;
            for j in (i + 1)..n {
                let fields_j = &field_sets[j];
                // If no intersection, they don't share fields
                if fields_i.is_disjoint(fields_j) {
                    count += 1;
                }
            }
            count
        })
        .sum();

    let total_pairs = n * (n - 1) / 2;

    if total_pairs == 0 {
        return 0.0;
    }

    non_sharing_pairs as f64 / total_pairs as f64
}

/// Calculate LCOM for multiple classes in parallel.
/// Takes list of (class_name, [(method_name, [field_names])]).
/// Returns list of (class_name, lcom_score).
pub fn calculate_lcom_batch(
    classes: Vec<(String, Vec<(String, Vec<String>)>)>
) -> Vec<(String, f64)> {
    classes
        .into_par_iter()
        .map(|(class_name, method_field_pairs)| {
            let lcom = calculate_lcom(&method_field_pairs);
            (class_name, lcom)
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_empty_methods() {
        let pairs: Vec<(String, Vec<String>)> = vec![];
        assert_eq!(calculate_lcom(&pairs), 0.0);
    }

    #[test]
    fn test_single_method() {
        let pairs = vec![
            ("method1".to_string(), vec!["field_a".to_string()]),
        ];
        assert_eq!(calculate_lcom(&pairs), 0.0);
    }

    #[test]
    fn test_two_methods_sharing_field() {
        // Both methods use field_a -> they share -> LCOM = 0
        let pairs = vec![
            ("method1".to_string(), vec!["field_a".to_string()]),
            ("method2".to_string(), vec!["field_a".to_string()]),
        ];
        assert_eq!(calculate_lcom(&pairs), 0.0);
    }

    #[test]
    fn test_two_methods_no_sharing() {
        // method1 uses field_a, method2 uses field_b -> no sharing -> LCOM = 1
        let pairs = vec![
            ("method1".to_string(), vec!["field_a".to_string()]),
            ("method2".to_string(), vec!["field_b".to_string()]),
        ];
        assert_eq!(calculate_lcom(&pairs), 1.0);
    }

    #[test]
    fn test_three_methods_mixed() {
        // method1: field_a
        // method2: field_a, field_b
        // method3: field_c
        // Pairs: (1,2) share a, (1,3) no share, (2,3) no share
        // Non-sharing: 2, Total: 3, LCOM = 2/3 ≈ 0.667
        let pairs = vec![
            ("method1".to_string(), vec!["field_a".to_string()]),
            ("method2".to_string(), vec!["field_a".to_string(), "field_b".to_string()]),
            ("method3".to_string(), vec!["field_c".to_string()]),
        ];
        let lcom = calculate_lcom(&pairs);
        assert!((lcom - 0.6666666666666666).abs() < 0.001);
    }

    #[test]
    fn test_methods_with_empty_fields() {
        // Methods with no fields are disjoint with everything
        let pairs = vec![
            ("method1".to_string(), vec![]),
            ("method2".to_string(), vec![]),
        ];
        assert_eq!(calculate_lcom(&pairs), 1.0);
    }

    #[test]
    fn test_batch_calculation() {
        let classes = vec![
            (
                "ClassA".to_string(),
                vec![
                    ("m1".to_string(), vec!["f1".to_string()]),
                    ("m2".to_string(), vec!["f1".to_string()]),
                ],
            ),
            (
                "ClassB".to_string(),
                vec![
                    ("m1".to_string(), vec!["f1".to_string()]),
                    ("m2".to_string(), vec!["f2".to_string()]),
                ],
            ),
        ];

        let results = calculate_lcom_batch(classes);
        assert_eq!(results.len(), 2);

        // Find results by class name
        let class_a = results.iter().find(|(name, _)| name == "ClassA").unwrap();
        let class_b = results.iter().find(|(name, _)| name == "ClassB").unwrap();

        assert_eq!(class_a.1, 0.0);  // ClassA is cohesive
        assert_eq!(class_b.1, 1.0);  // ClassB has no cohesion
    }
}
