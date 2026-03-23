# LSP Server Design

*2026-03-23*

## Problem

Repotoire's analysis results are only accessible via the CLI. Developers run `repotoire analyze` or `repotoire watch` in a terminal, then context-switch to their editor to find and fix issues. There's no inline feedback — no squiggly underlines, no quick fixes, no ambient score awareness. This limits daily usage and makes repotoire a "run occasionally" tool instead of an always-on companion.

## Goal

Ship an LSP server (`repotoire lsp`) that brings findings inline into any editor. Diagnostics on save, code actions for suppression and fixes, hover for context, and a status notification for score tracking. Two-process architecture: an LSP frontend speaking the protocol, and a worker backend running the analysis engine.

## Non-Goals

- VS Code extension (separate project — this spec ships the server, not editor-specific glue)
- Graph navigation (go-to-callers, dependency chains — deferred to future spec)
- MCP server (removed from roadmap)
- Multi-workspace support (one workspace root per LSP instance)
- Live typing analysis (`textDocument/didChange`) — too expensive, analyze on `didSave` only
- Custom LSP settings protocol — reads `repotoire.toml` from workspace root

---

## Architecture

Two processes connected by JSONL on stdin/stdout:

```
Editor ←→ repotoire lsp (tower-lsp, tokio, stdio)
               ↕ stdin/stdout JSONL
           repotoire __worker (child process)
               ↕
           WatchEngine (analysis, file watching, deltas)
```

**`repotoire lsp`** — the LSP server. Speaks LSP protocol to the editor via stdio, speaks JSONL to the worker child process. Handles all LSP capabilities: diagnostics, code actions, hover, custom notifications. Manages the worker lifecycle (spawn, restart on crash). Runs an async event loop on tokio.

**`repotoire __worker`** — a hidden subcommand. Wraps `WatchEngine` with a JSONL command/event protocol on stdin/stdout. Receives commands ("init", "analyze"), emits events (deltas, progress, errors). Owns all analysis state. The `__` prefix signals it's internal — not user-facing. Still debuggable: `echo '{"cmd":"init","id":1,"path":"/my/repo","config":{}}' | repotoire __worker` works in a terminal.

**Future unification:** `repotoire watch` will also consume the worker process as a frontend, replacing its current direct use of `WatchEngine`. This is deferred from initial implementation — watch continues to work as-is while the LSP ships. Once the worker is battle-tested, watch migrates to it and gains progress reporting and proper debouncing for free.

### Why Two Processes

- **Process isolation.** A crash in analysis doesn't kill the LSP. The editor stays responsive, the LSP respawns the worker.
- **Clean protocol boundary.** The JSONL interface is testable, debuggable, and reusable by other frontends.
- **Future-proof.** The worker is the foundation for a cloud service — the same protocol works over TCP/HTTP when the time comes.

### Memory & Performance

- Two processes doesn't meaningfully increase memory. The LSP process is lightweight (~10MB RSS — protocol translation only). The worker holds analysis state (200-500MB for large repos). The binary's static memory is shared via OS page cache.
- JSONL serialization adds ~1-5ms per event. Negligible vs. analysis time.
- Process spawn: ~50ms one-time cost on startup.
- Initial analysis dominates startup time (1-10s depending on repo size). Subsequent analyses use incremental cache.

---

## Worker Protocol

JSONL on stdin (commands) and stdout (events). One JSON object per line. Worker stderr is for logging — stdout is exclusively protocol messages.

### Commands (LSP → Worker)

```json
{"cmd": "init", "id": 1, "path": "/home/user/project", "config": {"all_detectors": false, "workers": 8}}
{"cmd": "analyze", "id": 2, "files": ["/home/user/project/src/main.rs"]}
{"cmd": "analyze_all", "id": 3}
{"cmd": "shutdown", "id": 4}
```

- **`init`** — set up WatchEngine for the given repo. Must be the first command. Triggers initial cold analysis. The worker starts its own filesystem watcher for changes outside the editor.
- **`analyze`** — re-analyze after specific files changed. Maps to `WatchEngine::reanalyze()`.
- **`analyze_all`** — full re-analysis (e.g., after config change). Maps to `WatchEngine::initial_analyze()`.
- **`shutdown`** — save state and exit cleanly.

All commands have a monotonic `id` for response correlation.

### Events (Worker → LSP)

