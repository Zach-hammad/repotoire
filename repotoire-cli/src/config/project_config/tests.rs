use super::*;

#[test]
fn test_normalize_detector_name() {
    assert_eq!(normalize_detector_name("GodClassDetector"), "god-class");
    assert_eq!(normalize_detector_name("god_class"), "god-class");
    assert_eq!(normalize_detector_name("god-class"), "god-class");
    // Consecutive uppercase stays together: SQL -> sql
    assert_eq!(
        normalize_detector_name("SQLInjectionDetector"),
        "sql-injection"
    );
    assert_eq!(normalize_detector_name("NPlusOneDetector"), "n-plus-one");
}

#[test]
fn test_glob_match() {
    // ** patterns
    assert!(glob_match("**/vendor/**", "src/vendor/lib/foo.py"));
    assert!(glob_match("generated/", "generated/model.py"));
    assert!(glob_match("*.test.ts", "foo.test.ts"));

    // Prefix patterns
    assert!(glob_match("vendor/", "vendor/lib/foo.py"));
    assert!(!glob_match("vendor/", "src/vendor/foo.py"));
}

#[test]
fn test_pillar_weights_validation() {
    let valid = PillarWeights {
        structure: 0.4,
        quality: 0.3,
        architecture: 0.3,
    };
    assert!(valid.is_valid());

    let invalid = PillarWeights {
        structure: 0.5,
        quality: 0.5,
        architecture: 0.5,
    };
    assert!(!invalid.is_valid());
}

#[test]
fn test_pillar_weights_normalize() {
    let mut weights = PillarWeights {
        structure: 2.0,
        quality: 1.0,
        architecture: 1.0,
    };
    weights.normalize();
    assert!((weights.structure - 0.5).abs() < 0.001);
    assert!((weights.quality - 0.25).abs() < 0.001);
    assert!((weights.architecture - 0.25).abs() < 0.001);
}

#[test]
fn test_threshold_value() {
    let int_val = ThresholdValue::Integer(42);
    assert_eq!(int_val.as_i64(), Some(42));
    assert_eq!(int_val.as_f64(), Some(42.0));
    assert_eq!(int_val.as_bool(), None);

    let float_val = ThresholdValue::Float(2.5);
    assert_eq!(float_val.as_i64(), Some(2));
    assert_eq!(float_val.as_f64(), Some(2.5));

    let bool_val = ThresholdValue::Boolean(true);
    assert_eq!(bool_val.as_bool(), Some(true));
    assert_eq!(bool_val.as_i64(), None);
}

#[test]
fn test_default_config() {
    let config = ProjectConfig::default();

    // All detectors enabled by default
    assert!(config.is_detector_enabled("god-class"));
    assert!(config.is_detector_enabled("unknown-detector"));

    // No severity overrides
    assert!(config.severity_override("god-class").is_none());

    // Default scoring
    assert!((config.scoring.security_multiplier - 3.0).abs() < 0.001);
    assert!(config.scoring.pillar_weights.is_valid());
}

#[test]
fn test_parse_toml_config() {
    let toml_content = r#"
[detectors.god-class]
enabled = true
thresholds = { method_count = 30, loc = 600 }

[detectors.sql-injection]
severity = "high"
enabled = false

[scoring]
security_multiplier = 5.0

[scoring.pillar_weights]
structure = 0.3
quality = 0.4
architecture = 0.3

[exclude]
paths = ["generated/", "vendor/"]

[defaults]
format = "json"
workers = 4
skip_detectors = ["debug-code"]
"#;

    let config: ProjectConfig = toml::from_str(toml_content).expect("parse project config");

    // Check detectors
    assert!(config.is_detector_enabled("god-class"));
    assert!(!config.is_detector_enabled("sql-injection"));
    assert_eq!(config.severity_override("sql-injection"), Some("high"));
    assert_eq!(
        config.threshold_i64("god-class", "method_count"),
        Some(30)
    );
    assert_eq!(config.threshold_i64("god-class", "loc"), Some(600));

    // Check scoring
    assert!((config.scoring.security_multiplier - 5.0).abs() < 0.001);
    assert!((config.scoring.pillar_weights.structure - 0.3).abs() < 0.001);

    // Check exclude
    assert_eq!(config.exclude.paths.len(), 2);
    assert!(config.should_exclude(Path::new("generated/foo.py")));

    // Check defaults
    assert_eq!(config.defaults.format, Some("json".to_string()));
    assert_eq!(config.defaults.workers, Some(4));
    assert!(config
        .defaults
        .skip_detectors
        .contains(&"debug-code".to_string()));
}

