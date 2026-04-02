# git2 Replacement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the `git2` crate with a hand-rolled pure Rust git reader in `src/git/raw/`, eliminating ~89 transitive dependencies including the libgit2 C build toolchain.

**Architecture:** Bottom-up implementation: primitives (SHA-1, DEFLATE, OID) -> storage (loose objects, pack index, packfile) -> object layer (repo, commit, tree) -> high-level (revwalk, diff, blame) -> migration (swap consumers one at a time).

**Tech Stack:** Pure Rust, zero new dependencies. Uses `memmap2` (already in Cargo.toml), `thiserror` (already in Cargo.toml). All other functionality hand-rolled.

**Spec:** `docs/superpowers/specs/2026-04-01-git2-replacement-design.md`

---

## File Structure

### New files (all under `src/git/raw/`)

| File | Responsibility | Approx Lines |
|------|---------------|-------------|
| `mod.rs` | Public API surface, re-exports | ~50 |
| `error.rs` | `GitError` enum with thiserror | ~30 |
| `sha1.rs` | RFC 3174 SHA-1 (hash-only, no crypto) | ~250 |
| `deflate.rs` | RFC 1951 DEFLATE decompressor + zlib framing | ~1,000 |
| `oid.rs` | 20-byte OID type, hex parse/display | ~80 |
| `object.rs` | Loose object reader (decompress `.git/objects/xx/yy`) | ~120 |
| `pack_index.rs` | Pack index v2 binary reader | ~200 |
| `pack.rs` | Packfile reader + delta reconstruction | ~400 |
| `repo.rs` | `RawRepo`: discovery, ref resolution, object lookup, LRU cache | ~300 |
| `commit.rs` | Commit object parser | ~80 |
| `tree.rs` | Tree object parser + recursive walk | ~100 |
| `revwalk.rs` | Time-sorted commit traversal (BinaryHeap) | ~150 |
| `diff.rs` | Tree-to-tree diff + Myers blob diff + stats | ~400 |
| `blame.rs` | Line-level blame algorithm | ~300 |

### Modified files (migration phase)

| File | Change |
|------|--------|
| `src/git/mod.rs` | Add `pub mod raw;` |
| `src/telemetry/config.rs` | Replace `git2::Repository` with `RawRepo` |
| `src/classifier/bootstrap.rs` | Replace `git2::Repository` with `RawRepo` |
| `src/git/history.rs` | Replace `GitHistory` internals with `RawRepo` |
| `src/git/blame.rs` | Replace `GitBlame` internals with `RawRepo` + raw blame |
| `Cargo.toml` | Remove `git2` dependency |

---

## Task 1: Module scaffold + error type + OID

**Files:**
- Create: `src/git/raw/mod.rs`
- Create: `src/git/raw/error.rs`
- Create: `src/git/raw/oid.rs`
- Modify: `src/git/mod.rs`

- [ ] **Step 1: Create module scaffold**

Create `src/git/raw/mod.rs`:
```rust
pub mod error;
pub mod oid;

pub use error::GitError;
pub use oid::Oid;

/// Shared test helpers for all raw git submodules.
#[cfg(test)]
pub(crate) mod tests {
    use std::path::PathBuf;

    /// Walk up from CARGO_MANIFEST_DIR to find the nearest `.git/` directory.
    pub fn find_repo_git_dir() -> PathBuf {
        let mut dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
        loop {
            let git = dir.join(".git");
            if git.is_dir() {
                return git;
            }
            if !dir.pop() {
                panic!("not in a git repo");
            }
        }
    }
}
```

Add to `src/git/mod.rs`:
```rust
pub mod raw;
```

- [ ] **Step 2: Write GitError enum**

Create `src/git/raw/error.rs`:
```rust
use super::oid::Oid;

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

- [ ] **Step 3: Write failing OID tests**

In `src/git/raw/oid.rs`, add test module:
```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_from_hex_valid() {
        let hex = "a94a8fe5ccb19ba61c4c0873d391e987982fbbd3";
        let oid = Oid::from_hex(hex).unwrap();
        assert_eq!(oid.to_hex(), hex);
    }

    #[test]
    fn test_from_hex_invalid_length() {
        assert!(Oid::from_hex("abcd").is_err());
    }

    #[test]
    fn test_from_hex_invalid_chars() {
        let bad = "zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz";
        assert!(Oid::from_hex(bad).is_err());
    }

    #[test]
    fn test_from_bytes_roundtrip() {
        let bytes = [0xA9, 0x4A, 0x8F, 0xE5, 0xCC, 0xB1, 0x9B, 0xA6, 0x1C, 0x4C,
                     0x08, 0x73, 0xD3, 0x91, 0xE9, 0x87, 0x98, 0x2F, 0xBB, 0xD3];
        let oid = Oid::from_bytes(bytes);
        assert_eq!(oid.as_bytes(), &bytes);
        assert_eq!(oid.to_hex(), "a94a8fe5ccb19ba61c4c0873d391e987982fbbd3");
    }

    #[test]
    fn test_display_short_hash() {
        let hex = "a94a8fe5ccb19ba61c4c0873d391e987982fbbd3";
        let oid = Oid::from_hex(hex).unwrap();
        assert_eq!(format!("{oid}"), "a94a8fe5ccb1");
    }

    #[test]
    fn test_ord() {
        let a = Oid::from_hex("0000000000000000000000000000000000000001").unwrap();
        let b = Oid::from_hex("0000000000000000000000000000000000000002").unwrap();
        assert!(a < b);
    }
}
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `cargo test raw::oid --no-run 2>&1 | head -20`
Expected: compile errors (struct/functions not defined)

- [ ] **Step 5: Implement OID**

```rust
use std::fmt;
use super::error::GitError;

#[derive(Clone, Copy, PartialEq, Eq, Hash, PartialOrd, Ord)]
pub struct Oid([u8; 20]);

impl Oid {
    pub const ZERO: Oid = Oid([0; 20]);

    pub fn from_hex(hex: &str) -> Result<Self, GitError> {
        if hex.len() != 40 {
            return Err(GitError::CorruptObject {
                path: String::new(),
                detail: format!("invalid OID hex length: {}", hex.len()),
            });
        }
        let mut bytes = [0u8; 20];
        for i in 0..20 {
            bytes[i] = u8::from_str_radix(&hex[i * 2..i * 2 + 2], 16).map_err(|_| {
                GitError::CorruptObject {
                    path: String::new(),
                    detail: format!("invalid hex in OID: {hex}"),
                }
            })?;
        }
        Ok(Oid(bytes))
    }

    pub fn from_bytes(bytes: [u8; 20]) -> Self {
        Oid(bytes)
    }

    pub fn as_bytes(&self) -> &[u8; 20] {
        &self.0
    }

    pub fn to_hex(&self) -> String {
        let mut s = String::with_capacity(40);
        for b in &self.0 {
            s.push_str(&format!("{b:02x}"));
        }
        s
    }

    /// Read 20 raw bytes from a slice (e.g., tree entry, pack index).
    pub fn from_slice(data: &[u8]) -> Result<Self, GitError> {
        if data.len() < 20 {
            return Err(GitError::CorruptObject {
                path: String::new(),
                detail: "OID slice too short".into(),
            });
        }
        let mut bytes = [0u8; 20];
        bytes.copy_from_slice(&data[..20]);
        Ok(Oid(bytes))
    }
}

/// Display prints short hash (first 12 hex chars).
impl fmt::Display for Oid {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        for b in &self.0[..6] {
            write!(f, "{b:02x}")?;
        }
        Ok(())
    }
}

impl fmt::Debug for Oid {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "Oid({})", self.to_hex())
    }
}
```

- [ ] **Step 6: Run tests**

Run: `cargo test raw::oid -- --nocapture`
Expected: all 5 tests pass

- [ ] **Step 7: Run clippy**

Run: `cargo clippy --all-features -- -D warnings 2>&1 | tail -5`
Expected: no warnings

- [ ] **Step 8: Commit**

```bash
git add src/git/raw/ src/git/mod.rs
git commit -m "feat(git/raw): scaffold module with error type and OID"
```

---

## Task 2: SHA-1

**Files:**
- Create: `src/git/raw/sha1.rs`
- Modify: `src/git/raw/mod.rs` (add `pub mod sha1;`)

- [ ] **Step 1: Write failing tests**