```json
{"event": "ready", "id": 1, "findings": [...], "score": 92.3, "grade": "A-", "elapsed_ms": 2050}
{"event": "progress", "id": 1, "stage": "parsing", "done": 120, "total": 456}
{"event": "delta", "id": 2, "new_findings": [...], "fixed_findings": [...], "score": 93.0, "grade": "A-", "score_delta": 0.7, "total_findings": 85, "elapsed_ms": 150}
{"event": "unchanged", "id": 2, "score": 93.0, "total_findings": 85, "elapsed_ms": 12}
{"event": "error", "id": 2, "message": "Analysis failed: ..."}
```

- **`ready`** — initial analysis complete. Contains full findings list for initial diagnostic push.
- **`progress`** — intermediate progress during analysis. Stages: `collecting`, `parsing`, `building_graph`, `detecting`, `scoring`. Only emitted for stages taking >100ms.
- **`delta`** — findings changed. `new_findings` and `fixed_findings` are full `Finding` objects serialized to JSON.
- **`unchanged`** — analysis ran but nothing changed. Score included for status update.
- **`error`** — analysis failed. Worker keeps running.

### Response Correlation

- Events with an `id` are responses to commands. The LSP correlates them.
- Events with `"id": null` are unsolicited — triggered by the filesystem watcher, not a command. The LSP processes these always.
- The LSP tracks `latest_request_id`. If a response arrives with `id < latest_request_id`, it's stale and discarded.

---

## LSP Capabilities

### Diagnostics (`textDocument/publishDiagnostics`)

On `ready` and `delta` events, the LSP maps findings to diagnostics and pushes them per-file.

**Field mapping:**

| Finding field | LSP Diagnostic field |
|---|---|
| `affected_files[0]` | document URI |
| `line_start`, `line_end` | range (default to `line_start..line_start+1` if no end) |
| `severity` | see table below |
| `title` | message |
| `detector` | source = `"repotoire"` |
| `id` | code |
| `category` | code description (if present) |

**Severity mapping:**

| Finding Severity | LSP DiagnosticSeverity |
|---|---|
| Critical | Error |
| High | Warning |
| Medium | Warning |
| Low | Information |
| Info | Hint |

**Edge cases:**
- **`line_start: None`** (e.g., graph-wide architectural findings) — default to line 0, column 0. The diagnostic appears at the top of the first affected file.
- **Multi-file findings** (e.g., circular dependencies) — diagnostic is placed on `affected_files[0]` only. Duplicating across all affected files is deferred.

**Diagnostic state management:** The LSP maintains a `HashMap<Url, Vec<Diagnostic>>` built from the latest full findings list. On `ready`, this map is populated from all findings. On `delta`, the map is patched: fixed findings are removed, new findings are added. Affected files are re-published. LSP replaces diagnostics per file (not incremental), so the full list for each affected file is sent.

### Code Actions (`textDocument/codeAction`)

Two types, offered when the cursor is on a diagnostic range:

1. **Suppress finding** — always available for any diagnostic. Inserts `// repotoire:ignore[detector-name]` on the line above the finding. The comment style matches the file's language (`#` for Python, `//` for Rust/JS/Go/Java, etc.).

2. **Apply suggested fix** — only available when `suggested_fix.is_some()`. Note: `Finding.suggested_fix` is a freeform `String` (description, not structured code). The code action shows the fix description as a comment inserted above the finding line. If the description looks like a code replacement (heuristic: contains no prose, just code), it can be inserted as a workspace edit. Otherwise, it's informational only — shown as a comment or in a `window/showMessage`.

### Hover (`textDocument/hover`)

When hovering over a diagnostic range, show rich markdown with the finding's context. The hover adds value beyond the diagnostic message (which already shows the title). Fields are included only when present:

```markdown
**Why it matters:** User input flows into an SQL query without parameterization.
An attacker can modify the query to read, modify, or delete data.

**Suggested fix:** Use parameterized queries instead of string concatenation.

**CWE:** CWE-89 · **Confidence:** 0.92 · **Effort:** Low
```

Fields that are `None` on the Finding are skipped. If a finding has no extra fields beyond title/description, the hover is omitted (the diagnostic tooltip is sufficient).

### Status Notification (`repotoire/scoreUpdate`)

Custom notification sent after every `ready`, `delta`, and `unchanged` event:

```json
{"score": 92.3, "grade": "A-", "delta": 0.7, "findings": 85}
```

Editors that understand this notification can display it in the status bar (e.g., `Repotoire: A- (92.3)`). Editors that don't understand it silently ignore it.

---

