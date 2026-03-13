use super::*;

#[test]
fn test_feature_extraction() {
    let features = FunctionFeatures::extract(&FunctionMetrics {
        name: "u3r_word",
        file_path: "pkg/noun/retrieve.c",
        fan_in: 50,
        fan_out: 10,
        max_fan_in: 100,
        max_fan_out: 50,
        caller_files: 20,
        complexity: Some(15),
        avg_complexity: 10.0,
        loc: 30,
        avg_loc: 25.0,
        param_count: 2,
        avg_params: 2.5,
        address_taken: false,
    });

    assert!(features.has_short_prefix);
    assert!(!features.has_test_prefix);
    assert!(!features.has_handler_suffix);
    assert!(features.fan_in_ratio > 0.4);
}

#[test]
fn test_classify_utility() {
    let hmm = ContextHMM::new();
    let features = FunctionFeatures {
        has_short_prefix: true,
        fan_in_ratio: 0.8,
        caller_file_spread: 0.7,
        in_util_path: true,
        ..Default::default()
    };

    let context = hmm.classify(&features);
    assert_eq!(context, FunctionContext::Utility);
}

#[test]
fn test_classify_handler() {
    let hmm = ContextHMM::new();
    let features = FunctionFeatures {
        has_handler_suffix: true,
        address_taken: true,
        in_handler_path: true,
        ..Default::default()
    };

    let context = hmm.classify(&features);
    assert_eq!(context, FunctionContext::Handler);
}

#[test]
fn test_classify_test() {
    let hmm = ContextHMM::new();
    let features = FunctionFeatures {
        has_test_prefix: true,
        in_test_path: true,
        ..Default::default()
    };

    let context = hmm.classify(&features);
    assert_eq!(context, FunctionContext::Test);
}

#[test]
fn test_viterbi() {
    let hmm = ContextHMM::new();

    // Sequence of functions that should be classified as Test
    let features = vec![
        FunctionFeatures {
            has_test_prefix: true,
            in_test_path: true,
            ..Default::default()
        },
        FunctionFeatures {
            has_test_prefix: true,
            in_test_path: true,
            ..Default::default()
        },
        FunctionFeatures {
            has_test_prefix: true,
            in_test_path: true,
            ..Default::default()
        },
    ];

    let path = hmm.classify_sequence(&features);
    assert!(path.iter().all(|&c| c == FunctionContext::Test));
}
