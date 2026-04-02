# Phase 1 Dependency Reduction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce transitive dependencies from 198 to ~181 by replacing `toml` with `basic-toml` and `ureq` with a hand-rolled HTTP/1.1 client over rustls.

**Architecture:** Two independent changes. (1) Swap `toml::from_str` → `basic_toml::from_str` across 16 call sites — near drop-in. (2) Create `src/http.rs` (~400 lines) providing `get()` and `post_json()` over `TcpStream` + rustls, replacing 4 ureq agent patterns.

**Tech Stack:** Rust, rustls 0.23 (TLS), webpki-roots (CA certs), basic-toml (TOML deser), serde_json (already a dep)

**Spec:** `docs/superpowers/specs/2026-04-01-phase1-dep-reduction-design.md`

---

## File Structure

| File | Action | Responsibility |
|------|--------|---------------|
| `Cargo.toml` | Modify | Remove `toml`, `ureq`. Add `basic-toml`, `rustls`, `webpki-roots` |
| `src/http.rs` | Create | Minimal HTTP/1.1 client: URL parsing, TCP connect, TLS, request writing, response reading (Content-Length + chunked + EOF), timeouts |
| `src/lib.rs` | Modify | Add `pub mod http;` |
| `src/config/user_config.rs` | Modify | `toml::` → `basic_toml::` (1 prod + 6 test) |
| `src/config/project_config/mod.rs` | Modify | `toml::` → `basic_toml::` (1 call) |
| `src/config/project_config/tests.rs` | Modify | `toml::` → `basic_toml::` (6 calls) |
| `src/detectors/framework_detection/mod.rs` | Modify | `toml::` → `basic_toml::` (2 calls) |
| `src/ai/client.rs` | Modify | Replace ureq Agent with `crate::http::post_json()` |
| `src/telemetry/posthog.rs` | Modify | Replace ureq with `crate::http::post_json()` |
| `src/telemetry/benchmarks.rs` | Modify | Replace ureq with `crate::http::get()` |
| `src/detectors/security/dep_audit.rs` | Modify | Replace ureq with `crate::http::post_json()` |

---

### Task 1: Replace toml with basic-toml

**Files:**
- Modify: `Cargo.toml:73`
- Modify: `src/config/user_config.rs:59,206,226,236,244,305,312`
- Modify: `src/config/project_config/mod.rs:561`
- Modify: `src/config/project_config/tests.rs:118,241,262,275,315,336`
- Modify: `src/detectors/framework_detection/mod.rs:602,669`

- [ ] **Step 1: Swap dep in Cargo.toml**

Replace line 73:
```diff
- toml = "0.8"
+ basic-toml = "0.1"
```

- [ ] **Step 2: Find-replace all toml:: → basic_toml:: in source**

In these 4 files, replace every `toml::from_str` with `basic_toml::from_str`:
- `src/config/user_config.rs` (7 occurrences)
- `src/config/project_config/mod.rs` (1 occurrence)
- `src/config/project_config/tests.rs` (6 occurrences)
- `src/detectors/framework_detection/mod.rs` (2 occurrences)

- [ ] **Step 3: Build and test**

Run: `cargo check`
Expected: Clean compile.

Run: `cargo test config -- --nocapture`
Expected: All config tests pass.

Run: `cargo test framework_detection -- --nocapture`
Expected: Framework detection tests pass.

- [ ] **Step 4: Commit**

```bash
git add Cargo.toml src/config/ src/detectors/framework_detection/
git commit -m "chore(deps): replace toml with basic-toml

All 16 call sites are toml::from_str() — pure deserialization. basic-toml
(by dtolnay) provides the same API backed by serde. Removes toml_edit,
winnow, toml_datetime, serde_spanned, toml_write (~6 transitive deps)."
```

---

### Task 2: Create HTTP client — URL parsing + TCP connect

**Files:**
- Create: `src/http.rs`
- Modify: `src/lib.rs`

- [ ] **Step 1: Create src/http.rs with URL parser and TCP connection**