## LSP Server Lifecycle

### Event Loop Architecture

The LSP runs a single async event loop on tokio that processes three event sources:

```
LSP Event Loop (tokio)
  ├── LSP requests (from editor via tower-lsp)
  │     didSave → add file to debounce buffer
  │     codeAction → respond from cached diagnostics
  │     hover → respond from cached findings
  │     shutdown → send shutdown to worker, exit
  │
  ├── Debounce timer (200ms)
  │     fires → collect all buffered files, send one analyze command to worker
  │
  └── Worker events (from child stdout, read via tokio)
        ready → push all diagnostics, send scoreUpdate, clear progress
        progress → forward as $/progress
        delta → patch diagnostic map, re-publish affected files, send scoreUpdate
        unchanged → send scoreUpdate
        error → window/showMessage warning
```

### Debounce Behavior

`didSave` does not immediately trigger analysis:

1. The saved file path is added to a pending set.
2. After 200ms with no new saves, the LSP sends one `analyze` command to the worker with all pending files.
3. If a save arrives during in-flight analysis, it goes into the next pending batch.
4. When the current analysis responds, the LSP immediately sends the next batch if one exists (no 200ms wait — the user already waited).

This prevents flooding on "save all" or formatter-triggered multi-file saves.

### Stale Response Handling

- The LSP maintains a `latest_request_id` counter — the most recent `analyze` command sent.
- When a response arrives with `id < latest_request_id`, it's discarded (stale).
- Unsolicited events (`id: null`) are never stale — they come from the filesystem watcher and are always relevant.
- No cancellation of in-flight analysis — the worker finishes, the LSP discards the stale result, and the next analysis builds on the updated engine state. No wasted work internally.

### Progress Reporting

- On `progress` events from the worker, the LSP creates or updates a `$/progress` work-done token.
- Shows in editor as "Repotoire: parsing (120/456)".
- Progress is cleared when `ready`, `delta`, `unchanged`, or `error` arrives.

### Startup

1. Editor launches `repotoire lsp` (stdio transport).
2. LSP completes `initialize` handshake, declares capabilities (diagnostics, code actions, hover).
3. LSP spawns `repotoire __worker` as a child process.
4. LSP sends `init` command with workspace root path and config.
5. Worker runs initial analysis, emitting `progress` events during long stages.
6. Worker emits `ready` with full findings list.
7. LSP pushes diagnostics for all files, sends `repotoire/scoreUpdate`.

### Worker Crash Recovery

1. LSP detects child process exit (broken pipe on stdout read, or `waitpid`).
2. Sends `window/showMessage` warning to editor: "Repotoire analysis process crashed. Restarting..."
3. Waits 2 seconds, respawns worker, sends `init` again.
4. After 3 consecutive crashes within 60 seconds, gives up — sends error notification, clears all diagnostics, stays alive but inactive.
5. The user can restart by reloading the editor window.

### Shutdown

1. Editor sends LSP `shutdown` request.
2. LSP sends `shutdown` command to worker, waits up to 5 seconds for exit.
3. If worker doesn't exit, kills the process.
4. LSP responds to the `shutdown` request and exits on `exit` notification.

---

## File Structure

```
repotoire-cli/src/
├── cli/
│   ├── mod.rs                    — add Lsp and Worker command variants
│   ├── lsp/
│   │   ├── mod.rs                — repotoire lsp entry point, spawns worker, runs event loop
│   │   ├── server.rs             — tower-lsp LanguageServer trait impl (capabilities, handlers)
│   │   ├── diagnostics.rs        — Finding → Diagnostic mapping, HashMap<Url, Vec<Diagnostic>> management
│   │   ├── actions.rs            — code action generation (ignore suppression, suggested fixes)
│   │   ├── hover.rs              — hover markdown rendering from Finding fields
│   │   └── worker_client.rs      — child process management (spawn, restart, JSONL read/write, stale filtering)
│   ├── watch/
│   │   ├── mod.rs                — repotoire watch entry point (unchanged for now — direct WatchEngine use)
│   │   ├── display.rs            — terminal output (existing)
│   │   ├── delta.rs              — WatchDelta (existing, also used by worker)
│   │   ├── engine.rs             — WatchEngine (existing, used by worker)
│   │   └── filter.rs             — WatchFilter (existing, used by worker)
│   └── worker/
│       ├── mod.rs                — repotoire __worker entry point, JSONL stdin/stdout event loop
│       ├── protocol.rs           — shared Command/Event serde types (used by both lsp/ and worker/)
│       └── handler.rs            — command dispatch: init → WatchEngine::new, analyze → reanalyze, etc.
```

