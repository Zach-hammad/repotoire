//! PostHog event capture — fire-and-forget background sends

use serde_json::{json, Value};
use std::time::Duration;

pub const POSTHOG_CAPTURE_URL: &str = "https://app.posthog.com/capture/";
pub const POSTHOG_API_KEY: &str = "phc_REPLACE_WITH_REAL_KEY";

/// Build the JSON payload expected by the PostHog `/capture/` endpoint.
pub fn build_capture_payload(
    api_key: &str,
    event: &str,
    distinct_id: &str,
    mut properties: Value,
) -> Value {
    // Disable GeoIP enrichment so PostHog never stores IP-derived location data
    if let Some(obj) = properties.as_object_mut() {
        obj.insert("$geoip_disable".to_string(), serde_json::Value::Bool(true));
    }
    json!({
        "api_key": api_key,
        "event": event,
        "distinct_id": distinct_id,
        "properties": properties,
    })
}

/// Spawn a background thread and POST the event to PostHog.
///
/// This is intentionally fire-and-forget: errors are silently discarded so
/// that telemetry never blocks or crashes the CLI.
pub fn send_event_background(
    url: &str,
    api_key: &str,
    event: &str,
    distinct_id: &str,
    properties: Value,
) {
    let payload = build_capture_payload(api_key, event, distinct_id, properties);
    let url = url.to_string();

    std::thread::spawn(move || {
        let agent = ureq::config::Config::builder()
            .timeout_global(Some(Duration::from_secs(10)))
            .build()
            .new_agent();

        // Ignore all errors — telemetry must never impact CLI behaviour
        let _ = agent
            .post(&url)
            .header("Content-Type", "application/json")
            .send_json(payload);
    });
}

/// Convenience wrapper that uses the compiled-in defaults.
pub fn capture(event: &str, distinct_id: &str, properties: Value) {
    send_event_background(
        POSTHOG_CAPTURE_URL,
        POSTHOG_API_KEY,
        event,
        distinct_id,
        properties,
    );
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn test_build_capture_payload() {
        let props = json!({ "score": 87.5, "grade": "B+" });
        let payload = build_capture_payload("api-key-123", "analysis_complete", "user-abc", props);

        assert_eq!(payload["api_key"], "api-key-123");
        assert_eq!(payload["event"], "analysis_complete");
        assert_eq!(payload["distinct_id"], "user-abc");
        assert_eq!(payload["properties"]["score"], 87.5);
        assert_eq!(payload["properties"]["grade"], "B+");
        assert_eq!(payload["properties"]["$geoip_disable"], true);
    }

    #[test]
    fn test_capture_does_not_block() {
        // Use an invalid URL so the request will fail quickly (or time out in the bg thread).
        // The important thing is that send_event_background returns immediately.
        let start = std::time::Instant::now();
        send_event_background(
            "http://127.0.0.1:1", // nothing listening on port 1
            "phc_test",
            "test_event",
            "test-user",
            json!({ "test": true }),
        );
        let elapsed = start.elapsed();

        // Should return in well under 100 ms — the network call happens in the bg thread
        assert!(
            elapsed.as_millis() < 100,
            "send_event_background blocked for {}ms",
            elapsed.as_millis()
        );
    }
}
