# Hand-Rolled Git Implementation (git2 Replacement)

## Goal

Replace the `git2` crate (libgit2 C bindings) with a hand-rolled pure Rust git reader in `src/git/raw/`. Eliminates ~89 transitive dependencies including the libgit2 C build toolchain. Targets performance parity or better than libgit2 by exploiting repotoire's read-only workload.

## Motivation

- **Dependency reduction**: git2 pulls in libgit2-sys, libz-sys, cc, url, idna, etc. (~89 unique transitive deps out of 275 total, ~32%)
- **C build toolchain elimination**: libgit2-sys requires `cc` for C compilation. Pure Rust means no C compiler needed at build time.
- **Performance**: libgit2 eagerly decompresses blob content during tree diffs even when only tree-entry OIDs are needed. A two-phase approach (tree OID comparison first, blob diff on demand) avoids unnecessary decompression. gitoxide benchmarks show 6-37x speedup over libgit2 for tree diffing with this approach.
- **Parallelism**: git2's `Repository` is not `Send`. The current blame prewarm opens N separate repository handles in parallel, each mmapping the same packfiles independently. A `Send + Sync` repo handle eliminates this waste.

## Current git2 Usage

4 files, 2 imports, ~5 distinct operations:

| Operation | Files | git2 API |
|-----------|-------|----------|
| Open repo | blame, history, bootstrap, telemetry | `Repository::discover()` |
| Walk commits | history, bootstrap, telemetry | `repo.revwalk()`, `Sort::TIME`, `Sort::REVERSE`, `simplify_first_parent()` |
| Diff trees | history, bootstrap | `repo.diff_tree_to_tree()`, `diff.deltas()`, `diff.foreach()`, `diff.stats()` |
| Blame file | blame | `repo.blame_file()`, `BlameOptions` |
| Read tree | history | `repo.head().peel_to_tree()`, `tree.walk()` |

Supporting APIs: `Oid::from_str()`, `repo.find_commit()`, `commit.tree()`, `commit.parent()`, `commit.author()`, `commit.time()`, `repo.workdir()`, `DiffOptions` (pathspec, skip_binary_check, context_lines, etc.)

Tests additionally use: `Repository::init()`, `repo.config()`, `repo.signature()`, `repo.index()`, `repo.commit()`.

## Architecture

### Module Layout

```
src/git/raw/
  mod.rs          -- Public API: RawRepo, re-exports
  sha1.rs         -- SHA-1 (RFC 3174), ~250 lines
  deflate.rs      -- DEFLATE decompressor (RFC 1951), ~1,000 lines
  oid.rs          -- 20-byte OID type with hex parse/display, ~80 lines
  object.rs       -- Loose object reader, ~120 lines
  pack.rs         -- Packfile reader + delta reconstruction, ~400 lines
  pack_index.rs   -- Pack index v2 reader, ~200 lines
  repo.rs         -- RawRepo: open, ref resolution, object lookup, ~200 lines
  commit.rs       -- Commit object parser, ~80 lines
  tree.rs         -- Tree object parser + walk, ~80 lines
  diff.rs         -- Tree-to-tree diff + Myers blob diff, ~400 lines
  blame.rs        -- Line-level blame algorithm, ~300 lines
  revwalk.rs      -- Time-sorted commit walker, ~150 lines
```

Estimated total: ~3,250 lines of pure Rust, zero external dependencies.

### Layer Diagram

```
┌──────────────────────────────────────────────────────────┐
│  Consumers: GitHistory, GitBlame, bootstrap, telemetry   │
├──────────────────────────────────────────────────────────┤
│  High-level: revwalk, diff (tree + blob), blame          │
├──────────────────────────────────────────────────────────┤
│  Object layer: RawRepo, commit/tree parsing              │
├──────────────────────────────────────────────────────────┤
│  Storage: loose objects, packfile + index v2             │
├──────────────────────────────────────────────────────────┤
│  Primitives: SHA-1, DEFLATE, OID                         │
└──────────────────────────────────────────────────────────┘
```