```rust
//! Minimal HTTP/1.1 client over TcpStream + rustls.
//! Replaces ureq (~41 transitive deps) for 4 call sites.

use std::io::{self, Read, Write};
use std::net::TcpStream;
use std::sync::Arc;
use std::time::Duration;

use anyhow::{bail, Context, Result};

/// Parsed URL components.
struct Url<'a> {
    scheme: &'a str,  // "http" or "https"
    host: &'a str,
    port: u16,
    path: &'a str,    // includes leading /
}

fn parse_url(url: &str) -> Result<Url<'_>> {
    let (scheme, rest) = url.split_once("://")
        .context("URL must start with http:// or https://")?;
    let (authority, path) = match rest.find('/') {
        Some(idx) => (&rest[..idx], &rest[idx..]),  // path includes leading /
        None => (rest, "/"),
    };
    let (host, port) = if let Some((h, p)) = authority.split_once(':') {
        (h, p.parse::<u16>().context("invalid port")?)
    } else {
        let default_port = if scheme == "https" { 443 } else { 80 };
        (authority, default_port)
    };
    Ok(Url { scheme, host, port, path })
}
```

- [ ] **Step 2: Add TLS connection setup with lazy ClientConfig**

```rust
use std::sync::OnceLock;

static TLS_CONFIG: OnceLock<Arc<rustls::ClientConfig>> = OnceLock::new();

fn tls_config() -> Arc<rustls::ClientConfig> {
    TLS_CONFIG.get_or_init(|| {
        let root_store = rustls::RootCertStore::from_iter(
            webpki_roots::TLS_SERVER_ROOTS.iter().cloned()
        );
        Arc::new(
            rustls::ClientConfig::builder()
                .with_root_certificates(root_store)
                .with_no_client_auth()
        )
    }).clone()
}

/// Open a TCP connection, optionally wrapped in TLS.
fn connect(url: &Url, timeout: Duration) -> Result<Box<dyn ReadWrite>> {
    let addr = format!("{}:{}", url.host, url.port);
    let stream = TcpStream::connect(&addr)
        .with_context(|| format!("failed to connect to {}", addr))?;
    stream.set_read_timeout(Some(timeout))?;
    stream.set_write_timeout(Some(timeout))?;

    if url.scheme == "https" {
        let server_name = rustls::pki_types::ServerName::try_from(url.host.to_string())
            .map_err(|e| anyhow::anyhow!("invalid server name: {}", e))?;
        let conn = rustls::ClientConnection::new(tls_config(), server_name)
            .context("TLS handshake failed")?;
        Ok(Box::new(rustls::StreamOwned::new(conn, stream)))
    } else {
        Ok(Box::new(stream))
    }
}

trait ReadWrite: Read + Write {}
impl<T: Read + Write> ReadWrite for T {}
```

- [ ] **Step 3: Register module in lib.rs**

Add `pub mod http;` to `src/lib.rs`.

- [ ] **Step 4: Build check**

Run: `cargo check`
Expected: Compiles (no callers yet).

- [ ] **Step 5: Commit**

```bash
git add src/http.rs src/lib.rs
git commit -m "feat(http): add minimal HTTP client — URL parsing + TCP/TLS connect"
```

---

### Task 3: HTTP client — request writing + response reading

**Files:**
- Modify: `src/http.rs`

- [ ] **Step 1: Add response struct and response reader**