Use RFC 3174 test vectors:
```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_empty() {
        let hash = sha1(b"");
        assert_eq!(
            hex(&hash),
            "da39a3ee5e6b4b0d3255bfef95601890afd80709"
        );
    }

    #[test]
    fn test_abc() {
        let hash = sha1(b"abc");
        assert_eq!(
            hex(&hash),
            "a9993e364706816aba3e25717850c26c9cd0d89d"
        );
    }

    #[test]
    fn test_long() {
        let hash = sha1(b"abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq");
        assert_eq!(
            hex(&hash),
            "84983e441c3bd26ebaae4aa1f95129e5e54670f1"
        );
    }

    #[test]
    fn test_million_a() {
        let data = vec![b'a'; 1_000_000];
        let hash = sha1(&data);
        assert_eq!(
            hex(&hash),
            "34aa973cd4c4daa4f61eeb2bdbad27316534016f"
        );
    }

    #[test]
    fn test_streaming() {
        let mut hasher = Sha1::new();
        hasher.update(b"abc");
        hasher.update(b"dbcdecdefdefg");
        hasher.update(b"efghfghighijhijkijkljklmklmnlmnomnopnopq");
        let hash = hasher.finalize();
        assert_eq!(
            hex(&hash),
            "84983e441c3bd26ebaae4aa1f95129e5e54670f1"
        );
    }

    #[test]
    fn test_git_blob_hash() {
        // git hash-object computes: SHA1("blob 5\0hello")
        let content = b"hello";
        let header = format!("blob {}\0", content.len());
        let mut hasher = Sha1::new();
        hasher.update(header.as_bytes());
        hasher.update(content);
        let hash = hasher.finalize();
        assert_eq!(
            hex(&hash),
            "ce013625030ba8dba906f756967f9e9ca394464a"
        );
    }

    fn hex(bytes: &[u8; 20]) -> String {
        bytes.iter().map(|b| format!("{b:02x}")).collect()
    }
}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cargo test raw::sha1 --no-run 2>&1 | head -10`
Expected: compile error

- [ ] **Step 3: Implement SHA-1**

Implement RFC 3174 in `src/git/raw/sha1.rs`. Core components:
- Constants `K0..K3` and initial hash values `H0..H4`
- `Sha1` struct with `[u32; 5]` state, `[u8; 64]` block buffer, byte count
- `update(&mut self, data: &[u8])`: fill buffer, process complete 64-byte blocks
- `finalize(self) -> [u8; 20]`: pad (0x80, zeros, 8-byte BE bit length), process final block, emit state as BE bytes
- `transform(&mut self, block: &[u8; 64])`: 80-round transform with `W[t]` message schedule expansion
- `pub fn sha1(data: &[u8]) -> [u8; 20]`: convenience wrapper

Reference the spec: "80-round block transform with 5 state words. Padding: append 0x80, zero-pad to 56 mod 64, append 8-byte big-endian bit length."

- [ ] **Step 4: Run tests**

Run: `cargo test raw::sha1 -- --nocapture`
Expected: all 6 tests pass

- [ ] **Step 5: Commit**

```bash
git add src/git/raw/sha1.rs src/git/raw/mod.rs
git commit -m "feat(git/raw): add SHA-1 implementation (RFC 3174)"
```

---

## Task 3: DEFLATE decompressor

**Files:**
- Create: `src/git/raw/deflate.rs`
- Modify: `src/git/raw/mod.rs` (add `pub mod deflate;`)

This is the largest and riskiest single component (~1,000 lines). Build incrementally: bit reader -> Huffman tables -> stored blocks -> fixed Huffman -> dynamic Huffman -> zlib framing.

- [ ] **Step 1: Write failing tests for bit reader and stored blocks**

```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_stored_block() {
        // Zlib-compressed "hello" (stored block, no compression)
        // Created with: python3 -c "import zlib; print(list(zlib.compress(b'hello', 0)))"
        let compressed = [0x78, 0x01, 0x01, 0x05, 0x00, 0xfa, 0xff,
                          0x68, 0x65, 0x6c, 0x6c, 0x6f, 0x06, 0x2c, 0x02, 0x15];
        let result = inflate_zlib(&compressed).unwrap();
        assert_eq!(&result, b"hello");
    }

    #[test]
    fn test_fixed_huffman() {
        // Zlib-compressed "hello" with default compression
        // Created with: python3 -c "import zlib; print(list(zlib.compress(b'hello')))"
        let compressed = [0x78, 0x9c, 0xcb, 0x48, 0xcd, 0xc9, 0xc9, 0x07, 0x00,
                          0x06, 0x2c, 0x02, 0x15];
        let result = inflate_zlib(&compressed).unwrap();
        assert_eq!(&result, b"hello");
    }

    #[test]
    fn test_empty() {
        // Zlib-compressed empty bytes
        let compressed = [0x78, 0x9c, 0x03, 0x00, 0x00, 0x00, 0x00, 0x01];
        let result = inflate_zlib(&compressed).unwrap();
        assert!(result.is_empty());
    }

    #[test]
    fn test_repeated_data() {
        // Data with LZ77 back-references (length > distance overlap case)
        let original = b"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
        let compressed = compress_with_python(original);
        let result = inflate_zlib(&compressed).unwrap();
        assert_eq!(&result, original);
    }

    #[test]
    fn test_real_git_object() {
        // Read a real loose object from this repo's .git/ and decompress it
        let git_dir = crate::git::raw::tests::find_repo_git_dir();
        let objects_dir = git_dir.join("objects");
        let mut found = false;
        for entry in std::fs::read_dir(&objects_dir).unwrap() {
            let entry = entry.unwrap();
            let name = entry.file_name().to_string_lossy().to_string();
            if name.len() == 2 && name != "pa" && name != "in" {
                let subdir = objects_dir.join(&name);
                if let Ok(files) = std::fs::read_dir(&subdir) {
                    for file in files {
                        let file = file.unwrap();
                        let data = std::fs::read(file.path()).unwrap();
                        let decompressed = inflate_zlib(&data).unwrap();
                        // Must start with "blob ", "tree ", "commit ", or "tag "
                        let header = std::str::from_utf8(&decompressed[..6]).unwrap_or("");
                        assert!(
                            header.starts_with("blob ")
                                || header.starts_with("tree ")
                                || header.starts_with("commit")
                                || header.starts_with("tag "),
                            "unexpected object header: {header:?}"
                        );
                        found = true;
                        return; // one object is enough
                    }
                }
            }
        }
        assert!(found, "no loose objects found in repo");
    }

    /// Compress data with Python zlib (test helper). Falls back to skip if python3 unavailable.
    fn compress_with_python(data: &[u8]) -> Vec<u8> {
        use std::process::Command;
        let hex: String = data.iter().map(|b| format!("{b:02x}")).collect();
        let output = Command::new("python3")
            .args(["-c", &format!(
                "import zlib,sys; sys.stdout.buffer.write(zlib.compress(bytes.fromhex('{}')))",
                hex
            )])
            .output()
            .expect("python3 required for deflate tests");
        assert!(output.status.success(), "python3 zlib compress failed");
        output.stdout
    }
}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cargo test raw::deflate --no-run 2>&1 | head -10`
Expected: compile error

- [ ] **Step 3: Implement DEFLATE decompressor**

Build in `src/git/raw/deflate.rs`. Implementation order:

1. **BitReader struct** (~80 lines): `bits: u64` buffer, `nbits: u8` count, `pos: usize` into input slice. Methods: `refill()`, `peek(n)`, `consume(n)`, `read_bits(n)`, `read_byte()`, `align_to_byte()`.

2. **Huffman table** (~100 lines): `struct HuffmanTable { symbols: Vec<u16>, counts: [u16; 16], offsets: [u16; 16] }`. Build from code lengths array. Decode: read max code length bits, search counts/offsets. For performance, add a 9-bit primary lookup table.

3. **Length/distance extra bits tables** (~40 lines): Static arrays for length codes 257-285 (base length + extra bits) and distance codes 0-29 (base distance + extra bits).

4. **Stored block** (~20 lines): Align to byte, read LEN/NLEN u16 LE, copy LEN bytes.

5. **Fixed Huffman** (~30 lines): Build hardcoded tables per RFC 1951 section 3.2.6 (0-143: 8-bit, 144-255: 9-bit, 256-279: 7-bit, 280-287: 8-bit; distances: fixed 5-bit).

6. **Dynamic Huffman** (~200 lines): Read HLIT, HDIST, HCLEN. Build code-length Huffman table from HCLEN code lengths in scrambled order `[16,17,18,0,8,7,9,6,10,5,11,4,12,3,13,2,14,1,15]`. Decode HLIT+HDIST code lengths handling repeat codes (16=repeat prev, 17=repeat 0 x3-10, 18=repeat 0 x11-138). Build lit/len and distance tables.

7. **Main decode loop** (~100 lines): Read symbol from lit/len table. If 0-255: literal byte. If 256: end of block. If 257-285: read extra length bits, read distance code from distance table, read extra distance bits, copy from output buffer. Handle overlapping copies (length > distance) byte-by-byte.

8. **Zlib framing** (~15 lines): `pub fn inflate_zlib(data: &[u8]) -> Result<Vec<u8>, GitError>` — verify CMF byte (must be deflate method 8), skip FLG byte, inflate raw deflate stream, skip trailing 4-byte Adler-32.

9. **Raw inflate** (~10 lines): `pub fn inflate_raw(data: &[u8]) -> Result<Vec<u8>, GitError>` — for packfile entries that store raw deflate without zlib header.

Key edge cases per spec:
- Dynamic Huffman meta-alphabet scrambled order
- Overlapping LZ77 copies (copy byte-by-byte when length > distance)
- Return `GitError::DecompressError` on any malformed input