## Component Designs

### SHA-1 (`sha1.rs`)

RFC 3174 implementation. ~250 lines. Only used for object ID computation (not crypto-sensitive).

- 80-round block transform with 5 state words
- Padding: append 0x80, zero-pad to 56 mod 64, append 8-byte big-endian bit length
- API: `fn sha1(data: &[u8]) -> [u8; 20]` and streaming `Sha1::new() -> update() -> finalize()`
- Performance: ~380 MB/s on typical hardware. At git-object sizes (<100KB), hashing takes <1us per object.

### DEFLATE Decompressor (`deflate.rs`)

RFC 1951 decode-only implementation. ~1,000 lines. No compression needed.

Components:
- **Bit reader**: LSB-first, 64-bit buffer with unconditional refill when <32 bits remain. ~80 lines.
- **Huffman table**: Canonical reconstruction from code lengths. Lookup table indexed by next N bits for O(1) decode. ~100 lines.
- **Block types**: Stored (uncompressed, ~20 lines), Fixed Huffman (hardcoded tables, ~30 lines), Dynamic Huffman (meta-Huffman indirection for tree reconstruction, ~200 lines).
- **LZ77 decode**: Literal/length codes 257-285 with extra bits, distance codes 0-29 with extra bits. ~100 lines. Critical edge case: when length > distance, copy byte-by-byte (overlapping copy / run-length encoding).
- **Zlib framing**: Parse 2-byte zlib header (CMF + FLG). Skip Adler-32 checksum verification — git already verifies integrity via SHA-1 object IDs. ~15 lines.

Key edge cases:
- Dynamic Huffman meta-alphabet transmitted in scrambled order (16, 17, 18, 0, 8, 7, 9, 6, 10, 5, 11, 4, 12, 3, 13, 2, 14, 1, 15)
- Overlapping LZ77 copies when length > distance
- Insert instruction byte 0x00 in delta format is reserved/invalid

### OID (`oid.rs`)

20-byte SHA-1 object identifier.

```rust
#[derive(Clone, Copy, PartialEq, Eq, Hash, PartialOrd, Ord)]
pub struct Oid([u8; 20]);
```

- `from_hex(&str) -> Result<Oid>`: parse 40-char hex string
- `to_hex(&self) -> String`: format as 40-char hex
- `from_bytes(&[u8; 20]) -> Oid`: from raw binary (tree entries, pack index)
- `Display` impl for short hash (first 12 hex chars)
- SHA-256 repos: detect via `.git/config` `extensions.objectFormat`, return clear error. Not implemented in v1.

### Loose Object Reader (`object.rs`)

Read objects from `.git/objects/xx/yyyyyy...`.

1. Construct path: `objects_dir / oid[0..2] / oid[2..40]`
2. Read file bytes
3. Zlib decompress (2-byte header + deflate)
4. Parse header: `type SP size NUL` where type is `blob|tree|commit|tag`, size is decimal ASCII
5. Return `(ObjectType, Vec<u8>)` — the content bytes after the NUL

Edge case: size field may have leading zeros (be tolerant on read).

### Pack Index v2 (`pack_index.rs`)

Binary reader for `.git/objects/pack/*.idx` files.

Layout (in order after 8-byte header):
1. **Fanout table**: 256 x u32 BE. `fanout[byte]` = count of OIDs with first byte <= `byte`. `fanout[255]` = total object count.
2. **OID table**: N x 20 bytes, lexicographically sorted. Binary search with fanout to narrow range.
3. **CRC32 table**: N x u32 (not needed for read-only, skip).
4. **32-bit offset table**: N x u32 BE. If MSB set, lower 31 bits index into 64-bit table.
5. **64-bit offset table**: Present only when any offset > 2^31-1 (packs > ~2GB). Entries are u64 BE.

