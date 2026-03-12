# Benchmark Suite

Reproducible benchmark suite for measuring Repotoire's detection precision across real-world OSS projects.

## Projects

| Project | Language | Tag | Repository |
|---------|----------|-----|------------|
| Flask | Python | `3.1.0` | github.com/pallets/flask |
| FastAPI | Python | `0.115.6` | github.com/fastapi/fastapi |
| Tokio | Rust | `tokio-1.41.1` | github.com/tokio-rs/tokio |
| Serde | Rust | `v1.0.215` | github.com/serde-rs/serde |
| Express | JavaScript | `v5.0.1` | github.com/expressjs/express |

All projects are cloned at pinned tags with `--depth 1` for fast, reproducible setup.

## Usage

```bash
# Clone all benchmark projects
make setup

# Run analysis on all projects (builds Repotoire in release mode)
make run

# Clone a single project
make flask

# Remove all cloned repos and results
make clean
```

You can also run from the repository root:

```bash
make -C benchmark setup
make -C benchmark run
```

## Output

Each project's analysis results are saved as JSON:

```
benchmark/
  flask/results.json
  fastapi/results.json
  tokio/results.json
  serde/results.json
  express/results.json
```

These results files are git-ignored and not committed to the repository.
