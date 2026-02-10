# Versioning Policy

Repotoire follows [Semantic Versioning 2.0.0](https://semver.org/) (SemVer).

## Version Format

```
MAJOR.MINOR.PATCH
```

- **MAJOR**: Incompatible API changes, breaking changes
- **MINOR**: New features, backwards-compatible additions
- **PATCH**: Bug fixes, backwards-compatible patches

## Current Version

Check the current version:

```bash
repotoire --version
```

Or programmatically:

```python
import repotoire
print(repotoire.__version__)
```

## Version Bumping

We use [bump-my-version](https://github.com/callowayproject/bump-my-version) for automated version management.

### Commands

```bash
# Preview what will change (dry-run)
uv run bump-my-version show-bump

# Bump patch version (0.1.0 → 0.1.1)
uv run bump-my-version bump patch

# Bump minor version (0.1.0 → 0.2.0)
uv run bump-my-version bump minor

# Bump major version (0.1.0 → 1.0.0)
uv run bump-my-version bump major

# Dry-run (see changes without applying)
uv run bump-my-version bump patch --dry-run --verbose
```

### What Gets Updated

When you bump a version, these files are automatically updated:

1. `pyproject.toml` - Package version
2. `repotoire/__init__.py` - `__version__` variable

A git commit and tag are automatically created:
- Commit message: `chore: bump version X.Y.Z → A.B.C`
- Tag: `vA.B.C`

## When to Bump Versions

### Patch (0.0.X)

- Bug fixes
- Security patches
- Documentation fixes
- Performance improvements (no API changes)
- Dependency updates (non-breaking)

### Minor (0.X.0)

- New features
- New CLI commands
- New detectors
- New configuration options
- Deprecations (with backwards compatibility)

### Major (X.0.0)

- Breaking API changes
- Removed features
- Changed CLI interface (incompatible)
- Changed configuration format (incompatible)
- Major architectural changes

## Pre-release Versions

For pre-release versions, we use suffixes:

- Alpha: `1.0.0a1`, `1.0.0a2`
- Beta: `1.0.0b1`, `1.0.0b2`
- Release Candidate: `1.0.0rc1`, `1.0.0rc2`

## Changelog Generation

We use [git-cliff](https://git-cliff.org/) for automatic changelog generation from conventional commits.

```bash
# Generate/update full changelog
uv run git-cliff --output CHANGELOG.md

# Preview unreleased changes
uv run git-cliff --unreleased

# Generate changelog for specific tag range
uv run git-cliff v0.1.0..v0.2.0
```

## Release Process

### Quick Release

```bash
# 1. Ensure clean working directory
git status

# 2. Run tests
uv run pytest

# 3. Update changelog
uv run git-cliff --output CHANGELOG.md
git add CHANGELOG.md && git commit -m "docs: update changelog"

# 4. Bump version (creates commit + tag)
uv run bump-my-version bump patch  # or minor/major

# 5. Push to trigger release
git push && git push --tags
```

### What Happens Automatically

When you push a tag (e.g., `v0.1.1`), GitHub Actions will:

1. **Build** the package (wheel + sdist)
2. **Publish to PyPI** via trusted publishing
3. **Create GitHub Release** with auto-generated release notes
4. **Upload artifacts** (wheel, sdist) to the release

Pre-release tags (`alpha`, `beta`, `rc`) are published to TestPyPI instead.

### Manual Release (if needed)

```bash
# Build locally
python -m build

# Upload to TestPyPI (for testing)
twine upload --repository testpypi dist/*

# Upload to PyPI
twine upload dist/*
```

## Version History

See [CHANGELOG.md](../CHANGELOG.md) for detailed release history.