API: `fn find(&self, oid: &Oid) -> Option<u64>` — returns pack file offset.

Critical: must handle 64-bit offsets or repos with >4GB packs (linux kernel) break silently.

### Packfile Reader (`pack.rs`)

Read objects from `.git/objects/pack/*.pack` files.

**Header**: `PACK` magic (4 bytes) + version u32 BE + object count u32 BE. Accept version 2 only. Explicitly reject version 3 (SHA-256 packs) with a clear error rather than silently misinterpreting.

**Object entry at offset**:
1. Parse variable-length type+size header. First byte: bit 7 = continuation flag, bits 4..=6 (inclusive) = 3-bit type (mask `0x70 >> 4`), bits 0..=3 = 4-bit size (mask `0x0F`). Subsequent bytes contribute 7 bits each. Note: first chunk is 4 bits, not 7.
2. Based on type:
   - Types 1-4 (commit/tree/blob/tag): zlib decompress `size` bytes of content.
   - Type 6 (OFS_DELTA): parse negative offset (non-standard VLQ with +1 correction on continuation bytes), then zlib decompress delta data. Resolve base object recursively. Apply delta.
   - Type 7 (REF_DELTA): read 20-byte base OID, then zlib decompress delta data. Look up base object. Apply delta.

**Delta application**: Delta data starts with two VLQ integers (base size, result size). Then instruction stream:
- **Copy** (MSB=1): bits 0-3 select offset bytes (1-4, little-endian), bits 4-6 select size bytes (1-3, little-endian). If size resolves to 0, use 0x10000 (65536).
- **Insert** (MSB=0): bits 0-6 = byte count (1-127), followed by that many literal bytes. Value 0 is reserved/invalid.

**OFS_DELTA VLQ encoding** (different from size VLQ):
```
result = byte[0] & 0x7F
while byte has MSB set:
    result = (result + 1) << 7
    result |= next_byte & 0x7F
```
The `+1` before shifting is the key difference from standard VLQ.

Delta chain depth limit: 50 (git default). Use iterative stack, not recursion.

**mmap**: Packfiles are memory-mapped for zero-copy reads. Single mmap shared across threads.

### Repo Opening & Ref Resolution (`repo.rs`)

**Repository discovery** (replaces `Repository::discover()`):
1. Walk up from given path looking for `.git/` directory or `.git` file
2. If `.git` is a file: read `gitdir: <path>`, follow indirection (may be relative)
3. For worktrees: read `commondir` file to find shared object store
4. Store: git_dir path, common_dir path (for objects/refs), workdir path

**Ref resolution** (for `push_head()`):
1. Read `.git/HEAD` — either `ref: refs/heads/<branch>` or bare 40-hex OID
2. If symbolic: read `.git/refs/heads/<branch>` file
3. If not found: search `.git/packed-refs` (parse on first access, sort lexicographically by ref name if `sorted` trait absent in header, cache in memory)
4. Symbolic ref chains: follow up to 10 levels, error on cycle

**Alternates support**: On open, read `.git/objects/info/alternates` if it exists. Each line is a path to another object directory. Chain resolution: alternates can have their own alternates files — follow recursively with cycle detection (cap at 5 levels). Object lookup searches the local store first, then each alternate in order. Common in CI (`actions/checkout --reference`), `git clone --shared`, and forked repos.

**Object lookup** (`find_object(oid)`):
1. Check LRU cache (trees + commits only, not blobs)
2. Try loose: `objects/xx/yy...` path
3. Try each pack index: binary search for OID, read from packfile at offset
4. Try each alternate's loose objects and pack indices
5. For delta objects: resolve chain iteratively (stack-based)
6. Return `(ObjectType, Vec<u8>)`