- [ ] **Step 4: Run tests**

Run: `cargo test raw::deflate -- --nocapture`
Expected: all 5 tests pass

- [ ] **Step 5: Fuzz test against Python zlib**

Add a property test that generates random data, compresses with Python zlib, decompresses with our inflate, asserts equality:
```rust
#[test]
fn test_fuzz_random_data() {
    use std::process::Command;
    for size in [0, 1, 10, 100, 1000, 10000, 50000] {
        let data: Vec<u8> = (0..size).map(|i| (i % 256) as u8).collect();
        let compressed = compress_with_python(&data);
        let result = inflate_zlib(&compressed).unwrap();
        assert_eq!(result, data, "mismatch at size {size}");
    }
}
```

- [ ] **Step 6: Run all tests including fuzz**

Run: `cargo test raw::deflate -- --nocapture`
Expected: all 6 tests pass

- [ ] **Step 7: Commit**

```bash
git add src/git/raw/deflate.rs src/git/raw/mod.rs
git commit -m "feat(git/raw): add DEFLATE decompressor (RFC 1951)"
```

---

## Task 4: Loose object reader

**Files:**
- Create: `src/git/raw/object.rs`
- Modify: `src/git/raw/mod.rs`

- [ ] **Step 1: Write failing tests**

```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_parse_object_header_blob() {
        let data = b"blob 5\0hello";
        let (obj_type, content) = parse_object_data(data).unwrap();
        assert_eq!(obj_type, ObjectType::Blob);
        assert_eq!(content, b"hello");
    }

    #[test]
    fn test_parse_object_header_commit() {
        let data = b"commit 11\0tree abcdef";
        let (obj_type, content) = parse_object_data(data).unwrap();
        assert_eq!(obj_type, ObjectType::Commit);
        assert_eq!(content, b"tree abcdef");
    }

    #[test]
    fn test_read_real_loose_object() {
        let git_dir = crate::git::raw::tests::find_repo_git_dir();
        let objects_dir = git_dir.join("objects");
        // Find any loose object
        for entry in std::fs::read_dir(&objects_dir).unwrap().flatten() {
            let name = entry.file_name().to_string_lossy().to_string();
            if name.len() == 2 && entry.path().is_dir() && name != "pa" && name != "in" {
                for file in std::fs::read_dir(entry.path()).unwrap().flatten() {
                    let fname = file.file_name().to_string_lossy().to_string();
                    let hex = format!("{name}{fname}");
                    if hex.len() != 40 { continue; }
                    let oid = Oid::from_hex(&hex).unwrap();
                    let (obj_type, _content) = read_loose_object(&objects_dir, &oid).unwrap();
                    assert!(matches!(
                        obj_type,
                        ObjectType::Blob | ObjectType::Tree | ObjectType::Commit | ObjectType::Tag
                    ));
                    return;
                }
            }
        }
        panic!("no loose objects found");
    }
}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cargo test raw::object --no-run 2>&1 | head -10`

- [ ] **Step 3: Implement loose object reader**

```rust
use std::path::Path;
use super::deflate::inflate_zlib;
use super::error::GitError;
use super::oid::Oid;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ObjectType {
    Commit,
    Tree,
    Blob,
    Tag,
}

impl ObjectType {
    pub fn parse(s: &str) -> Result<Self, GitError> {
        match s {
            "commit" => Ok(Self::Commit),
            "tree" => Ok(Self::Tree),
            "blob" => Ok(Self::Blob),
            "tag" => Ok(Self::Tag),
            _ => Err(GitError::CorruptObject {
                path: String::new(),
                detail: format!("unknown object type: {s}"),
            }),
        }
    }

    pub fn type_id(&self) -> u8 {
        match self {
            Self::Commit => 1,
            Self::Tree => 2,
            Self::Blob => 3,
            Self::Tag => 4,
        }
    }
}

/// Parse the "type size\0content" format from decompressed object data.
pub fn parse_object_data(data: &[u8]) -> Result<(ObjectType, &[u8]), GitError> {
    let nul_pos = data.iter().position(|&b| b == 0).ok_or_else(|| {
        GitError::CorruptObject {
            path: String::new(),
            detail: "no NUL in object header".into(),
        }
    })?;
    let header = std::str::from_utf8(&data[..nul_pos]).map_err(|_| GitError::CorruptObject {
        path: String::new(),
        detail: "non-UTF8 object header".into(),
    })?;
    let space_pos = header.find(' ').ok_or_else(|| GitError::CorruptObject {
        path: String::new(),
        detail: "no space in object header".into(),
    })?;
    let obj_type = ObjectType::parse(&header[..space_pos])?;
    let content = &data[nul_pos + 1..];
    Ok((obj_type, content))
}

/// Read and decompress a loose object from the objects directory.
pub fn read_loose_object(objects_dir: &Path, oid: &Oid) -> Result<(ObjectType, Vec<u8>), GitError> {
    let hex = oid.to_hex();
    let path = objects_dir.join(&hex[..2]).join(&hex[2..]);
    let compressed = std::fs::read(&path).map_err(|e| {
        if e.kind() == std::io::ErrorKind::NotFound {
            GitError::ObjectNotFound(*oid)
        } else {
            GitError::Io(e)
        }
    })?;
    let decompressed = inflate_zlib(&compressed)?;
    let (obj_type, content) = parse_object_data(&decompressed)?;
    Ok((obj_type, content.to_vec()))
}
```

- [ ] **Step 4: Run tests**

Run: `cargo test raw::object -- --nocapture`
Expected: all 3 tests pass

- [ ] **Step 5: Commit**

```bash
git add src/git/raw/object.rs src/git/raw/mod.rs
git commit -m "feat(git/raw): add loose object reader"
```

---

## Task 5: Pack index v2 reader

**Files:**
- Create: `src/git/raw/pack_index.rs`
- Modify: `src/git/raw/mod.rs`

- [ ] **Step 1: Write failing tests**

```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_read_real_pack_index() {
        let git_dir = crate::git::raw::tests::find_repo_git_dir();
        let pack_dir = git_dir.join("objects/pack");
        if !pack_dir.exists() { return; } // skip if no packfiles

        for entry in std::fs::read_dir(&pack_dir).unwrap().flatten() {
            let name = entry.file_name().to_string_lossy().to_string();
            if name.ends_with(".idx") {
                let idx = PackIndex::open(entry.path()).unwrap();
                assert!(idx.object_count() > 0);

                // Verify first OID can be found
                let first_oid = idx.oid_at(0).unwrap();
                let offset = idx.find(&first_oid);
                assert!(offset.is_some(), "first OID not found in index");
                return;
            }
        }
    }

    #[test]
    fn test_binary_search_correctness() {
        let git_dir = crate::git::raw::tests::find_repo_git_dir();
        let pack_dir = git_dir.join("objects/pack");
        if !pack_dir.exists() { return; }

        for entry in std::fs::read_dir(&pack_dir).unwrap().flatten() {
            let name = entry.file_name().to_string_lossy().to_string();
            if name.ends_with(".idx") {
                let idx = PackIndex::open(entry.path()).unwrap();
                let count = idx.object_count();
                // Check a few OIDs from the middle
                for i in [0, count / 4, count / 2, count - 1].iter().copied() {
                    if i >= count { continue; }
                    let oid = idx.oid_at(i).unwrap();
                    assert!(idx.find(&oid).is_some(), "OID at index {i} not found");
                }
                // Non-existent OID
                assert!(idx.find(&Oid::ZERO).is_none());
                return;
            }
        }
    }
}
```

- [ ] **Step 2: Run tests to verify they fail**

- [ ] **Step 3: Implement pack index v2 reader**

Key structures in `src/git/raw/pack_index.rs`:
- `PackIndex` struct: owns mmapped bytes, stores `object_count: u32`
- `open(path)`: mmap the file, verify magic `0xff744f63` + version 2, read `fanout[255]` for count
- `find(&self, oid: &Oid) -> Option<u64>`: use fanout to narrow binary search range, search OID table, read offset (handling 64-bit MSB flag)
- `oid_at(&self, index: u32) -> Option<Oid>`: direct access to OID table at index
- Helper: `read_u32_be(data, offset)`, `read_u64_be(data, offset)`

Per spec: 8-byte header, 256x u32 fanout, N x 20-byte OIDs (sorted), N x u32 CRC32 (skip), N x u32 offsets (MSB = large offset flag), optional Mx u64 large offsets.

- [ ] **Step 4: Run tests**

Run: `cargo test raw::pack_index -- --nocapture`
Expected: both tests pass

- [ ] **Step 5: Commit**

```bash
git add src/git/raw/pack_index.rs src/git/raw/mod.rs
git commit -m "feat(git/raw): add pack index v2 reader"
```

---

## Task 6: Packfile reader + delta reconstruction

**Files:**
- Create: `src/git/raw/pack.rs`
- Modify: `src/git/raw/mod.rs`

- [ ] **Step 1: Write failing tests**

