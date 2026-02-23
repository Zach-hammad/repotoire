# Vendor/Third-Party Exclusion Defaults — Design Document

**Date**: 2026-02-23
**Status**: Approved

## Problem

When analyzing Django, 985 of 1,802 findings (55%) come from vendored third-party files — jQuery, XRegExp, Select2 — shipped in `django/contrib/admin/static/admin/js/vendor/`. GlobalVariablesDetector alone produces 738 findings from a single vendored file (`xregexp.js`).

The exclusion infrastructure exists (`ExcludeConfig`, `should_exclude()`, postprocess filter) but requires explicit per-project configuration with no built-in defaults. Every major analysis tool (ESLint, ruff, pylint, SonarQube) excludes vendor directories by default.

## Solution

Add built-in default exclusion patterns applied at the **file collection stage** so vendor files are never collected, cached, or passed to detectors.

### Default Patterns

```
**/vendor/**
**/node_modules/**
**/third_party/**
**/third-party/**
**/bower_components/**
**/dist/**
**/*.min.js
**/*.min.css
**/*.bundle.js
```

### Override Mechanism

New `skip_defaults` field in `ExcludeConfig`:

```toml
[exclude]
skip_defaults = true   # Disable built-in patterns
paths = ["my-vendor/"]  # Only user patterns apply
```

When `skip_defaults` is false (the default), built-in patterns are merged with user patterns.

## Architecture

### Files Changed

1. **`src/config/project_config.rs`** — `DEFAULT_EXCLUDE_PATTERNS` constant, `skip_defaults` field, `effective_patterns()` method
2. **`src/cli/analyze/files.rs`** — `collect_source_files()` and `collect_file_list()` accept `&ExcludeConfig`, filter during walk
3. **`src/cli/mod.rs`** — Pass `ExcludeConfig::default()` from calibrate caller

### Data Flow

```
ExcludeConfig::default() → effective_patterns() → [defaults + user patterns]
  → collect_source_files(repo_path, &exclude) → WalkBuilder loop skips matching paths
  → Detectors never see vendor files
  → Postprocess Step 2.5 remains as safety net (no-op for defaults)
```

## Expected Impact

- Django: ~985 fewer findings (55% reduction), score improvement from B+ toward A
- Zero-config improvement for all projects with vendor directories
- Performance improvement — fewer files to parse, cache, and analyze