#[test]
fn test_default_exclude_patterns_applied() {
    let config = ExcludeConfig::default();
    let patterns = config.effective_patterns();
    assert!(patterns.contains(&"**/vendor/**".to_string()));
    assert!(patterns.contains(&"**/node_modules/**".to_string()));
    assert!(patterns.contains(&"**/*.min.js".to_string()));
    assert_eq!(patterns.len(), DEFAULT_EXCLUDE_PATTERNS.len());
}

#[test]
fn test_skip_defaults_disables_builtin_patterns() {
    let config = ExcludeConfig {
        paths: vec!["custom/".to_string()],
        skip_defaults: true,
    };
    let patterns = config.effective_patterns();
    assert_eq!(patterns, vec!["custom/"]);
    assert!(!patterns.contains(&"**/vendor/**".to_string()));
}

#[test]
fn test_user_patterns_merged_with_defaults() {
    let config = ExcludeConfig {
        paths: vec!["generated/".to_string()],
        skip_defaults: false,
    };
    let patterns = config.effective_patterns();
    assert!(patterns.contains(&"**/vendor/**".to_string()));
    assert!(patterns.contains(&"generated/".to_string()));
    assert_eq!(patterns.len(), DEFAULT_EXCLUDE_PATTERNS.len() + 1);
}

#[test]
fn test_effective_patterns_deduplication() {
    let config = ExcludeConfig {
        paths: vec!["**/vendor/**".to_string()],
        skip_defaults: false,
    };
    let patterns = config.effective_patterns();
    let vendor_count = patterns.iter().filter(|p| *p == "**/vendor/**").count();
    assert_eq!(vendor_count, 1);
}

#[test]
fn test_should_exclude_vendor_by_default() {
    let config = ProjectConfig::default();
    // Relative paths
    assert!(config.should_exclude(std::path::Path::new("src/vendor/jquery.js")));
    assert!(config.should_exclude(std::path::Path::new("node_modules/react/index.js")));
    assert!(config.should_exclude(std::path::Path::new("deep/path/dist/bundle.js")));
    assert!(config.should_exclude(std::path::Path::new("assets/lib.min.js")));
    assert!(config.should_exclude(std::path::Path::new("css/styles.min.css")));
    assert!(config.should_exclude(std::path::Path::new("js/app.bundle.js")));
    assert!(!config.should_exclude(std::path::Path::new("src/main.py")));
    // Absolute paths (as returned by affected_files in findings)
    assert!(config.should_exclude(std::path::Path::new(
        "/tmp/django/django/contrib/admin/static/admin/js/vendor/jquery/jquery.js"
    )));
    assert!(config.should_exclude(std::path::Path::new(
        "/tmp/project/node_modules/react/index.js"
    )));
    assert!(config.should_exclude(std::path::Path::new(
        "/home/user/project/assets/app.min.js"
    )));
}

#[test]
fn test_default_project_type() {
    let pt = ProjectType::default();
    assert_eq!(pt, ProjectType::Web);
}

#[test]
fn test_default_exclude_patterns_populated() {
    assert!(!DEFAULT_EXCLUDE_PATTERNS.is_empty());
    assert!(DEFAULT_EXCLUDE_PATTERNS.contains(&"**/node_modules/**"));
    assert!(DEFAULT_EXCLUDE_PATTERNS.contains(&"**/vendor/**"));
    assert!(DEFAULT_EXCLUDE_PATTERNS.contains(&"**/dist/**"));
    assert!(DEFAULT_EXCLUDE_PATTERNS.contains(&"**/*.min.js"));
}