```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_read_object_from_pack() {
        let git_dir = crate::git::raw::tests::find_repo_git_dir();
        let pack_dir = git_dir.join("objects/pack");
        if !pack_dir.exists() { return; }

        for entry in std::fs::read_dir(&pack_dir).unwrap().flatten() {
            let name = entry.file_name().to_string_lossy().to_string();
            if name.ends_with(".idx") {
                let pack_path = entry.path().with_extension("pack");
                let idx = PackIndex::open(entry.path()).unwrap();
                let pack = Packfile::open(&pack_path).unwrap();

                // Read first object
                let oid = idx.oid_at(0).unwrap();
                let offset = idx.find(&oid).unwrap();
                let (obj_type, data) = pack.read_object_at(offset, &idx).unwrap();
                assert!(matches!(
                    obj_type,
                    ObjectType::Commit | ObjectType::Tree | ObjectType::Blob | ObjectType::Tag
                ));
                assert!(!data.is_empty());
                return;
            }
        }
    }

    #[test]
    fn test_pack_header_validation() {
        let bad_data = b"NOTPACK";
        let result = Packfile::from_bytes(bad_data);
        assert!(result.is_err());
    }

    #[test]
    fn test_vlq_size_decode() {
        // Type 1 (commit), size 10: byte = (0 << 7) | (1 << 4) | 10 = 0x1A
        let data = [0x1A];
        let (obj_type, size, consumed) = parse_object_header(&data);
        assert_eq!(obj_type, 1);
        assert_eq!(size, 10);
        assert_eq!(consumed, 1);
    }
}
```

- [ ] **Step 2: Run tests to verify they fail**

- [ ] **Step 3: Implement packfile reader**

In `src/git/raw/pack.rs`:

1. **`Packfile` struct**: mmapped bytes, verify `PACK` header + version 2 (reject v3 explicitly)

2. **`parse_object_header(data: &[u8]) -> (u8, u64, usize)`**: Parse VLQ type+size. First byte: type = `(byte >> 4) & 0x07`, size bits 0..=3. Continuation bytes add 7 bits each.

3. **`read_ofs_delta_offset(data: &[u8]) -> (u64, usize)`**: Non-standard VLQ with +1 correction: `result = byte & 0x7F; while MSB { result = (result + 1) << 7; result |= next & 0x7F; }`

4. **`apply_delta(base: &[u8], delta: &[u8]) -> Result<Vec<u8>, GitError>`**: Parse base_size + result_size VLQs. Process instruction stream: copy (MSB=1, offset/size byte selection via bits) or insert (MSB=0, literal bytes). Handle `size=0 -> 0x10000`.

5. **`read_object_at(&self, offset: u64, idx: &PackIndex) -> Result<(ObjectType, Vec<u8>), GitError>`**: Read header, switch on type. Types 1-4: inflate raw. Type 6 (OFS_DELTA): read base offset, recurse with iterative stack. Type 7 (REF_DELTA): read 20-byte OID, find in index, recurse. Cap delta chain at 50.

- [ ] **Step 4: Run tests**

Run: `cargo test raw::pack -- --nocapture`
Expected: all 3 tests pass

- [ ] **Step 5: Commit**

```bash
git add src/git/raw/pack.rs src/git/raw/mod.rs
git commit -m "feat(git/raw): add packfile reader with delta reconstruction"
```

---

## Task 7: Commit and tree parsers

**Files:**
- Create: `src/git/raw/commit.rs`
- Create: `src/git/raw/tree.rs`
- Modify: `src/git/raw/mod.rs`

- [ ] **Step 1: Write failing tests for commit parser**

```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_parse_simple_commit() {
        let data = b"tree a94a8fe5ccb19ba61c4c0873d391e987982fbbd3\n\
parent b94a8fe5ccb19ba61c4c0873d391e987982fbbd3\n\
author Test User <test@example.com> 1700000000 +0000\n\
committer Test User <test@example.com> 1700000000 +0000\n\
\n\
Initial commit\n\
\n\
Some details";
        let commit = RawCommit::parse(data).unwrap();
        assert_eq!(commit.tree_oid.to_hex(), "a94a8fe5ccb19ba61c4c0873d391e987982fbbd3");
        assert_eq!(commit.parents.len(), 1);
        assert_eq!(commit.author_name, "Test User");
        assert_eq!(commit.author_email, "test@example.com");
        assert_eq!(commit.author_time, 1700000000);
        assert_eq!(commit.message, "Initial commit");
    }

    #[test]
    fn test_parse_root_commit() {
        let data = b"tree a94a8fe5ccb19ba61c4c0873d391e987982fbbd3\n\
author Test <t@e.com> 1700000000 +0000\n\
committer Test <t@e.com> 1700000000 +0000\n\
\n\
root";
        let commit = RawCommit::parse(data).unwrap();
        assert!(commit.parents.is_empty());
    }

    #[test]
    fn test_parse_merge_commit() {
        let data = b"tree a94a8fe5ccb19ba61c4c0873d391e987982fbbd3\n\
parent 1111111111111111111111111111111111111111\n\
parent 2222222222222222222222222222222222222222\n\
author Test <t@e.com> 1700000000 +0000\n\
committer Test <t@e.com> 1700000000 +0000\n\
\n\
merge";
        let commit = RawCommit::parse(data).unwrap();
        assert_eq!(commit.parents.len(), 2);
    }

    #[test]
    fn test_parse_gpgsig_commit() {
        let data = b"tree a94a8fe5ccb19ba61c4c0873d391e987982fbbd3\n\
author Test <t@e.com> 1700000000 +0000\n\
committer Test <t@e.com> 1700000000 +0000\n\
gpgsig -----BEGIN PGP SIGNATURE-----\n \n wsBcBAAB\n -----END PGP SIGNATURE-----\n\
\n\
signed commit";
        let commit = RawCommit::parse(data).unwrap();
        assert_eq!(commit.message, "signed commit");
    }
}
```

- [ ] **Step 2: Write failing tests for tree parser**

```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_parse_tree_entry() {
        // Build a tree entry: "100644 hello.txt\0" + 20 bytes OID
        let mut data = Vec::new();
        data.extend_from_slice(b"100644 hello.txt\0");
        data.extend_from_slice(&[0xAA; 20]);
        data.extend_from_slice(b"40000 subdir\0");
        data.extend_from_slice(&[0xBB; 20]);

        let entries = parse_tree(&data).unwrap();
        assert_eq!(entries.len(), 2);
        assert_eq!(entries[0].mode, 0o100644);
        assert_eq!(entries[0].name, "hello.txt");
        assert_eq!(entries[1].mode, 0o40000);
        assert_eq!(entries[1].name, "subdir");
    }

    #[test]
    fn test_parse_real_tree() {
        // Read HEAD tree from this repo
        let git_dir = crate::git::raw::tests::find_repo_git_dir();
        let head = std::fs::read_to_string(git_dir.join("HEAD")).unwrap();
        // Just verify we can parse a real tree without error
        // (full integration tested in repo.rs)
    }
}
```

- [ ] **Step 3: Implement commit parser**

`RawCommit::parse(data: &[u8])`: Split on `\n\n` for headers/body. Parse `tree`, `parent` (0+), `author`, `committer` lines. Skip multi-line headers (gpgsig/mergetag) where continuation lines start with space. Parse author: split on `<` and `>` for name/email, parse timestamp + tz offset.

- [ ] **Step 4: Implement tree parser**

`parse_tree(data: &[u8]) -> Vec<TreeEntry>`: Walk bytes, parse mode (octal ASCII until space), name (until NUL), 20-byte binary OID. Repeat until end.

Also implement `walk_tree()` for recursive traversal yielding `(path_prefix, entry)`.

- [ ] **Step 5: Run all tests**

Run: `cargo test raw::commit raw::tree -- --nocapture`
Expected: all tests pass

- [ ] **Step 6: Commit**

```bash
git add src/git/raw/commit.rs src/git/raw/tree.rs src/git/raw/mod.rs
git commit -m "feat(git/raw): add commit and tree parsers"
```

---

## Task 8: RawRepo (discovery, refs, object lookup, LRU cache)

**Files:**
- Create: `src/git/raw/repo.rs`
- Modify: `src/git/raw/mod.rs`

This is the central type that ties all storage layers together.

- [ ] **Step 1: Write failing tests**

