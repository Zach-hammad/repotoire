# Contributing to Repotoire

Thank you for your interest in contributing to Repotoire! This document provides guidelines and information for contributors.

## Table of Contents

- [Code of Conduct](#code-of-conduct)
- [Getting Started](#getting-started)
- [Development Setup](#development-setup)
- [How to Contribute](#how-to-contribute)
- [Pull Request Process](#pull-request-process)
- [Code Style](#code-style)
- [Testing](#testing)
- [Commit Messages](#commit-messages)
- [Getting Help](#getting-help)

## Code of Conduct

By participating in this project, you agree to maintain a respectful and inclusive environment. We expect all contributors to:

- Be respectful of differing viewpoints and experiences
- Accept constructive criticism gracefully
- Focus on what is best for the community
- Show empathy towards other community members

## Getting Started

1. **Fork the repository** on GitHub
2. **Clone your fork** locally
3. **Set up the development environment** (see below)
4. **Create a branch** for your changes
5. **Make your changes** with tests
6. **Submit a pull request**

## Development Setup

### Prerequisites

- Python 3.10 or higher
- Docker (for Neo4j)
- Git

### Installation

```bash
# Clone your fork
git clone https://github.com/YOUR_USERNAME/repotoire.git
cd repotoire

# Install with development dependencies
pip install -e ".[dev]"

# Or use uv for faster installation
uv pip install -e ".[dev]"

# Download spaCy model for NLP features
python -m spacy download en_core_web_lg
```

### Neo4j Setup

Start Neo4j using Docker:

```bash
docker run \
    --name repotoire-neo4j \
    -p 7474:7474 -p 7687:7687 \
    -d \
    -e NEO4J_AUTH=neo4j/your-password \
    -e NEO4J_PLUGINS='["graph-data-science", "apoc"]' \
    neo4j:latest
```

Configure your environment:

```bash
export REPOTOIRE_NEO4J_URI=bolt://localhost:7687
export REPOTOIRE_NEO4J_PASSWORD=your-password
```

For detailed setup instructions, see [CLAUDE.md](CLAUDE.md).

## How to Contribute

### Reporting Bugs

Before submitting a bug report:

1. Check the [issue tracker](https://github.com/repotoire/repotoire/issues) for existing reports
2. Collect information about the bug:
   - Stack trace
   - Python version (`python --version`)
   - OS and version
   - Steps to reproduce

Create a new issue with the **Bug Report** template.

### Suggesting Features

Feature requests are welcome! Please:

1. Check existing issues and discussions first
2. Clearly describe the use case
3. Explain why this would benefit other users
4. Use the **Feature Request** template

### Code Contributions

Good first issues are labeled with `good first issue`. These are ideal for newcomers.

Types of contributions we're looking for:

- **Bug fixes**: Fix issues in the tracker
- **New detectors**: Add code smell or security detectors
- **Parser improvements**: Enhance Python parsing or add new languages
- **Documentation**: Improve docs, examples, or docstrings
- **Tests**: Increase test coverage

## Pull Request Process

1. **Create a feature branch** from `main`:
   ```bash
   git checkout -b feature/your-feature-name
   ```

2. **Make your changes** following our code style guidelines

3. **Write or update tests** for your changes

4. **Run the test suite** to ensure nothing is broken:
   ```bash
   pytest
   ```

5. **Run linting and formatting**:
   ```bash
   black repotoire tests
   ruff check repotoire tests
   mypy repotoire
   ```

6. **Commit your changes** with a descriptive message (see [Commit Messages](#commit-messages))

7. **Push to your fork** and create a pull request

8. **Fill out the PR template** completely

9. **Address review feedback** promptly

### PR Requirements

- All tests must pass
- Code must be formatted with Black
- No new Ruff or mypy errors
- Documentation updated if needed
- Meaningful commit messages

## Code Style

We use automated tools to maintain consistent code style:

### Formatting

```bash
# Format code with Black
black repotoire tests

# Check without modifying
black --check repotoire tests
```

### Linting

```bash
# Run Ruff linter
ruff check repotoire tests

# Auto-fix issues where possible
ruff check --fix repotoire tests
```

### Type Checking

```bash
# Run mypy type checker
mypy repotoire
```

### Style Guidelines

- Follow PEP 8 conventions
- Use type hints for function signatures
- Write docstrings for public functions and classes
- Keep functions focused and reasonably sized
- Prefer descriptive variable names

## Testing

### Running Tests

```bash
# Run all tests
pytest

# Run with coverage report
pytest --cov=repotoire --cov-report=html

# Run specific test file
pytest tests/unit/test_models.py

# Run tests in parallel
pytest -n auto
```

### Writing Tests

- Place unit tests in `tests/unit/`
- Place integration tests in `tests/integration/`
- Use descriptive test names: `test_circular_dependency_detector_finds_cycles`
- Mock external services (Neo4j) in unit tests
- Use fixtures for common test data

### Test Coverage

We aim for high test coverage. New code should include tests that:

- Cover the happy path
- Test edge cases
- Verify error handling

## Commit Messages

Write clear, concise commit messages:

### Format

```
<type>: <short summary>

<optional body with more details>

<optional footer>
```

### Types

- `feat`: New feature
- `fix`: Bug fix
- `docs`: Documentation changes
- `style`: Code style changes (formatting, no logic change)
- `refactor`: Code refactoring
- `test`: Adding or updating tests
- `chore`: Maintenance tasks

### Examples

```
feat: add TypeScript parser support

Implements tree-sitter based TypeScript parsing with support for:
- Classes and interfaces
- Functions and methods
- Import/export relationships

Closes #123
```

```
fix: prevent false positives in dead code detection

Functions referenced via decorators were incorrectly flagged.
Added USES relationship tracking for decorator patterns.
```

## Getting Help

- **Documentation**: [CLAUDE.md](CLAUDE.md) for development details
- **Issues**: [GitHub Issues](https://github.com/repotoire/repotoire/issues)
- **Discussions**: [GitHub Discussions](https://github.com/repotoire/repotoire/discussions)

## Recognition

Contributors are recognized in:

- The project's contributor list
- Release notes for significant contributions

Thank you for contributing to Repotoire!