**LRU cache policy**: Two-tiered. Trees and commits cached (small, re-accessed during revwalk). Blobs never cached (up to 2MB, typically accessed once). Cache sized by total byte count of stored `Vec<u8>` content — cap at 16MB. On insert, evict LRU entries until under cap. Typical tree objects are 1-10KB, commits are <1KB, so 16MB holds thousands of entries.

**Tag peeling**: If ref resolution yields a tag object (type 4), read the tag and follow the `object` field to the target commit. Annotated tags point to tag objects which point to commits. Peel through up to 10 levels (tags can chain, though rare). Used implicitly — any ref lookup that expects a commit must peel through tags.

**Shallow clone support**: Read `.git/shallow` on open. Store set of grafted OIDs. When walking parents, treat shallow OIDs as root commits (no parents). Don't error on missing parent objects if OID is in the shallow set.

### Commit Parser (`commit.rs`)

Parse commit object content (plaintext):

```
tree <40-hex-oid>\n
parent <40-hex-oid>\n    (0 or more)
author <name> <email> <timestamp> <tz>\n
committer <name> <email> <timestamp> <tz>\n
[gpgsig <multi-line, space-prefixed continuation>]\n
\n
<message>
```

Multi-line headers (gpgsig, mergetag): continuation lines start with a space. Skip the entire header — we don't use signatures.

Timestamp: Unix epoch seconds (signed i64) + timezone `+HHMM` / `-HHMM`.

Output struct:
```rust
pub struct RawCommit {
    pub tree_oid: Oid,
    pub parents: Vec<Oid>,       // preserves order (first parent = main branch)
    pub author_name: String,
    pub author_email: String,
    pub author_time: i64,        // epoch seconds
    pub author_tz_offset: i32,   // minutes from UTC
    pub committer_time: i64,
    pub message: String,         // first line only — deliberate simplification, repotoire never uses full body
}
```

### Tree Parser (`tree.rs`)

Parse tree object content (binary):

Repeated entries: `<mode> <name>\0<20-byte-oid>` (no separators, no trailing newline).

Modes are variable-length ASCII octal strings (no zero-padding to fixed width):
- `40000` — subtree (directory), 5 digits
- `100644` — normal file, 6 digits
- `100755` — executable, 6 digits
- `120000` — symlink, 6 digits
- `160000` — gitlink (submodule), 6 digits

Parser must handle any mode length — do not assume fixed width.

Output:
```rust
pub struct TreeEntry {
    pub mode: u32,
    pub name: String,
    pub oid: Oid,
}

pub fn parse_tree(data: &[u8]) -> Vec<TreeEntry>
```

**Tree walk** (replaces `tree.walk(TreeWalkMode::PreOrder, callback)`): Recursive pre-order traversal yielding `(path_prefix: &str, entry: &TreeEntry)` for every entry. Blob entries (mode != 40000) are the leaf nodes used by `get_tracked_files()`. Subtree entries (mode 40000) are recursed into, prepending `name/` to the path prefix.

**Peel to tree**: Convenience path for `head.peel_to_tree()`. Resolves HEAD ref -> commit OID -> parse commit -> extract `tree_oid` -> read tree object. Handles tag peeling if HEAD points to a tag (rare). Exposed as `repo.head_tree() -> Result<(Oid, Vec<TreeEntry>)>`.

### Tree-to-Tree Diff (`diff.rs`)

Replaces `repo.diff_tree_to_tree()`.

**Algorithm**: Both trees are sorted by name (git spec guarantees this). Merge-walk simultaneously:
- Same name + same OID = unchanged, skip
- Same name + different OID = modified. If both are subtrees, recurse. If blob, emit Modified delta.
- Name only in old tree = Deleted
- Name only in new tree = Added
- **Mode 160000 (gitlink/submodule)**: Skip entirely — never recurse into submodule entries. The OID points to a commit in the submodule's object store, not our repo. Matches current `ignore_submodules(true)` behavior.