```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_discover_repo() {
        let repo = RawRepo::discover(std::path::Path::new(env!("CARGO_MANIFEST_DIR"))).unwrap();
        assert!(repo.workdir().exists());
    }

    #[test]
    fn test_resolve_head() {
        let repo = RawRepo::discover(std::path::Path::new(env!("CARGO_MANIFEST_DIR"))).unwrap();
        let head_oid = repo.resolve_head().unwrap();
        assert_ne!(head_oid, Oid::ZERO);
    }

    #[test]
    fn test_find_commit() {
        let repo = RawRepo::discover(std::path::Path::new(env!("CARGO_MANIFEST_DIR"))).unwrap();
        let head_oid = repo.resolve_head().unwrap();
        let commit = repo.find_commit(&head_oid).unwrap();
        assert_ne!(commit.tree_oid, Oid::ZERO);
        assert!(!commit.author_name.is_empty());
    }

    #[test]
    fn test_find_tree() {
        let repo = RawRepo::discover(std::path::Path::new(env!("CARGO_MANIFEST_DIR"))).unwrap();
        let head_oid = repo.resolve_head().unwrap();
        let commit = repo.find_commit(&head_oid).unwrap();
        let entries = repo.find_tree(&commit.tree_oid).unwrap();
        assert!(!entries.is_empty());
    }

    #[test]
    fn test_head_tree() {
        let repo = RawRepo::discover(std::path::Path::new(env!("CARGO_MANIFEST_DIR"))).unwrap();
        let (_oid, entries) = repo.head_tree().unwrap();
        assert!(!entries.is_empty());
    }

    #[test]
    fn test_not_a_repo() {
        let result = RawRepo::discover(std::path::Path::new("/tmp"));
        assert!(result.is_err());
    }

    #[test]
    fn test_find_root_commit() {
        let repo = RawRepo::discover(std::path::Path::new(env!("CARGO_MANIFEST_DIR"))).unwrap();
        let root = repo.find_root_commit().unwrap();
        let commit = repo.find_commit(&root).unwrap();
        assert!(commit.parents.is_empty());
    }

    #[test]
    fn test_detached_head() {
        // Create a repo with detached HEAD (bare OID in HEAD file)
        let dir = tempfile::tempdir().unwrap();
        let run = |args: &[&str]| {
            std::process::Command::new("git")
                .args(args).current_dir(dir.path())
                .env("GIT_AUTHOR_NAME", "Test").env("GIT_AUTHOR_EMAIL", "t@t.com")
                .env("GIT_COMMITTER_NAME", "Test").env("GIT_COMMITTER_EMAIL", "t@t.com")
                .output().unwrap()
        };
        run(&["init"]);
        run(&["config", "user.name", "Test"]);
        run(&["config", "user.email", "t@t.com"]);
        std::fs::write(dir.path().join("f.txt"), "x").unwrap();
        run(&["add", "."]);
        run(&["commit", "-m", "init"]);
        run(&["checkout", "--detach", "HEAD"]);

        let repo = RawRepo::discover(dir.path()).unwrap();
        let head = repo.resolve_head().unwrap();
        assert_ne!(head, Oid::ZERO);
    }

    #[test]
    fn test_empty_repo() {
        let dir = tempfile::tempdir().unwrap();
        std::process::Command::new("git").args(["init"]).current_dir(dir.path()).output().unwrap();
        let repo = RawRepo::discover(dir.path()).unwrap();
        // HEAD exists but points to unborn branch
        assert!(repo.resolve_head().is_err());
    }

    #[test]
    fn test_sha256_detection() {
        // If a repo has extensions.objectFormat = sha256, we should error clearly
        let dir = tempfile::tempdir().unwrap();
        std::process::Command::new("git").args(["init"]).current_dir(dir.path()).output().unwrap();
        let config_path = dir.path().join(".git/config");
        let mut config = std::fs::read_to_string(&config_path).unwrap();
        config.push_str("\n[extensions]\n\tobjectFormat = sha256\n");
        std::fs::write(&config_path, config).unwrap();
        let result = RawRepo::discover(dir.path());
        assert!(result.is_err(), "should reject SHA-256 repos");
    }
}
```

- [ ] **Step 2: Run tests to verify they fail**

- [ ] **Step 3: Implement RawRepo**

`src/git/raw/repo.rs` (~300 lines):

1. **`RawRepo` struct**: `git_dir: PathBuf`, `common_dir: PathBuf`, `workdir: PathBuf`, `pack_stores: Vec<(PackIndex, Packfile)>`, `packed_refs: Vec<(String, Oid)>`, `shallow_oids: HashSet<Oid>`, `cache: Mutex<LruCache>` (simple custom LRU: `Vec<(Oid, ObjectType, Vec<u8>)>` with byte budget)

2. **`discover(path: &Path)`**: Walk up looking for `.git`. If file, read `gitdir:`. Read `commondir` for worktrees. Parse `.git/shallow`. Load pack indices from `objects/pack/*.idx`. Parse packed-refs. Load alternates recursively.

3. **`resolve_ref(&self, refname: &str) -> Result<Oid>`**: Check loose ref file, then packed-refs. Handle symbolic refs (`ref: ...`) with depth limit.

4. **`resolve_head(&self) -> Result<Oid>`**: Read HEAD, resolve.

5. **`find_object(&self, oid: &Oid) -> Result<(ObjectType, Vec<u8>)>`**: Check cache -> loose -> pack stores -> alternates. Cache on hit (trees/commits only).

6. **`find_commit(&self, oid: &Oid) -> Result<RawCommit>`**: find_object + parse. Peel through tags.

7. **`find_tree(&self, oid: &Oid) -> Result<Vec<TreeEntry>>`**: find_object + parse.

8. **`find_blob(&self, oid: &Oid) -> Result<Vec<u8>>`**: find_object, assert blob.

9. **`head_tree(&self) -> Result<(Oid, Vec<TreeEntry>)>`**: resolve HEAD -> find_commit -> find_tree.

10. **`find_root_commit(&self) -> Result<Oid>`**: Walk first-parent chain from HEAD to root.

11. **`is_shallow(&self, oid: &Oid) -> bool`**: Check shallow set.

- [ ] **Step 4: Run tests**

Run: `cargo test raw::repo -- --nocapture`
Expected: all 7 tests pass

- [ ] **Step 5: Commit**

```bash
git add src/git/raw/repo.rs src/git/raw/mod.rs
git commit -m "feat(git/raw): add RawRepo with discovery, refs, and object lookup"
```

---

## Task 9: Revwalk

**Files:**
- Create: `src/git/raw/revwalk.rs`
- Modify: `src/git/raw/mod.rs`

- [ ] **Step 1: Write failing tests**

```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_revwalk_head() {
        let repo = RawRepo::discover(std::path::Path::new(env!("CARGO_MANIFEST_DIR"))).unwrap();
        let mut walk = RevWalk::new(&repo);
        walk.push_head().unwrap();
        let first = walk.next().unwrap().unwrap();
        assert_eq!(first, repo.resolve_head().unwrap());
    }

    #[test]
    fn test_revwalk_time_sorted() {
        let repo = RawRepo::discover(std::path::Path::new(env!("CARGO_MANIFEST_DIR"))).unwrap();
        let mut walk = RevWalk::new(&repo);
        walk.push_head().unwrap();
        let mut prev_time = i64::MAX;
        let mut count = 0;
        while let Some(Ok(oid)) = walk.next() {
            let commit = repo.find_commit(&oid).unwrap();
            assert!(commit.committer_time <= prev_time, "commits not time-sorted (git2 sorts by committer_time)");
            prev_time = commit.committer_time;
            count += 1;
            if count >= 20 { break; }
        }
        assert!(count > 0);
    }

    #[test]
    fn test_revwalk_first_parent() {
        let repo = RawRepo::discover(std::path::Path::new(env!("CARGO_MANIFEST_DIR"))).unwrap();
        let mut walk = RevWalk::new(&repo);
        walk.push_head().unwrap();
        walk.simplify_first_parent();
        let mut count = 0;
        while let Some(Ok(_)) = walk.next() {
            count += 1;
            if count >= 50 { break; }
        }
        assert!(count > 0);
    }
}
```

- [ ] **Step 2: Implement revwalk**

`RevWalk` struct with `BinaryHeap<(i64, Oid)>`, `HashSet<Oid>` seen, `first_parent_only: bool`. **Important**: heap key must be `committer_time` (not `author_time`) to match git2's `Sort::TIME` behavior.

- `push_head()`: resolve HEAD, parse commit for `committer_time`, push
- `simplify_first_parent()`: set flag
- `Iterator::next()`: pop from heap, parse commit, enqueue parents (first only if simplified), skip shallow OIDs
- All parents are checked against `seen` set to avoid duplicates in DAG

- [ ] **Step 3: Run tests**

Run: `cargo test raw::revwalk -- --nocapture`
Expected: all 3 tests pass

- [ ] **Step 4: Commit**

```bash
git add src/git/raw/revwalk.rs src/git/raw/mod.rs
git commit -m "feat(git/raw): add time-sorted revwalk"
```

---

## Task 10: Tree-to-tree diff + Myers diff + stats

**Files:**
- Create: `src/git/raw/diff.rs`
- Modify: `src/git/raw/mod.rs`

- [ ] **Step 1: Write failing tests for Myers diff**