```rust
pub struct HttpResponse {
    pub status: u16,
    pub body: String,
}

/// Read HTTP response: status line, headers, body.
fn read_response(stream: &mut dyn ReadWrite) -> Result<HttpResponse> {
    let mut buf = Vec::with_capacity(4096);
    let mut byte = [0u8; 1];

    // Read until \r\n\r\n (end of headers)
    loop {
        stream.read_exact(&mut byte)?;
        buf.push(byte[0]);
        if buf.ends_with(b"\r\n\r\n") { break; }
        if buf.len() > 65536 { bail!("headers too large"); }
    }

    let header_str = String::from_utf8_lossy(&buf);
    let mut lines = header_str.lines();

    // Parse status line: "HTTP/1.1 200 OK"
    let status_line = lines.next().context("empty response")?;
    let status = status_line.split_whitespace().nth(1)
        .context("missing status code")?
        .parse::<u16>().context("invalid status code")?;

    // Parse headers (case-insensitive)
    let mut content_length: Option<usize> = None;
    let mut chunked = false;
    for line in lines {
        if line.is_empty() { break; }
        if let Some((name, value)) = line.split_once(':') {
            let name = name.trim().to_ascii_lowercase();
            let value = value.trim();
            match name.as_str() {
                "content-length" => content_length = value.parse().ok(),
                "transfer-encoding" => chunked = value.to_ascii_lowercase().contains("chunked"),
                _ => {}
            }
        }
    }

    // Read body
    let body = if chunked {
        read_chunked(stream)?
    } else if let Some(len) = content_length {
        let mut body = vec![0u8; len];
        stream.read_exact(&mut body)?;
        String::from_utf8_lossy(&body).into_owned()
    } else {
        // No Content-Length, no chunked — read to EOF
        let mut body = Vec::new();
        stream.read_to_end(&mut body)?;
        String::from_utf8_lossy(&body).into_owned()
    };

    Ok(HttpResponse { status, body })
}

fn read_chunked(stream: &mut dyn ReadWrite) -> Result<String> {
    let mut body = Vec::new();
    loop {
        // Read chunk size line
        let mut size_line = Vec::new();
        let mut byte = [0u8; 1];
        loop {
            stream.read_exact(&mut byte)?;
            if byte[0] == b'\n' { break; }
            if byte[0] != b'\r' { size_line.push(byte[0]); }
        }
        let size = usize::from_str_radix(
            std::str::from_utf8(&size_line)?.trim(),
            16
        ).context("invalid chunk size")?;
        if size == 0 { break; }

        // Read chunk data
        let mut chunk = vec![0u8; size];
        stream.read_exact(&mut chunk)?;
        body.extend_from_slice(&chunk);

        // Read trailing \r\n
        let mut crlf = [0u8; 2];
        stream.read_exact(&mut crlf)?;
    }
    // Read trailing headers (just discard until \r\n)
    let mut byte = [0u8; 1];
    loop {
        stream.read_exact(&mut byte)?;
        if byte[0] == b'\n' { break; }
    }
    Ok(String::from_utf8_lossy(&body).into_owned())
}
```

- [ ] **Step 2: Add GET and POST functions**

```rust
/// HTTP GET request.
pub fn get(url: &str, timeout: Duration) -> Result<HttpResponse> {
    let parsed = parse_url(url)?;
    let mut stream = connect(&parsed, timeout)?;

    write!(stream,
        "GET {} HTTP/1.1\r\nHost: {}\r\nAccept-Encoding: identity\r\nConnection: close\r\n\r\n",
        parsed.path, parsed.host
    )?;
    stream.flush()?;

    read_response(&mut *stream)
}

/// HTTP POST with JSON body and custom headers.
pub fn post_json(
    url: &str,
    headers: &[(&str, &str)],
    body: &str,
    timeout: Duration,
) -> Result<HttpResponse> {
    let parsed = parse_url(url)?;
    let mut stream = connect(&parsed, timeout)?;

    write!(stream,
        "POST {} HTTP/1.1\r\nHost: {}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nAccept-Encoding: identity\r\nConnection: close\r\n",
        parsed.path, parsed.host, body.len()
    )?;
    for (name, value) in headers {
        write!(stream, "{}: {}\r\n", name, value)?;
    }
    write!(stream, "\r\n{}", body)?;
    stream.flush()?;

    read_response(&mut *stream)
}
```

- [ ] **Step 3: Add inline tests**