**Pathspec filtering**: Before recursing into a subtree, check if the subtree's path prefix matches any pathspec. Prune non-matching subtrees without reading their tree objects. This is critical for `fast_pathspec_opts` performance (single-file diffs skip all unrelated subtrees).

**Output**:
```rust
pub struct DiffDelta {
    pub old_path: Option<String>,
    pub new_path: String,
    pub status: DeltaStatus,     // Added, Deleted, Modified
    pub old_oid: Oid,
    pub new_oid: Oid,
}

pub enum DeltaStatus { Added, Deleted, Modified }
```

**Blob diff** (for hunk-level detail): Myers diff on line-split blob content. Only called when consumers need hunk data (`get_hunks_for_paths`, blame). Never called for delta-only operations (`get_file_churn_counts`).

```rust
pub struct DiffHunk {
    pub old_start: u32,
    pub old_lines: u32,
    pub new_start: u32,
    pub new_lines: u32,
}

pub fn diff_blobs(old: &[u8], new: &[u8]) -> Vec<DiffHunk>
```

**Diff stats**: Computed from hunk data, which requires running the Myers blob diff. There is no shortcut to get line-level insertion/deletion counts without diffing blob content. Call sites that currently use `diff.stats()` without requesting hunks (e.g., `extract_commit_info`, `get_commit_file_stats`) will implicitly trigger blob decompression + diff. This is acceptable — the cost is bounded by file size (2MB max) and these are already I/O-bound operations.

```rust
pub struct DiffStats {
    pub insertions: usize,
    pub deletions: usize,
}

pub fn compute_stats(hunks: &[DiffHunk]) -> DiffStats
```

**Pathspec matching**: All pathspecs are literal exact matches (no glob patterns). This matches the current code which always calls `disable_pathspec_match(true)`. Multiple pathspecs supported — match if file path equals any pathspec (OR semantics, matching `get_hunks_for_paths` which adds multiple paths via `diff_opts.pathspec(path)` in a loop). Pathspec filtering prunes subtrees during tree walk — if a subtree's path prefix doesn't match any pathspec, skip it entirely without reading the tree object.

**Hunk output**: Zero context lines, no inter-hunk merging. Each Myers edit region produces a separate hunk. This matches the current `context_lines(0)` and `interhunk_lines(0)` settings in `fast_diff_opts()`. Hunks map to the existing `HunkDetail` struct via: `new_start` = `DiffHunk.new_start`, `new_end` = `new_start + new_lines`, `insertions` = `new_lines`, `deletions` = `old_lines`.

**Binary detection**: Skipped entirely. The current code sets `skip_binary_check(true)` everywhere. No consumer needs binary file detection.

**SHA-1 verification**: Not performed on object read. The current code sets `strict_hash_verification(false)` for performance. Our implementation follows the same policy — trust the object store, never recompute SHA-1 to verify. SHA-1 is only used for computing the hash when we need to identify objects (e.g., looking up by OID).

**Myers diff implementation** (~200 lines):
- Forward-only O(ND) algorithm
- Line-level granularity (split on `\n`)
- Bail-out heuristic: if edit distance D exceeds `max(old_lines, new_lines) / 2`, treat as full replacement (all old lines deleted, all new lines inserted). Protects against O(N^2) pathological cases on large auto-generated files.
- Output: edit script of Equal/Insert/Delete operations with line indices

### Revwalk (`revwalk.rs`)

Replaces `git2::Revwalk`.

Time-sorted commit traversal using a max-heap (BinaryHeap) keyed by commit timestamp.

```rust
pub struct RevWalk<'repo> {
    repo: &'repo RawRepo,
    heap: BinaryHeap<(i64, Oid)>,   // (timestamp, oid), max-heap = newest first
    seen: HashSet<Oid>,
    first_parent_only: bool,
    reverse: bool,                   // collect all, then reverse iteration order
}
```