```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_myers_identical() {
        let hunks = diff_blobs(b"hello\nworld\n", b"hello\nworld\n");
        assert!(hunks.is_empty());
    }

    #[test]
    fn test_myers_insertion() {
        let hunks = diff_blobs(b"a\nc\n", b"a\nb\nc\n");
        assert_eq!(hunks.len(), 1);
        assert_eq!(hunks[0].new_start, 2);
        assert_eq!(hunks[0].new_lines, 1);
        assert_eq!(hunks[0].old_lines, 0);
    }

    #[test]
    fn test_myers_deletion() {
        let hunks = diff_blobs(b"a\nb\nc\n", b"a\nc\n");
        assert_eq!(hunks.len(), 1);
        assert_eq!(hunks[0].old_start, 2);
        assert_eq!(hunks[0].old_lines, 1);
        assert_eq!(hunks[0].new_lines, 0);
    }

    #[test]
    fn test_myers_modification() {
        let hunks = diff_blobs(b"a\nb\nc\n", b"a\nB\nc\n");
        assert_eq!(hunks.len(), 1);
        assert_eq!(hunks[0].old_lines, 1);
        assert_eq!(hunks[0].new_lines, 1);
    }

    #[test]
    fn test_myers_empty_to_content() {
        let hunks = diff_blobs(b"", b"a\nb\n");
        assert_eq!(hunks.len(), 1);
        assert_eq!(hunks[0].new_lines, 2);
    }

    #[test]
    fn test_diff_stats() {
        let hunks = diff_blobs(b"a\nb\nc\n", b"a\nB\nc\nd\n");
        let stats = compute_stats(&hunks);
        assert_eq!(stats.insertions, 2); // B + d
        assert_eq!(stats.deletions, 1);  // b
    }
}
```

- [ ] **Step 2: Write failing tests for tree diff**

```rust
    #[test]
    fn test_tree_diff_real_repo() {
        let repo = RawRepo::discover(std::path::Path::new(env!("CARGO_MANIFEST_DIR"))).unwrap();
        let head = repo.resolve_head().unwrap();
        let commit = repo.find_commit(&head).unwrap();
        if let Some(parent_oid) = commit.parents.first() {
            let parent = repo.find_commit(parent_oid).unwrap();
            let deltas = diff_trees(&repo, &parent.tree_oid, &commit.tree_oid, &[]).unwrap();
            // HEAD commit should have some changes (unless empty merge)
            // Just verify it doesn't crash
            for delta in &deltas {
                assert!(!delta.new_path.is_empty());
            }
        }
    }

    #[test]
    fn test_tree_diff_with_pathspec() {
        let repo = RawRepo::discover(std::path::Path::new(env!("CARGO_MANIFEST_DIR"))).unwrap();
        let head = repo.resolve_head().unwrap();
        let commit = repo.find_commit(&head).unwrap();
        if let Some(parent_oid) = commit.parents.first() {
            let parent = repo.find_commit(parent_oid).unwrap();
            let pathspecs = vec!["src/main.rs".to_string()];
            let deltas = diff_trees(&repo, &parent.tree_oid, &commit.tree_oid, &pathspecs).unwrap();
            for delta in &deltas {
                assert_eq!(delta.new_path, "src/main.rs");
            }
        }
    }
```

- [ ] **Step 3: Implement Myers diff**

O(ND) forward algorithm with bail-out at D > max(old, new) / 2. Input: two byte slices split on `\n`. Output: `Vec<DiffHunk>` with zero context, no merging.

- [ ] **Step 4: Implement tree-to-tree diff**

`diff_trees(repo, old_tree_oid, new_tree_oid, pathspecs) -> Result<Vec<DiffDelta>>`:
- Read both trees, merge-walk by name
- Skip mode 160000 (submodule)
- Pathspec pruning: skip subtrees whose prefix doesn't match
- For modified subtrees: recurse
- For modified blobs: emit Modified delta

Also implement `diff_trees_with_hunks()` that additionally runs Myers on modified blobs and returns `Vec<(DiffDelta, Vec<DiffHunk>)>`.

- [ ] **Step 5: Run tests**

Run: `cargo test raw::diff -- --nocapture`
Expected: all 8 tests pass

- [ ] **Step 6: Commit**

```bash
git add src/git/raw/diff.rs src/git/raw/mod.rs
git commit -m "feat(git/raw): add tree-to-tree diff with Myers blob diff"
```

---

## Task 11: Blame algorithm

**Files:**
- Create: `src/git/raw/blame.rs` (the raw algorithm, NOT the existing `src/git/blame.rs`)
- Modify: `src/git/raw/mod.rs`

- [ ] **Step 1: Write failing tests**

```rust
#[cfg(test)]
mod tests {
    use super::*;
    use std::process::Command;

    fn create_test_repo() -> tempfile::TempDir {
        let dir = tempfile::tempdir().unwrap();
        let run = |args: &[&str]| {
            Command::new("git")
                .args(args)
                .current_dir(dir.path())
                .env("GIT_AUTHOR_NAME", "Test")
                .env("GIT_AUTHOR_EMAIL", "test@test.com")
                .env("GIT_COMMITTER_NAME", "Test")
                .env("GIT_COMMITTER_EMAIL", "test@test.com")
                .output()
                .unwrap()
        };
        run(&["init"]);
        run(&["config", "user.name", "Test"]);
        run(&["config", "user.email", "test@test.com"]);

        std::fs::write(dir.path().join("test.txt"), "line1\nline2\nline3\n").unwrap();
        run(&["add", "test.txt"]);
        run(&["commit", "-m", "first"]);

        // Second commit modifies line 2
        std::fs::write(dir.path().join("test.txt"), "line1\nmodified\nline3\n").unwrap();
        run(&["add", "test.txt"]);
        run(&["commit", "-m", "second"]);

        dir
    }

    #[test]
    fn test_blame_basic() {
        let dir = create_test_repo();
        let repo = RawRepo::discover(dir.path()).unwrap();
        let hunks = blame_file(&repo, "test.txt").unwrap();
        assert!(!hunks.is_empty());
        // Line 2 should be blamed to the second commit (most recent)
        // Lines 1 and 3 should be blamed to the first commit
        // Group hunks by commit
        let line2_hunk = hunks.iter().find(|h| {
            h.orig_start_line <= 2 && h.orig_start_line + h.num_lines > 2
        }).expect("no hunk covers line 2");
        let line1_hunk = hunks.iter().find(|h| {
            h.orig_start_line <= 1 && h.orig_start_line + h.num_lines > 1
        }).expect("no hunk covers line 1");
        // Line 2 (modified) should be blamed to a DIFFERENT commit than line 1 (unchanged)
        assert_ne!(line2_hunk.commit, line1_hunk.commit, "modified line should be a different commit");
    }

    #[test]
    fn test_blame_all_lines_covered() {
        let dir = create_test_repo();
        let repo = RawRepo::discover(dir.path()).unwrap();
        let hunks = blame_file(&repo, "test.txt").unwrap();
        let total_lines: u32 = hunks.iter().map(|h| h.num_lines).sum();
        assert_eq!(total_lines, 3);
    }
}
```

- [ ] **Step 2: Implement blame**

`blame_file(repo, file_path) -> Result<Vec<BlameHunk>>`:

1. Resolve HEAD, find file blob OID via tree walk
2. Read file content, split into lines. All lines start as unblamed.
3. Revwalk from HEAD with pathspec filter for this file
4. At each commit: read file blob from commit's tree. If blob OID same as child's, skip. Otherwise Myers diff old vs new.
5. Lines unchanged in diff: pass blame to parent. Lines inserted: assign to this commit, mark settled.
6. Stop when all lines settled or root reached.

Track blame state: `Vec<Option<BlameHunk>>` indexed by line number.

- [ ] **Step 3: Run tests**

Run: `cargo test raw::blame -- --nocapture`
Expected: both tests pass

- [ ] **Step 4: Commit**

```bash
git add src/git/raw/blame.rs src/git/raw/mod.rs
git commit -m "feat(git/raw): add line-level blame algorithm"
```

---

## Task 11b: Finalize `mod.rs` public API surface

**Files:**
- Modify: `src/git/raw/mod.rs`

After all submodules are built, ensure `mod.rs` re-exports the complete public API:

- [ ] **Step 1: Update mod.rs with all modules and re-exports**

```rust
pub mod blame;
pub mod commit;
pub mod deflate;
pub mod diff;
pub mod error;
pub mod object;
pub mod oid;
pub mod pack;
pub mod pack_index;
pub mod repo;
pub mod revwalk;
pub mod sha1;
pub mod tree;

pub use blame::{blame_file, BlameHunk};
pub use commit::RawCommit;
pub use diff::{compute_stats, diff_blobs, diff_trees, diff_trees_with_hunks, DiffDelta, DiffHunk, DiffStats, DeltaStatus};
pub use error::GitError;
pub use object::ObjectType;
pub use oid::Oid;
pub use repo::RawRepo;
pub use revwalk::RevWalk;
pub use tree::{parse_tree, walk_tree, TreeEntry};

#[cfg(test)]
pub(crate) mod tests {
    use std::path::PathBuf;
    pub fn find_repo_git_dir() -> PathBuf {
        let mut dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
        loop {
            let git = dir.join(".git");
            if git.is_dir() { return git; }
            if !dir.pop() { panic!("not in a git repo"); }
        }
    }
}
```

- [ ] **Step 2: Verify compilation**

Run: `cargo check 2>&1 | tail -5`
Expected: compiles clean