```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_parse_url_https() {
        let u = parse_url("https://api.example.com/v1/endpoint").unwrap();
        assert_eq!(u.scheme, "https");
        assert_eq!(u.host, "api.example.com");
        assert_eq!(u.port, 443);
        assert_eq!(u.path, "/v1/endpoint");
    }

    #[test]
    fn test_parse_url_http_with_port() {
        let u = parse_url("http://localhost:11434/v1/chat").unwrap();
        assert_eq!(u.scheme, "http");
        assert_eq!(u.host, "localhost");
        assert_eq!(u.port, 11434);
        assert_eq!(u.path, "/v1/chat");
    }

    #[test]
    fn test_parse_url_no_path() {
        let u = parse_url("https://example.com").unwrap();
        assert_eq!(u.path, "/");
    }

    #[test]
    fn test_parse_url_invalid() {
        assert!(parse_url("not-a-url").is_err());
    }
}
```

- [ ] **Step 4: Build and test**

Run: `cargo test http::tests -- --nocapture`
Expected: URL parsing tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/http.rs
git commit -m "feat(http): add request writing + response reading (Content-Length, chunked, EOF)"
```

---

### Task 4: Replace ureq in ai/client.rs

**Files:**
- Modify: `src/ai/client.rs:143-152,244-268,293-315`

- [ ] **Step 1: Remove ureq Agent from AiClient struct**

Replace the `agent: ureq::Agent` field and `make_agent()` function with a `timeout: Duration` field.

In `AiClient::new()`, set `timeout: Duration::from_secs(120)`.

- [ ] **Step 2: Replace generate_openai() ureq calls**

Replace ureq agent calls with hand-rolled HTTP. Note: `self.api_key` is a `String`, and `self.config.backend.requires_api_key()` is a `bool` guard. `post_json()` already hardcodes `Content-Type: application/json`, so callers only pass extra headers.

For OpenAI-compatible endpoints:
```rust
let mut headers = Vec::new();
let auth_header;
if self.config.backend.requires_api_key() {
    auth_header = format!("Bearer {}", self.api_key);
    headers.push(("Authorization", auth_header.as_str()));
}
let body = serde_json::to_string(&request)?;
let response = crate::http::post_json(url, &headers, &body, self.timeout)?;
```

Then replace `response.into_body().read_json::<T>()` with `serde_json::from_str::<T>(&response.body)?`.
Replace `response.into_body().read_to_string()` with `response.body` (already a String).

- [ ] **Step 3: Replace generate_anthropic() with Anthropic-specific headers**

Anthropic uses `x-api-key` (NOT `Authorization: Bearer`) and requires `anthropic-version`:
```rust
let headers = vec![
    ("x-api-key", self.api_key.as_str()),
    ("anthropic-version", "2023-06-01"),
];
let body = serde_json::to_string(&request)?;
let response = crate::http::post_json(url, &headers, &body, self.timeout)?;
```

Then `serde_json::from_str::<AnthropicResponse>(&response.body)?` for the response.

- [ ] **Step 4: Build and test**

Run: `cargo check`
Expected: Clean compile.

- [ ] **Step 5: Commit**

```bash
git add src/ai/client.rs
git commit -m "refactor(ai): replace ureq with hand-rolled HTTP client"
```

---

### Task 5: Replace ureq in telemetry + dep_audit

**Files:**
- Modify: `src/telemetry/posthog.rs:43-52`
- Modify: `src/telemetry/benchmarks.rs:194-200`
- Modify: `src/detectors/security/dep_audit.rs:447-469`

- [ ] **Step 1: Replace posthog.rs**

Replace ureq agent + `send_json` with (no extra headers — `post_json` hardcodes Content-Type):
```rust
let body = match serde_json::to_string(&payload) {
    Ok(b) => b,
    Err(_) => return,  // fire-and-forget: silently skip on serialize error
};
let _ = crate::http::post_json(
    POSTHOG_CAPTURE_URL,
    &[],
    &body,
    Duration::from_secs(10),
);
```
Note: runs in a spawned thread — errors discarded with `let _ =`. No `?` operator (thread returns `()`).

- [ ] **Step 2: Replace benchmarks.rs**

Replace ureq agent + `get().call().read_json()` with:
```rust
let response = crate::http::get(url, Duration::from_secs(5)).ok()?;
if response.status >= 400 { return None; }  // fallback chain: try next URL on 404 etc.
serde_json::from_str(&response.body).ok()?
```
Important: explicitly check `response.status >= 400` since the raw client returns any status (ureq with `http_status_as_error` defaulting true would have auto-errored).

- [ ] **Step 3: Replace dep_audit.rs (preserve graceful degradation)**

Replace ureq agent + `send_json`. IMPORTANT: use `match`, not `?` — network errors must degrade gracefully (return no findings), not crash the analysis pipeline:
```rust
let body = match serde_json::to_string(&query) {
    Ok(b) => b,
    Err(e) => {
        tracing::warn!("Failed to serialize OSV query: {}", e);
        continue;
    }
};
let response = match crate::http::post_json(
    "https://api.osv.dev/v1/querybatch",
    &[],
    &body,
    Duration::from_secs(30),
) {
    Ok(r) => r,
    Err(e) => {
        tracing::warn!("OSV.dev API request failed (working offline): {}", e);
        continue;  // skip this chunk, try next
    }
};
```

- [ ] **Step 4: Build and test**

Run: `cargo check`
Expected: Clean compile.

Run: `cargo test telemetry -- --nocapture`
Expected: Telemetry tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/telemetry/ src/detectors/security/dep_audit.rs
git commit -m "refactor(telemetry,dep_audit): replace ureq with hand-rolled HTTP client"
```