- `push_head()`: resolve HEAD to OID, parse commit for timestamp, push to heap
- `simplify_first_parent()`: only enqueue `parents[0]`, skip merge parents
- `find_root_commit()`: dedicated helper for `telemetry/config.rs` use case (`Sort::TIME | Sort::REVERSE` + `.next()`). Walks first-parent chain to the root commit (zero parents) without collecting the entire history. O(depth) instead of O(N) — avoids performance regression on large repos where collecting + reversing the full revwalk would be expensive.
- `Iterator::next()`: pop max timestamp from heap, enqueue parents (if not seen), yield OID
- Shallow awareness: skip parent OIDs that are in the shallow set

### Blame (`blame.rs`)

Replaces `repo.blame_file()` + `BlameOptions`.

**Algorithm**:
1. Resolve HEAD, read current file content. Each line starts as unblamed.
2. Walk backwards through commits that modified this file (revwalk + pathspec filter).
3. At each commit, read the file blob from the commit's tree. Diff against child version (Myers).
4. Lines unchanged between parent and child: pass blame upward to parent.
5. Lines new in this commit (insertions): assign blame to this commit. Mark as settled.
6. Stop when all lines settled or root commit reached (remaining lines blamed to root).

No rename/copy detection — current repotoire code never enables `-M`/`-C` in `BlameOptions`.

Output maps to existing `LineBlame` struct:
```rust
pub struct BlameHunk {
    pub commit: Oid,
    pub author_name: String,
    pub author_email: String,
    pub author_time: i64,
    pub orig_start_line: u32,
    pub num_lines: u32,
}
```

**BlameOptions replacement**: `min_line` / `max_line` filtering applied after blame computation (blame the full file, filter output to range). This matches the current behavior where `blame_lines()` sets these options.

### Thread Safety

`RawRepo` is `Send + Sync`:
- Packfile mmaps are read-only (`&[u8]` from mmap, immutable after creation)
- LRU object cache behind `Mutex<LruCache<Oid, (ObjectType, Vec<u8>)>>`
- Packed-refs parsed once, stored as `Vec<(String, Oid)>`, immutable after init

This eliminates the current pattern of opening N `Repository::discover()` handles in the blame prewarm parallel loop. `GitBlame::open()` will take an `Arc<RawRepo>` (or `&RawRepo` with lifetime) rather than discovering its own repository — all blame threads share the same mmapped packfiles and object cache.

## Error Handling

```rust
#[derive(Debug, thiserror::Error)]
pub enum GitError {
    #[error("object not found: {0}")]
    ObjectNotFound(Oid),
    #[error("corrupt object at {path}: {detail}")]
    CorruptObject { path: String, detail: String },
    #[error("invalid pack file: {0}")]
    InvalidPack(String),
    #[error("ref not found: {0}")]
    RefNotFound(String),
    #[error("not a git repository: {0}")]
    NotAGitRepo(String),
    #[error("decompression failed: {0}")]
    DecompressError(String),
    #[error("delta chain too deep (>{0} levels)")]
    DeltaChainTooDeep(usize),
    #[error(transparent)]
    Io(#[from] std::io::Error),
}
```

All public methods return `Result<T, GitError>`. Existing call sites use `anyhow` and `?`, so wrapping is seamless.

## Testing Strategy

**Unit tests**: Each submodule tested against repotoire's own `.git/` as a real-world fixture. Known HEAD, known commit count, known file content — assert correct parsing.

**Golden tests**: During development, run identical operations through git2 and raw implementation, assert matching output. Remove git2 golden tests after migration.

**Test fixture setup**: Use `git init` + `git commit` via `std::process::Command` instead of git2's `Repository::init()`. Creates real git repos with real packfiles.