**Key relationships:**
- `worker/protocol.rs` defines the JSONL types — shared between the LSP (client) and worker (server).
- `worker/handler.rs` uses `WatchEngine`, `WatchFilter`, and `compute_delta` from `cli/watch/`.
- `lsp/worker_client.rs` spawns `repotoire __worker` and reads/writes JSONL.
- `lsp/server.rs` implements `tower_lsp::LanguageServer`, delegates to diagnostics/actions/hover modules.
- `watch/mod.rs` is unchanged for now — it continues to use `WatchEngine` directly. Migration to the worker process is deferred until the worker is battle-tested.

**New dependencies:**
- `tower-lsp` — LSP protocol implementation
- `tokio` (features: `full`) — async runtime for the LSP event loop
- `tokio-util` (features: `codec`) — for reading JSONL lines from child process stdout

---

## Testing

### Unit Tests

| Module | Tests |
|---|---|
| `protocol.rs` | Serialize/deserialize round-trip for all command and event types |
| `diagnostics.rs` | Finding → Diagnostic mapping: severity levels, range with/without line_end, missing fields |
| `actions.rs` | Ignore comment generation per language (Python `#`, Rust `//`, etc.), suggested fix action presence/absence |
| `hover.rs` | Markdown rendering with all fields present, with partial fields, with no extra fields (returns None) |
| `worker_client.rs` | JSONL line parsing, stale response filtering (id < latest), unsolicited event passthrough |
| `handler.rs` | Command dispatch: init creates engine, analyze returns delta, shutdown exits |

### Integration Tests

| Test | Description |
|---|---|
| `worker_test.rs` | Spawn `repotoire __worker`, send init + analyze commands via stdin, verify JSONL events on stdout |
| `lsp_test.rs` | Spawn `repotoire lsp`, send LSP initialize + didSave via JSON-RPC on stdio, verify `publishDiagnostics` response |

### Manual Test Scenarios

- Open a project in an editor with LSP configured
- Save a file with a known issue → diagnostic appears with squiggly underline
- Hover over the diagnostic → rich markdown popup with context
- Click code action → `// repotoire:ignore[detector]` inserted
- Fix the issue and save → diagnostic disappears
- Save multiple files rapidly → single analysis (debounce working)
- Kill worker process manually → LSP recovers and diagnostics return
- Check editor status bar → score updates after each save

---

## Implementation Order

1. **Worker protocol types** — `protocol.rs` with Command/Event serde structs + unit tests
2. **Worker process** — `worker/mod.rs` + `handler.rs`, JSONL loop wrapping WatchEngine
3. **Worker integration test** — spawn worker, send commands, verify events
4. **LSP skeleton** — `lsp/mod.rs` + `server.rs` with tower-lsp, initialize handshake only
5. **Worker client** — `lsp/worker_client.rs`, spawn/restart/read/write child process
6. **Diagnostics** — `diagnostics.rs`, Finding → Diagnostic mapping, publish on ready/delta
7. **Code actions** — `actions.rs`, ignore suppression + suggested fix
8. **Hover** — `hover.rs`, markdown rendering from Finding fields
9. **Status notification** — `repotoire/scoreUpdate` custom notification
10. **Debounce + stale handling** — 200ms save batching, request ID tracking
11. **Progress reporting** — forward worker progress events as `$/progress`
12. **Crash recovery** — detect worker exit, respawn with backoff
13. **CLI integration** — add `Lsp` and `Worker` command variants to `cli/mod.rs`
14. **Documentation** — editor config snippets for VS Code, Neovim, Helix
15. **Watch migration** (deferred) — rewrite watch to use worker process

---

## Success Criteria

- `repotoire lsp` starts, completes handshake, pushes diagnostics within 5 seconds on a medium repo
- Saving a file updates diagnostics within 2 seconds
- Rapid saves (3 saves in 500ms) produce exactly one analysis
- Worker crash recovers automatically — diagnostics return within 10 seconds
- Hover shows rich markdown for any diagnostic with extra fields
- Code action inserts `// repotoire:ignore[detector]` with correct comment style for the language
- Code action applies `suggested_fix` when available
- Score notification updates after every analysis
- `repotoire watch` continues to work unchanged (not migrated yet)
- Config snippets documented for VS Code, Neovim, and Helix
