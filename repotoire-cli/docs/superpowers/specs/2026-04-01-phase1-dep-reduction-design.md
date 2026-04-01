# Phase 1: Dependency Reduction — toml + ureq

## Context

Repotoire's transitive dependency count sits at 198 (down from 327). The goal is sub-100. This is Phase 1 of a multi-phase effort, targeting two medium-size dependency trees that can be replaced without architectural changes.

## Change 1: toml (21 transitive) → basic-toml (1 transitive)

### What

Replace the `toml` crate with `basic-toml` (by dtolnay). basic-toml provides `from_str()` and `to_string()` backed by serde — the exact subset repotoire uses.

### Why

The `toml` crate pulls `toml_edit` + `winnow` parser combinator framework for features we don't use (TOML editing, comment preservation, span tracking). We only deserialize.

### Affected files

| File | Usage | Change |
|------|-------|--------|
| `src/config/user_config.rs` | `toml::from_str::<UserConfig>()` | `basic_toml::from_str()` |
| `src/config/project_config/mod.rs` | `toml::from_str::<ProjectConfig>()` | `basic_toml::from_str()` |
| `src/config/project_config/tests.rs` | `toml::from_str()` (6 test calls) | `basic_toml::from_str()` |
| `src/detectors/framework_detection/mod.rs` | `toml::from_str::<PyProjectToml>()`, `toml::from_str::<CargoToml>()` | `basic_toml::from_str()` |

### Cargo.toml change

```diff
- toml = "0.8"
+ basic-toml = "0.1"
```

### Risk

Low. Near drop-in API. No `to_string_pretty()` used in production.

### Expected savings

~16 unique transitive deps removed (winnow, toml_edit, toml_datetime, etc.).

---

## Change 2: ureq (41 transitive) → vendored HTTP client over rustls

### What

Replace ureq with a minimal hand-rolled HTTP/1.1 client in `src/http.rs` (~350 lines). Supports both `http://` (plain TCP) and `https://` (TLS via rustls). Uses `std::net::TcpStream` for transport.

### Why

ureq brings 41 transitive deps including httparse, http, ureq-proto, percent-encoding, base64, flate2, miniz_oxide, and the entire compression stack. We make exactly 4 types of HTTP requests (POST JSON to 3 APIs, GET JSON from 1 CDN). A purpose-built client for these 4 patterns is ~350 lines.

### HTTP client design (`src/http.rs`)

```rust
pub struct HttpResponse {
    pub status: u16,
    pub body: String,
}

pub fn get(url: &str, timeout: Duration) -> Result<HttpResponse>;
pub fn post_json(url: &str, headers: &[(&str, &str)], body: &str, timeout: Duration) -> Result<HttpResponse>;
```

Internals:
- Parse URL into (scheme, host, port, path) — simple string splitting, no url crate
- DNS resolve via `std::net::ToSocketAddrs`
- Branch on scheme: `https` wraps TcpStream in `rustls::StreamOwned`, `http` uses TcpStream directly (needed for Ollama at `http://localhost:11434`)
- Send `Accept-Encoding: identity` to prevent compressed responses (we removed flate2)
- Send `Connection: close` to simplify response reading
- Write raw HTTP/1.1 request: `POST /path HTTP/1.1\r\nHost: ...\r\nContent-Length: ...\r\n\r\nbody`
- Read response: parse status line, collect headers, read body via Content-Length or chunked transfer encoding
- Chunked decoding: read hex chunk size, read that many bytes, repeat until `0\r\n`
- Timeout via `TcpStream::set_read_timeout` / `set_write_timeout` (per-operation, not total — documented limitation)

No connection pooling (each request opens a new connection). Acceptable because:
- AI API calls: 1-2 per session, 120s timeout dominates
- Telemetry: 1 fire-and-forget POST per session
- OSV.dev: 1-5 batch POSTs per analysis
- Benchmarks: 1-3 GETs with fallback chain

### Affected files

| File | Current ureq usage | New http.rs usage | serde_json changes |
|------|-------------------|-------------------|--------------------|
| `src/ai/client.rs` | `agent.post(url).send_json(&body)`, `response.read_json()` | `http::post_json(url, &headers, &serde_json::to_string(&body)?, timeout)` | Add `serde_json::to_string()` for request, `serde_json::from_str()` for response |
| `src/telemetry/posthog.rs` | `agent.post(url).send_json(&payload)` | `http::post_json(url, &headers, &serde_json::to_string(&payload)?, timeout)` | Add `serde_json::to_string()` for request |
| `src/telemetry/benchmarks.rs` | `agent.get(url).call()`, `response.read_json()` | `http::get(url, timeout)`, `serde_json::from_str(&response.body)` | Add `serde_json::from_str()` for response |
| `src/detectors/security/dep_audit.rs` | `agent.post(url).send_json(&query)`, manual `serde_json::from_str()` | `http::post_json(url, &headers, &serde_json::to_string(&query)?, timeout)` | Add `serde_json::to_string()` for request (response parsing already manual) |

**Do not touch:** `src/detectors/detector_context.rs` line 150 contains `"ureq"` as a string literal for detecting analyzed code's dependencies — not a dependency usage.

**Comment-only updates:** `src/ai/verify.rs` and `src/cli/fix.rs` reference "ureq" in comments.

### Cargo.toml change

```diff
- ureq = "3"
+ rustls = { version = "0.23", default-features = false, features = ["std", "ring", "logging"] }
+ webpki-roots = "1"
```

(`rustls` and `webpki-roots` are already transitive deps of ureq, so this just makes them direct.)

### Risk

Medium. Hand-rolled HTTP parsing must handle:
- Chunked transfer encoding (Anthropic and OpenAI APIs use it)
- Content-Length response reading
- Plain HTTP for localhost (Ollama)
- TLS handshake errors (certificate validation via webpki-roots)
- Timeout enforcement (per-operation via TcpStream, not total request timeout — slow-drip responses could exceed expected duration)

Not needed:
- Redirect following (our endpoints don't redirect)
- Compression (we send `Accept-Encoding: identity`)

### Expected savings

~15 unique transitive deps removed (ureq, ureq-proto, httparse, http, percent-encoding, base64, flate2, miniz_oxide, adler2, simd-adler32, crc32fast, bytes, utf-8, zmij).

---

## Combined impact

198 → ~167 transitive deps (~31 removed).

## Verification

1. `cargo check` — clean compile
2. `cargo test --lib` — all tests pass
3. `cargo clippy -- -D warnings` — clean
4. `cargo tree -e normal --prefix none | sort -u | wc -l` — target ~167
5. Manual test: `cargo run -- analyze .` (exercises config loading via basic-toml)
6. Manual test: `RUST_LOG=debug cargo run -- analyze . --all-detectors` (exercises dep_audit HTTP to OSV.dev)
7. Manual test: `cargo run -- benchmark` (exercises GET to benchmark CDN)