- [ ] **Step 3: Commit**

```bash
git add src/git/raw/mod.rs
git commit -m "feat(git/raw): finalize public API surface in mod.rs"
```

---

## Task 12: Golden tests (raw vs git2 parity)

**Files:**
- Create: `tests/raw_git_golden.rs`

Before migrating consumers, verify our raw implementation matches git2 output for the same operations on the real repo.

- [ ] **Step 1: Write golden comparison tests**

```rust
//! Golden tests: verify raw git implementation matches git2 for identical operations.

use repotoire::git::raw::{RawRepo, RevWalk};
use std::path::Path;

fn repo_path() -> &'static Path {
    Path::new(env!("CARGO_MANIFEST_DIR"))
}

#[test]
fn golden_head_oid_matches() {
    let raw_repo = RawRepo::discover(repo_path()).unwrap();
    let raw_head = raw_repo.resolve_head().unwrap();

    let git2_repo = git2::Repository::discover(repo_path()).unwrap();
    let git2_head = git2_repo.head().unwrap().target().unwrap();

    assert_eq!(raw_head.to_hex(), git2_head.to_string());
}

#[test]
fn golden_revwalk_first_20_match() {
    let raw_repo = RawRepo::discover(repo_path()).unwrap();
    let mut raw_walk = RevWalk::new(&raw_repo);
    raw_walk.push_head().unwrap();
    let raw_oids: Vec<String> = std::iter::from_fn(|| raw_walk.next()?.ok())
        .take(20)
        .map(|o| o.to_hex())
        .collect();

    let git2_repo = git2::Repository::discover(repo_path()).unwrap();
    let mut git2_walk = git2_repo.revwalk().unwrap();
    git2_walk.set_sorting(git2::Sort::TIME).unwrap();
    git2_walk.push_head().unwrap();
    let git2_oids: Vec<String> = git2_walk
        .take(20)
        .map(|o| o.unwrap().to_string())
        .collect();

    assert_eq!(raw_oids, git2_oids, "revwalk order mismatch");
}

#[test]
fn golden_root_commit_matches() {
    let raw_repo = RawRepo::discover(repo_path()).unwrap();
    let raw_root = raw_repo.find_root_commit().unwrap();

    let git2_repo = git2::Repository::discover(repo_path()).unwrap();
    let mut git2_walk = git2_repo.revwalk().unwrap();
    git2_walk.set_sorting(git2::Sort::TIME | git2::Sort::REVERSE).unwrap();
    git2_walk.push_head().unwrap();
    let git2_root = git2_walk.next().unwrap().unwrap();

    assert_eq!(raw_root.to_hex(), git2_root.to_string());
}

#[test]
fn golden_tracked_files_match() {
    use repotoire::git::raw::tree::walk_tree;

    let raw_repo = RawRepo::discover(repo_path()).unwrap();
    let (_, tree) = raw_repo.head_tree().unwrap();
    let mut raw_files: Vec<String> = Vec::new();
    walk_tree(&raw_repo, &tree, "", &mut |path, entry| {
        if entry.mode != 0o40000 {
            raw_files.push(format!("{path}{}", entry.name));
        }
    }).unwrap();
    raw_files.sort();

    let git2_repo = git2::Repository::discover(repo_path()).unwrap();
    let head = git2_repo.head().unwrap();
    let tree = head.peel_to_tree().unwrap();
    let mut git2_files = Vec::new();
    tree.walk(git2::TreeWalkMode::PreOrder, |dir, entry| {
        if entry.kind() == Some(git2::ObjectType::Blob) {
            git2_files.push(format!("{dir}{}", entry.name().unwrap_or("")));
        }
        git2::TreeWalkResult::Ok
    }).unwrap();
    git2_files.sort();

    assert_eq!(raw_files, git2_files, "tracked file lists differ");
}

#[test]
fn golden_diff_stats_match() {
    use repotoire::git::raw::{diff_trees_with_hunks, compute_stats};

    let raw_repo = RawRepo::discover(repo_path()).unwrap();
    let head_oid = raw_repo.resolve_head().unwrap();
    let commit = raw_repo.find_commit(&head_oid).unwrap();
    if let Some(parent_oid) = commit.parents.first() {
        let parent = raw_repo.find_commit(parent_oid).unwrap();
        let results = diff_trees_with_hunks(&raw_repo, &parent.tree_oid, &commit.tree_oid, &[]).unwrap();
        let (raw_ins, raw_del) = results.iter().fold((0, 0), |(i, d), (_, hunks)| {
            let s = compute_stats(hunks);
            (i + s.insertions, d + s.deletions)
        });

        // Compare with git2
        let git2_repo = git2::Repository::discover(repo_path()).unwrap();
        let git2_commit = git2_repo.find_commit(git2::Oid::from_str(&head_oid.to_hex()).unwrap()).unwrap();
        let git2_parent = git2_commit.parent(0).unwrap();
        let diff = git2_repo.diff_tree_to_tree(
            Some(&git2_parent.tree().unwrap()),
            Some(&git2_commit.tree().unwrap()),
            None,
        ).unwrap();
        let stats = diff.stats().unwrap();

        assert_eq!(raw_ins, stats.insertions(), "insertions mismatch");
        assert_eq!(raw_del, stats.deletions(), "deletions mismatch");
    }
}
```

- [ ] **Step 2: Run golden tests**

Run: `cargo test --test raw_git_golden -- --nocapture`
Expected: all 5 tests pass

- [ ] **Step 3: Commit**

```bash
git add tests/raw_git_golden.rs
git commit -m "test: add golden tests for raw git vs git2 parity"
```

---

## Task 13: Migrate telemetry/config.rs

**Files:**
- Modify: `src/telemetry/config.rs:131-145`

Simplest consumer: just needs root commit OID.

- [ ] **Step 1: Replace git2 usage**

Change `compute_repo_id`:
```rust
pub fn compute_repo_id(path: &Path) -> Option<String> {
    let repo = crate::git::raw::RawRepo::discover(path).ok()?;
    let root_oid = repo.find_root_commit().ok()?;
    let hash = root_oid.to_hex();
    Some(compute_repo_id_from_hash(&hash))
}
```

Remove `use git2` import (if any — this file uses fully-qualified `git2::` paths).

- [ ] **Step 2: Run tests**

Run: `cargo test telemetry -- --nocapture`
Expected: existing tests pass

- [ ] **Step 3: Run golden test to verify root commit still matches**

Run: `cargo test golden_root_commit_matches -- --nocapture`
Expected: pass

- [ ] **Step 4: Commit**

```bash
git add src/telemetry/config.rs
git commit -m "refactor: migrate telemetry/config.rs from git2 to raw git"
```

---

## Task 14: Migrate classifier/bootstrap.rs

**Files:**
- Modify: `src/classifier/bootstrap.rs:67-230`

Replace `git2::Repository`, `git2::Sort`, revwalk, and diff_tree_to_tree.

- [ ] **Step 1: Rewrite `mine_labels`, `find_fix_commit_files`, `find_stable_files`**

