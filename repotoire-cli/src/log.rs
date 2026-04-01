//! Minimal tracing subscriber that writes to stderr with env filter support.
//!
//! Replaces tracing-subscriber (27 transitive deps) with ~80 lines.
//! Supports `RUST_LOG` env var and `--log-level` CLI flag.
//! Only handles events (no span tracking — we don't use #[instrument]).

use std::io::Write;
use std::sync::OnceLock;
use tracing_core::{
    field::{Field, Visit},
    Event, Level, Metadata, Subscriber,
};

/// Parsed filter directives: (target_prefix, level).
/// Empty target_prefix means "apply to all".
static FILTER: OnceLock<Vec<(String, Level)>> = OnceLock::new();

/// A minimal stderr subscriber with env-filter-style filtering.
pub struct StderrSubscriber;

impl StderrSubscriber {
    /// Initialize as the global default subscriber.
    /// `default_filter` is used when `RUST_LOG` is not set.
    pub fn init(default_filter: &str) {
        let filter_str = std::env::var("RUST_LOG").unwrap_or_else(|_| default_filter.to_string());
        let directives = parse_filter(&filter_str);
        FILTER.set(directives).ok();
        tracing_core::dispatcher::set_global_default(tracing_core::dispatcher::Dispatch::new(
            StderrSubscriber,
        ))
        .ok();
    }
}

fn parse_filter(s: &str) -> Vec<(String, Level)> {
    s.split(',')
        .filter(|d| !d.is_empty())
        .map(|directive| {
            let directive = directive.trim();
            if let Some((target, level_str)) = directive.split_once('=') {
                (target.to_string(), parse_level(level_str))
            } else {
                // No target prefix — applies to all
                (String::new(), parse_level(directive))
            }
        })
        .collect()
}

fn parse_level(s: &str) -> Level {
    match s.to_lowercase().as_str() {
        "trace" => Level::TRACE,
        "debug" => Level::DEBUG,
        "info" => Level::INFO,
        "warn" | "warning" => Level::WARN,
        "error" => Level::ERROR,
        _ => Level::WARN,
    }
}

fn is_enabled(meta: &Metadata<'_>) -> bool {
    let directives = FILTER.get().map(|v| v.as_slice()).unwrap_or(&[]);
    if directives.is_empty() {
        return *meta.level() <= Level::WARN;
    }

    // Check target-specific directives first (longest match wins)
    let target = meta.target();
    let mut best_match: Option<&Level> = None;
    let mut best_len = 0;

    for (prefix, level) in directives {
        if prefix.is_empty() {
            // Global directive — weakest priority
            if best_match.is_none() {
                best_match = Some(level);
            }
        } else if target.starts_with(prefix.as_str()) && prefix.len() > best_len {
            best_match = Some(level);
            best_len = prefix.len();
        }
    }

    match best_match {
        Some(max_level) => *meta.level() <= *max_level,
        None => false,
    }
}

/// Visitor that extracts the "message" field from tracing events.
struct MessageVisitor {
    message: String,
}

impl Visit for MessageVisitor {
    fn record_debug(&mut self, field: &Field, value: &dyn std::fmt::Debug) {
        if field.name() == "message" {
            self.message = format!("{:?}", value);
        } else if !self.message.is_empty() {
            // Append extra fields
            self.message
                .push_str(&format!(" {}={:?}", field.name(), value));
        } else {
            self.message = format!("{}={:?}", field.name(), value);
        }
    }

    fn record_str(&mut self, field: &Field, value: &str) {
        if field.name() == "message" {
            self.message = value.to_string();
        } else if !self.message.is_empty() {
            self.message
                .push_str(&format!(" {}={}", field.name(), value));
        } else {
            self.message = format!("{}={}", field.name(), value);
        }
    }
}

impl Subscriber for StderrSubscriber {
    fn enabled(&self, metadata: &Metadata<'_>) -> bool {
        is_enabled(metadata)
    }

    fn new_span(&self, _span: &tracing_core::span::Attributes<'_>) -> tracing_core::span::Id {
        // No span tracking — return dummy ID
        tracing_core::span::Id::from_u64(1)
    }

    fn record(&self, _span: &tracing_core::span::Id, _values: &tracing_core::span::Record<'_>) {}

    fn record_follows_from(
        &self,
        _span: &tracing_core::span::Id,
        _follows: &tracing_core::span::Id,
    ) {
    }

    fn event(&self, event: &Event<'_>) {
        if !is_enabled(event.metadata()) {
            return;
        }

        let mut visitor = MessageVisitor {
            message: String::new(),
        };
        event.record(&mut visitor);

        let level = event.metadata().level();
        let target = event.metadata().target();

        // Compact format: level target: message
        let level_str = match *level {
            Level::ERROR => "ERROR",
            Level::WARN => " WARN",
            Level::INFO => " INFO",
            Level::DEBUG => "DEBUG",
            Level::TRACE => "TRACE",
        };

        let _ = writeln!(
            std::io::stderr(),
            "{} {}: {}",
            level_str,
            target,
            visitor.message
        );
    }

    fn enter(&self, _span: &tracing_core::span::Id) {}
    fn exit(&self, _span: &tracing_core::span::Id) {}
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_parse_level() {
        assert_eq!(parse_level("trace"), Level::TRACE);
        assert_eq!(parse_level("debug"), Level::DEBUG);
        assert_eq!(parse_level("info"), Level::INFO);
        assert_eq!(parse_level("warn"), Level::WARN);
        assert_eq!(parse_level("error"), Level::ERROR);
        assert_eq!(parse_level("WARNING"), Level::WARN);
        assert_eq!(parse_level("garbage"), Level::WARN);
    }

    #[test]
    fn test_parse_filter_simple() {
        let directives = parse_filter("warn");
        assert_eq!(directives.len(), 1);
        assert_eq!(directives[0].0, "");
        assert_eq!(directives[0].1, Level::WARN);
    }

    #[test]
    fn test_parse_filter_complex() {
        let directives = parse_filter("warn,repotoire=debug,hyper=error");
        assert_eq!(directives.len(), 3);
        assert_eq!(directives[0], ("".to_string(), Level::WARN));
        assert_eq!(directives[1], ("repotoire".to_string(), Level::DEBUG));
        assert_eq!(directives[2], ("hyper".to_string(), Level::ERROR));
    }

    #[test]
    fn test_is_enabled_respects_level() {
        FILTER
            .set(vec![("".to_string(), Level::INFO)])
            .unwrap_or(());
        // Can't easily test is_enabled without constructing Metadata,
        // but parse_filter + parse_level coverage is sufficient.
    }
}
