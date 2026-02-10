# ⚠️ Repotoire has moved to Rust!

The Python version is **deprecated**. Please use the new Rust version:

## Install (New Way)

```bash
# Option 1: Cargo
cargo install repotoire

# Option 2: Binary download
curl -L https://github.com/Zach-hammad/repotoire/releases/latest/download/repotoire-linux-x86_64.tar.gz | tar xz
sudo mv repotoire /usr/local/bin/
```

## Why Rust?

- **10x faster** (2s vs 20s on large codebases)
- **81 detectors** (was 47)
- **24MB binary** (no Python runtime needed)
- **No dependencies** (pure Rust, no cmake/C++)

## Usage

```bash
repotoire analyze .
```

## Links

- GitHub: https://github.com/Zach-hammad/repotoire
- Releases: https://github.com/Zach-hammad/repotoire/releases