**Edge case tests**:
- Empty repo (no commits)
- Shallow clone (missing parents, `.git/shallow` file)
- Detached HEAD (bare OID in HEAD, not symbolic ref)
- Packed-refs only (no loose refs)
- Delta chains > 10 levels deep
- Pack > 4GB (64-bit offset table) — synthetic test with crafted index
- Worktree (`.git` file with `gitdir:` indirection)
- Multi-line commit headers (gpgsig blocks)

## Migration Plan

Incremental, not big-bang. git2 and raw/ coexist during migration.

1. **Build `src/git/raw/`** with full test coverage. git2 remains in Cargo.toml.
2. **Swap `telemetry/config.rs`** — simplest consumer, just needs root commit OID via revwalk.
3. **Swap `classifier/bootstrap.rs`** — revwalk + diff for fix-commit and stable-file detection.
4. **Swap `history.rs`** — the bulk. Revwalk, diff, tree walk, hunks, churn. Replace `GitHistory` internals to use `RawRepo`.
5. **Swap `blame.rs`** — most complex. Replace `GitBlame` internals with raw blame algorithm.
6. **Swap tests** — replace `Repository::init()` test helpers with `Command::new("git")` setup. Note: this adds a runtime dependency on the `git` binary for tests only (not for production). CI environments already have git installed. Consider a minimal `RawRepo::init_for_test()` helper that creates bare `.git/` structure (HEAD, objects/, refs/) to avoid the external dependency if test speed is a concern.
7. **Remove `git2`** from Cargo.toml. Delete golden tests. Verify `cargo tree` shows ~89 fewer deps.

Each step is a separate commit. The codebase compiles and tests pass at every step.

## Future Optimizations (Not in v1)

- **Commit-graph file reader**: 6-10x revwalk speedup on large repos. Flat binary format with pre-computed parent OIDs and generation numbers. Worth adding after v1 is stable.
- **Multi-pack-index (MIDX)**: Modern repos from GitHub have these. v1 falls back to scanning individual .idx files. MIDX avoids opening N index files.
- **SHA-256 support**: Parameterize hash length (20 vs 32 bytes). Not needed today — no major hosting platform supports SHA-256 repos yet.
- **Replace refs**: `refs/replace/` for object substitution. libgit2 still doesn't support this. Low priority. Note: repos using replace refs will get original objects instead of replacements — a known behavioral divergence from git CLI (but matching git2 behavior).
- **Grafts** (`.git/info/grafts`): Deprecated in favor of `git replace`, but some older repos may have them. Very low priority.

## Dependencies Removed

| Crate | Why |
|-------|-----|
| `git2` | Replaced by `src/git/raw/` |
| `libgit2-sys` | C bindings, no longer needed |
| `libz-sys` | zlib C library, replaced by hand-rolled deflate |
| `cc` | C compiler integration for libgit2-sys build (if not used elsewhere) |
| `url` | URL parsing for git remotes (we never use remote URLs) |
| `idna` | International domain names (transitive via url) |
| `form_urlencoded` | URL encoding (transitive via url) |
| `percent-encoding` | URL encoding (transitive via url) |
| `vcpkg` | Windows package manager lookup (build dep) |
| `pkg-config` | Unix package lookup (build dep) |
| Plus ~79 other transitive deps | Various |

Net result: ~89 fewer transitive dependencies, elimination of C build toolchain requirement.

## Estimated LOC

| Component | Lines |
|-----------|-------|
| SHA-1 | ~250 |
| DEFLATE decompressor | ~1,000 |
| OID type | ~80 |
| Loose object reader | ~120 |
| Pack index v2 | ~200 |
| Packfile reader + delta reconstruction | ~400 |
| Repo open + ref resolution | ~200 |
| Commit/tree/tag parsing | ~200 |
| Tree-to-tree diff | ~200 |
| Myers diff (blob-level) | ~200 |
| Revwalk | ~150 |
| Blame | ~300 |
| **Total implementation** | **~3,300** |
| Tests (estimated) | ~1,500 |
| **Grand total** | **~4,800** |