#[test]
fn test_project_config_toml_with_project_type() {
    let toml_str = r#"
project_type = "library"

[scoring]
security_multiplier = 3.0

[exclude]
paths = ["generated/"]
"#;
    let config: ProjectConfig = toml::from_str(toml_str).expect("parse scoring config");
    assert_eq!(config.project_type, Some(ProjectType::Library));
    assert!((config.scoring.security_multiplier - 3.0).abs() < 0.001);
    assert_eq!(config.exclude.paths, vec!["generated/"]);
}

#[test]
fn test_project_config_all_project_types_parse() {
    for (type_str, expected) in [
        ("web", ProjectType::Web),
        ("interpreter", ProjectType::Interpreter),
        ("compiler", ProjectType::Compiler),
        ("library", ProjectType::Library),
        ("framework", ProjectType::Framework),
        ("cli", ProjectType::Cli),
        ("kernel", ProjectType::Kernel),
        ("game", ProjectType::Game),
        ("datascience", ProjectType::DataScience),
        ("mobile", ProjectType::Mobile),
    ] {
        let toml_str = format!("project_type = \"{}\"", type_str);
        let config: ProjectConfig = toml::from_str(&toml_str).expect("parse project type config");
        assert_eq!(
            config.project_type,
            Some(expected),
            "Failed for project_type = \"{}\"",
            type_str
        );
    }
}

#[test]
fn test_unknown_project_type_is_error() {
    let toml_str = r#"project_type = "unknown_type""#;
    let result = toml::from_str::<ProjectConfig>(toml_str);
    assert!(result.is_err());
}

#[test]
fn test_coupling_multiplier_varies_by_type() {
    // Web (default) should be the strictest at 1.0
    assert!((ProjectType::Web.coupling_multiplier() - 1.0).abs() < 0.001);
    // Compiler and Kernel should be lenient
    assert!(ProjectType::Compiler.coupling_multiplier() > 2.0);
    assert!(ProjectType::Kernel.coupling_multiplier() > 2.0);
}

#[test]
fn test_lenient_dead_code() {
    assert!(ProjectType::Interpreter.lenient_dead_code());
    assert!(ProjectType::Kernel.lenient_dead_code());
    assert!(ProjectType::Game.lenient_dead_code());
    assert!(ProjectType::Framework.lenient_dead_code());
    assert!(ProjectType::DataScience.lenient_dead_code());
    // Non-lenient types
    assert!(!ProjectType::Web.lenient_dead_code());
    assert!(!ProjectType::Library.lenient_dead_code());
    assert!(!ProjectType::Cli.lenient_dead_code());
    assert!(!ProjectType::Compiler.lenient_dead_code());
    assert!(!ProjectType::Mobile.lenient_dead_code());
}

#[test]
fn test_disabled_detectors() {
    let toml_str = r#"
[detectors.god-class]
enabled = false

[detectors.sql-injection]
enabled = true

[defaults]
skip_detectors = ["debug-code"]
"#;
    let config: ProjectConfig = toml::from_str(toml_str).expect("parse disabled detectors config");
    let disabled = config.disabled_detectors();
    assert!(disabled.contains(&"god-class".to_string()));
    assert!(disabled.contains(&"debug-code".to_string()));
    assert!(!disabled.contains(&"sql-injection".to_string()));
}

#[test]
fn test_cli_defaults_parsing() {
    let toml_str = r#"
[defaults]
format = "sarif"
severity = "high"
workers = 16
per_page = 50
thorough = true
no_git = false
no_emoji = true
fail_on = "medium"
skip_detectors = ["dead-code", "unused-import"]
"#;
    let config: ProjectConfig = toml::from_str(toml_str).expect("parse CLI defaults config");
    assert_eq!(config.defaults.format, Some("sarif".to_string()));
    assert_eq!(config.defaults.severity, Some("high".to_string()));
    assert_eq!(config.defaults.workers, Some(16));
    assert_eq!(config.defaults.per_page, Some(50));
    assert_eq!(config.defaults.thorough, Some(true));
    assert_eq!(config.defaults.no_git, Some(false));
    assert_eq!(config.defaults.no_emoji, Some(true));
    assert_eq!(config.defaults.fail_on, Some("medium".to_string()));
    assert_eq!(config.defaults.skip_detectors.len(), 2);
}