Replace:
- `git2::Repository::discover(repo_path)` -> `RawRepo::discover(repo_path)`
- `repo.revwalk()` + `push_head()` + `set_sorting(Sort::TIME)` -> `RevWalk::new(&repo)` + `push_head()`
- `repo.find_commit(oid)` -> `repo.find_commit(&oid)`
- `commit.message()` -> `commit.message` (it's a field, not method)
- `repo.diff_tree_to_tree(parent, Some(&tree), None)` -> `diff_trees(&repo, &parent.tree_oid, &commit.tree_oid, &[])`
- `diff.deltas()` iterator -> iterate `Vec<DiffDelta>` directly
- `delta.new_file().path()` -> `delta.new_path`

Keep the same error-handling pattern (return empty vec on any git error).

- [ ] **Step 2: Verify compilation**

Run: `cargo check 2>&1 | tail -5`
Expected: compiles clean

- [ ] **Step 3: Run tests**

Run: `cargo test classifier::bootstrap -- --nocapture`
Expected: existing tests pass (if any)

- [ ] **Step 4: Commit**

```bash
git add src/classifier/bootstrap.rs
git commit -m "refactor: migrate classifier/bootstrap.rs from git2 to raw git"
```

---

## Task 15: Migrate history.rs

**Files:**
- Modify: `src/git/history.rs`

This is the bulk of the migration. Replace all git2 internals in `GitHistory`.

- [ ] **Step 1: Replace `GitHistory` struct and constructor**

Change `GitHistory` to hold `RawRepo` instead of `git2::Repository`:
```rust
pub struct GitHistory {
    repo: RawRepo,
}
```

Replace `open()`:
```rust
pub fn open(path: &Path) -> Result<Self> {
    let repo = RawRepo::discover(path)
        .with_context(|| format!("Failed to open git repository at {:?}", path))?;
    debug!("Opened git repository at {:?}", repo.git_dir());
    Ok(Self { repo })
}
```

Replace `is_git_repo`: `RawRepo::discover(path).is_ok()`

Replace `repo_root`: `self.repo.workdir()`

- [ ] **Step 2: Replace helper functions**

Remove `tune_libgit2()`, `fast_diff_opts()`, `fast_pathspec_opts()`, `format_git_time()`.

Add time formatting helper:
```rust
fn format_epoch_time(secs: i64) -> String {
    match chrono::Utc.timestamp_opt(secs, 0).single() {
        Some(dt) => dt.to_rfc3339(),
        None => "1970-01-01T00:00:00Z".to_string(),
    }
}
```

- [ ] **Step 3: Replace `get_file_commits` and `get_recent_commits`**

Use `RevWalk` + `diff_trees` with pathspec filtering. Pattern:
```rust
let mut walk = RevWalk::new(&self.repo);
walk.push_head()?;
for oid in &mut walk {
    let oid = oid?;
    let commit = self.repo.find_commit(&oid)?;
    // diff with parent...
}
```

- [ ] **Step 4: Replace `get_all_file_churn`, `get_file_churn_counts`**

Same pattern but with `walk.simplify_first_parent()` and `diff_trees` without pathspec.

- [ ] **Step 5: Replace `get_file_commits_with_hunks`, `get_batch_file_commits_with_hunks`, `get_hunks_for_paths`**

Use `diff_trees_with_hunks` for hunk data. Map `DiffHunk` -> `HunkDetail` per spec mapping.

- [ ] **Step 6: Replace `get_tracked_files`**

Use `repo.head_tree()` + `walk_tree()`.

- [ ] **Step 7: Replace `extract_commit_info`, `get_commit_file_stats`, `commit_touches_lines`**

**Concrete `diff.stats()` replacement pattern**: Every call site that used `diff.stats()?.insertions()` must now use `diff_trees_with_hunks` and aggregate:
```rust
// Before (git2):
let diff = self.repo.diff_tree_to_tree(...)?;
let stats = diff.stats()?;
let ins = stats.insertions();
let del = stats.deletions();

// After (raw):
let results = diff_trees_with_hunks(&self.repo, &parent_tree_oid, &commit_tree_oid, &pathspecs)?;
let (ins, del) = results.iter().fold((0, 0), |(i, d), (_, hunks)| {
    let s = compute_stats(hunks);
    (i + s.insertions, d + s.deletions)
});
```

**Root commit diff (no parent)**: When `commit.parents` is empty, pass `Oid::ZERO` as the old tree OID. `diff_trees` must handle `Oid::ZERO` by treating it as an empty tree (all entries in new tree are Added). Implement this check at the top of `diff_trees`:
```rust
if old_tree_oid == &Oid::ZERO {
    // Everything in new tree is Added
}
```

**`process_diff_file_cb` rewrite**: This function currently takes `git2::DiffDelta<'_>`. Replace with direct iteration over `Vec<DiffDelta>`:
```rust
// Before (git2 callback):
diff.foreach(&mut |delta, _| { process_diff_file_cb(delta, ...); true }, None, None, None)?;

// After (raw):
for delta in &deltas {
    process_diff_delta(churn_map, delta.new_path.clone(), author, timestamp);
}
```
The `process_diff_file_cb` wrapper function can be deleted; call `process_diff_delta` directly.

Additional API mappings:
- `git2::Oid::from_str(commit_hash)` -> `Oid::from_hex(commit_hash)`
- `commit.time().seconds()` -> `commit.committer_time`
- `commit.id().to_string()` -> `commit_oid.to_hex()`
- `diff.foreach(file_cb, None, Some(hunk_cb), None)` pattern -> iterate `Vec<(DiffDelta, Vec<DiffHunk>)>` directly (callbacks eliminated)

- [ ] **Step 8: Replace test helpers**

`history.rs` tests use `Repository::init()`, `repo.config()`, `repo.signature()`, `repo.index()`, `repo.commit()` for test setup. Replace all with `Command::new("git")` setup pattern (same as blame tests).

- [ ] **Step 9: Run tests**

Run: `cargo test git::history -- --nocapture`
Expected: all existing tests pass

- [ ] **Step 10: Run golden tests**

Run: `cargo test --test raw_git_golden -- --nocapture`
Expected: all pass

- [ ] **Step 11: Commit**

```bash
git add src/git/history.rs
git commit -m "refactor: migrate history.rs from git2 to raw git"
```

---

## Task 16: Migrate blame.rs

**Files:**
- Modify: `src/git/blame.rs`

- [ ] **Step 1: Replace `GitBlame` struct**

Change to hold `Arc<RawRepo>` (or shared reference):
```rust
pub struct GitBlame {
    repo: Arc<RawRepo>,
    repo_path: PathBuf,
    file_cache: Arc<DashMap<String, Vec<LineBlame>>>,
    disk_cache: Arc<std::sync::RwLock<GitCache>>,
    cache_path: PathBuf,
}
```

Replace `open()` to use `RawRepo::discover`.

- [ ] **Step 2: Replace `blame_file` and `blame_lines`**

Use `raw::blame::blame_file(&self.repo, file_path)` and convert `BlameHunk` -> `LineBlame`:
```rust
let raw_hunks = crate::git::raw::blame::blame_file(&self.repo, file_path)?;
let entries: Vec<LineBlame> = raw_hunks.into_iter().map(|h| LineBlame {
    commit_hash: format!("{}", h.commit), // short hash via Display
    full_hash: h.commit.to_hex(),
    author: h.author_name,
    author_email: h.author_email,
    timestamp: format_epoch_time(h.author_time),
    line_start: h.orig_start_line,
    line_end: h.orig_start_line + h.num_lines - 1,
    line_count: h.num_lines,
}).collect();
```

- [ ] **Step 3: Fix `prewarm_cache` to share single repo**

Since `RawRepo` is `Send + Sync`, replace the per-thread `Repository::discover()` with shared `&self.repo`:
```rust
file_paths.par_iter().for_each(|file_path| {
    // No more Repository::discover() per thread!
    let Ok(entries) = blame_file_with_raw_repo(&self.repo, file_path) else {
        return;
    };
    mem_cache.insert(file_path.clone(), entries.clone());
    update_disk_cache(&disk_cache, file_path, &repo_path, entries);
});
```

- [ ] **Step 4: Replace test helpers and `format_git_time`**

Replace `Repository::init()` in tests with `Command::new("git")` setup. Also replace `format_git_time(&git2::Time)` with `format_epoch_time(secs: i64)` (same helper as history.rs).

- [ ] **Step 5: Run tests**

Run: `cargo test git::blame -- --nocapture`
Expected: all existing tests pass

- [ ] **Step 6: Commit**

```bash
git add src/git/blame.rs
git commit -m "refactor: migrate blame.rs from git2 to raw git"
```

---

## Task 17: Remove git2 + verify dep reduction

**Files:**
- Modify: `Cargo.toml` (remove `git2` line)
- Delete: `tests/raw_git_golden.rs` (golden tests reference git2)

- [ ] **Step 1: Count deps before removal**

Run: `cargo tree -e no-dev | sort -u | wc -l`
Record the number.

- [ ] **Step 2: Remove git2 from Cargo.toml**

Remove the line:
```toml
git2 = { version = "0.20", default-features = false, features = ["vendored-libgit2"] }
```

- [ ] **Step 3: Remove golden tests**

Delete `tests/raw_git_golden.rs` (they reference `git2` directly).

- [ ] **Step 4: Verify compilation**

Run: `cargo check 2>&1 | tail -10`
Expected: compiles clean with zero git2 references

- [ ] **Step 5: Verify no remaining git2 references**

Run: `grep -r "git2" src/ --include="*.rs" | grep -v "// " | head -20`
Expected: zero matches

- [ ] **Step 6: Count deps after removal**

Run: `cargo tree -e no-dev | sort -u | wc -l`
Compare with before. Expected: ~89 fewer deps.

- [ ] **Step 7: Run full test suite**

Run: `cargo test`
Expected: all tests pass

- [ ] **Step 8: Run clippy**

Run: `cargo clippy --all-features -- -D warnings`
Expected: no warnings

- [ ] **Step 9: Commit**

```bash
git add Cargo.toml Cargo.lock
git rm tests/raw_git_golden.rs
git commit -m "feat: remove git2 dependency (~89 fewer transitive deps)"
```

---

## Summary

| Task | Component | Estimated Lines |
|------|-----------|----------------|
| 1 | Module scaffold + error + OID | ~130 |
| 2 | SHA-1 | ~250 |
| 3 | DEFLATE decompressor | ~1,000 |
| 4 | Loose object reader | ~120 |
| 5 | Pack index v2 | ~200 |
| 6 | Packfile + delta | ~400 |
| 7 | Commit + tree parsers | ~180 |
| 8 | RawRepo | ~300 |
| 9 | Revwalk | ~150 |
| 10 | Tree diff + Myers diff | ~400 |
| 11 | Blame algorithm | ~300 |
| 12 | Golden tests | ~100 |
| 13-16 | Migration (4 consumers) | ~net zero (rewrite) |
| 17 | Remove git2 | ~net negative |
| **Total new code** | | **~3,530** |