---

### Task 6: Remove ureq from Cargo.toml, add rustls + webpki-roots, verify

**Files:**
- Modify: `Cargo.toml:76`

- [ ] **Step 1: Update Cargo.toml**

```diff
- ureq = { version = "3", features = ["json"] }
+ rustls = { version = "0.23", default-features = false, features = ["std", "ring", "logging"] }
+ webpki-roots = "1"
```

- [ ] **Step 2: Update comments referencing ureq**

In `src/ai/verify.rs` and `src/cli/fix.rs`, update any comments mentioning "ureq" to say "crate::http" or just remove the reference.

Do NOT touch `src/detectors/detector_context.rs` line 150 — that's a string literal detecting analyzed code's dependencies.

- [ ] **Step 3: Full verification**

Run: `cargo check` — clean compile
Run: `cargo test --lib` — all tests pass
Run: `cargo clippy -- -D warnings` — clean

- [ ] **Step 4: Count dependencies**

Run: `cargo tree -e normal --prefix none | sort -u | wc -l`
Expected: ~181 (down from 198)

- [ ] **Step 5: Commit and push**

```bash
git add -A
git commit -m "chore(deps): remove ureq, add rustls+webpki-roots as direct deps

Replaced ureq (41 transitive deps) with a hand-rolled HTTP/1.1 client
in src/http.rs (~400 lines). Supports both http:// (Ollama) and https://
(Anthropic, OpenAI, PostHog, OSV.dev). Handles Content-Length, chunked
transfer encoding, and read-to-EOF response modes.

Removes ~12 unique transitive dependencies."
git push origin main
```

---

### Task 7: Manual integration test

- [ ] **Step 1: Test config loading**

Run: `cargo run -- analyze .`
Expected: Analysis runs, config loaded via basic-toml (check no TOML parse errors).

- [ ] **Step 2: Test benchmark CDN**

Run: `cargo run -- benchmark`
Expected: Benchmark data fetched from CDN (exercises HTTPS GET).

- [ ] **Step 3: Test dep audit**

Run: `RUST_LOG=debug cargo run -- analyze . --all-detectors 2>&1 | grep -i "osv\|dep.audit\|vuln"`
Expected: OSV.dev API called, dependency audit results shown.

- [ ] **Step 4: Save memory**

```bash
# Record the achievement
```
