# Auto-Fix: AI-Powered Code Fixing with Human-in-the-Loop

Repotoire's auto-fix feature provides intelligent, evidence-based code fixes powered by GPT-4o with human approval before any changes are made to your codebase.

## Table of Contents

- [Overview](#overview)
- [Features](#features)
- [Architecture](#architecture)
- [Installation](#installation)
- [Quick Start](#quick-start)
- [CLI Reference](#cli-reference)
- [Evidence-Based Fixes](#evidence-based-fixes)
- [Workflow](#workflow)
- [Best Practices](#best-practices)
- [Configuration](#configuration)
- [Examples](#examples)
- [Troubleshooting](#troubleshooting)
- [API Reference](#api-reference)

## Overview

The auto-fix feature analyzes code smells and issues detected by Repotoire and generates intelligent fixes using:

- **GPT-4o** for fix generation
- **RAG (Retrieval-Augmented Generation)** for context-aware fixes using codebase knowledge
- **Evidence-based justification** with documentation references, best practices, and similar patterns
- **Human-in-the-loop approval** with interactive review UI
- **Automatic git integration** with branch creation and commits
- **Syntax validation** to ensure fixes don't break code
- **Optional test generation** for refactoring fixes

## Features

### 🤖 AI-Powered Fix Generation

- Uses GPT-4o to generate high-quality code fixes
- Leverages RAG to find related code patterns in your codebase
- Provides evidence-based justification for every fix

### 📊 Evidence-Based Fixes

Every fix includes:
- **Documentation references** (PEP 8, Python docs, standards)
- **Best practices** justifications
- **Similar patterns** from your codebase
- **RAG context** showing related code

### 🎨 Beautiful Interactive UI

- Rich terminal interface with syntax highlighting
- Side-by-side diffs with color coding
- Confidence indicators (HIGH/MEDIUM/LOW)
- Validation status display
- Evidence panel with research backing

### 🔧 Git Integration

- Automatic branch creation for fixes
- Descriptive commit messages
- Rollback capability
- Batch commit support

### ✅ Safety Features

- Syntax validation before applying
- Human approval required
- Original code verification
- Test execution support

## Architecture

```
Finding → RAG Context Gathering → GPT-4 Fix Generation → Syntax Validation → Human Review → Git Apply
```

### Core Components

1. **AutoFixEngine**: Generates fixes using LLM + RAG
2. **InteractiveReviewer**: Human approval UI with Rich
3. **FixApplicator**: Applies fixes to files with git integration

## Installation

### Prerequisites

```bash
# Install Repotoire with auto-fix support
pip install repotoire[autofix]

# Set OpenAI API key
export OPENAI_API_KEY="sk-..."

# Neo4j is required for RAG
export REPOTOIRE_NEO4J_URI="bolt://localhost:7687"
export REPOTOIRE_NEO4J_PASSWORD="your-password"
```

### Setup Neo4j

```bash
# Start Neo4j with Docker
docker run \
    --name repotoire-neo4j \
    -p 7474:7474 -p 7688:7687 \
    -d \
    -e NEO4J_AUTH=neo4j/your-password \
    neo4j:latest
```

### Ingest Your Codebase

```bash
# Ingest with embeddings for RAG
repotoire ingest /path/to/repo --generate-embeddings

# Analyze to detect issues
repotoire analyze /path/to/repo
```

## Quick Start

### Basic Usage

```bash
# Auto-fix issues in your repository
repotoire auto-fix /path/to/repo

# Fix only critical issues
repotoire auto-fix /path/to/repo --severity critical

# Auto-approve high-confidence fixes
repotoire auto-fix /path/to/repo --auto-approve-high

# Limit number of fixes
repotoire auto-fix /path/to/repo --max-fixes 5
```

### Example Session

```bash
$ repotoire auto-fix ./my-project --severity high

🔍 Analyzing codebase health...
Found 15 issues (5 high, 10 medium)

🤖 Generating fixes for high severity issues...
Generated 5 fix proposals in 12.3s

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                           Auto-Fix Proposal
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

┌────────────────────┬────────────────────────────────────────────────────────┐
│ Fix ID             │ fix-a3b2c1                                             │
│ Issue              │ Use 'is None' instead of '== None'                    │
│ Severity           │ MEDIUM                                                 │
│ Fix Type           │ Refactor                                               │
│ Confidence         │ ● HIGH                                                 │
│ Files              │ src/utils.py                                           │
└────────────────────┴────────────────────────────────────────────────────────┘

┌─ Description ──────────────────────────────────────────────────────────────┐
│ Change == None to is None for PEP 8 compliance                            │
└────────────────────────────────────────────────────────────────────────────┘

┌─ Rationale ────────────────────────────────────────────────────────────────┐
│ PEP 8 recommends using 'is' for None comparisons as it's more explicit    │
│ and prevents potential bugs with objects that override __eq__             │
└────────────────────────────────────────────────────────────────────────────┘

┌─ Research & Evidence ──────────────────────────────────────────────────────┐
│ 📚 Documentation & Standards:                                              │
│   • PEP 8: Comparisons to singletons should always use 'is' or 'is not'  │
│   • Python docs: None is a singleton, use 'is' for identity checks       │
│                                                                            │
│ ✓ Best Practices:                                                          │
│   • Using 'is None' is more explicit and prevents bugs                    │
│   • Identity checks are faster than equality checks                       │
│                                                                            │
│ 🔍 Similar Patterns in Codebase:                                           │
│   • Found in 23 files using 'is None' correctly                           │
│   • Common pattern in src/models.py, src/validators.py                   │
└────────────────────────────────────────────────────────────────────────────┘

┌─ Change 1/1: src/utils.py - Fix None comparison ──────────────────────────┐
--- a/src/utils.py
+++ b/src/utils.py
@@ -15,7 +15,7 @@
 def validate_input(value):
-    if value == None:
+    if value is None:
         return False
     return True
└────────────────────────────────────────────────────────────────────────────┘

┌─ Validation ───────────────────────────────────────────────────────────────┐
│ ✓ Syntax valid | ○ No tests generated                                     │
└────────────────────────────────────────────────────────────────────────────┘

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Apply this fix? [Y/n]: y

✅ Fix approved

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📝 Applying approved fixes...

✅ Applied fix: Use 'is None' instead of '== None'
   Branch: autofix/refactor/fix-a3b2c1
   Commit: 8a3f2b1

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                      Auto-Fix Session Summary
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Fixes Generated           5
Approved for Application  3
Successfully Applied      3

✓ 3 fix(es) have been applied to your codebase.
Review the changes with 'git diff' and commit when ready.
```

## CLI Reference

### Command: `repotoire auto-fix`

```bash
repotoire auto-fix [OPTIONS] REPOSITORY
```

#### Arguments

- `REPOSITORY`: Path to the repository to analyze and fix

#### Options

| Option | Description | Default |
|--------|-------------|---------|
| `--max-fixes, -n` | Maximum number of fixes to generate | 10 |
| `--severity, -s` | Minimum severity level (critical/high/medium/low) | All |
| `--fix-type, -t` | Filter by fix type (refactor/security/documentation/etc.) | All |
| `--auto-approve-high` | Automatically approve high-confidence fixes | False |
| `--create-branch/--no-branch` | Create git branches for fixes | True |
| `--run-tests` | Run tests after applying fixes | False |
| `--test-command` | Custom test command | `pytest` |
| `--dry-run` | Show fixes without applying | False |
| `--output, -o` | Save fix proposals to JSON file | - |

#### Examples

```bash
# Fix critical security issues only
repotoire auto-fix ./repo --severity critical --fix-type security

# Auto-approve high confidence, run tests
repotoire auto-fix ./repo --auto-approve-high --run-tests

# Generate fixes but don't apply (dry-run)
repotoire auto-fix ./repo --dry-run --output fixes.json

# Custom test command
repotoire auto-fix ./repo --run-tests --test-command "python -m pytest -v"

# No branch creation (apply to current branch)
repotoire auto-fix ./repo --no-branch
```

## Evidence-Based Fixes

Every fix generated by Repotoire includes comprehensive evidence to justify the change:

### Documentation References

References to official documentation, standards, and style guides:
- PEP 8 (Python Enhancement Proposals)
- Official Python documentation
- Language-specific best practices
- Security guidelines (OWASP, CWE)

### Best Practices

Industry-standard best practices explaining why the fix is recommended:
- Performance benefits
- Security improvements
- Maintainability gains
- Readability enhancements

### Similar Patterns

Examples from your codebase showing similar patterns:
- Files using the recommended pattern
- Successful implementations in your code
- Consistency with team conventions

### RAG Context

Related code snippets retrieved from your codebase:
- Similar functions or classes
- Related implementations
- Contextually relevant code

## Workflow

### 1. Analysis Phase

```bash
repotoire auto-fix /path/to/repo
```

- Runs codebase health analysis
- Identifies fixable issues
- Filters by severity and fix type

### 2. Fix Generation Phase

- Uses RAG to gather context from codebase
- Generates fixes using GPT-4o
- Validates syntax of generated code
- Calculates confidence scores
- Optionally generates tests

### 3. Review Phase

- Displays fix proposals with evidence
- Shows diffs with syntax highlighting
- Indicates confidence level
- Prompts for human approval
- Auto-approves high-confidence fixes (if enabled)

### 4. Application Phase

- Creates git branch (if enabled)
- Applies code changes to files
- Verifies original code matches
- Creates git commit with descriptive message
- Runs tests (if enabled)

### 5. Summary Phase

- Shows statistics (generated/approved/applied)
- Provides git commands for next steps
- Offers rollback option if needed

## Best Practices

### 1. Start with High Severity Issues

```bash
repotoire auto-fix ./repo --severity high
```

Focus on critical and high-severity issues first for maximum impact.

### 2. Review Evidence Carefully

Always review the evidence panel to understand:
- Why the fix is recommended
- What standards it follows
- How it's used elsewhere in your codebase

### 3. Use Dry-Run for Exploration

```bash
repotoire auto-fix ./repo --dry-run --output fixes.json
```

Explore potential fixes without committing to changes.

### 4. Enable Tests for Refactoring

```bash
repotoire auto-fix ./repo --fix-type refactor --run-tests
```

Always run tests when applying refactoring fixes to ensure functionality is preserved.

### 5. Auto-Approve Conservatively

Only use `--auto-approve-high` when you're confident in the fix types:

```bash
# Safe: Auto-approve simple fixes
repotoire auto-fix ./repo --severity low --auto-approve-high

# Risky: Don't auto-approve security fixes
repotoire auto-fix ./repo --severity critical  # Review manually
```

### 6. Incremental Adoption

Start small and iterate:

```bash
# Week 1: Fix documentation issues
repotoire auto-fix ./repo --fix-type documentation

# Week 2: Fix simple refactorings
repotoire auto-fix ./repo --fix-type simplify

# Week 3: Fix security issues (with review)
repotoire auto-fix ./repo --fix-type security
```

## Configuration

### Environment Variables

```bash
# Required
export OPENAI_API_KEY="sk-..."
export REPOTOIRE_NEO4J_PASSWORD="your-password"

# Optional
export REPOTOIRE_NEO4J_URI="bolt://localhost:7687"
export REPOTOIRE_AUTOFIX_MAX_FIXES=20
export REPOTOIRE_AUTOFIX_AUTO_APPROVE=false
```

### Config File (.repotoirerc)

```yaml
autofix:
  max_fixes: 10
  auto_approve_high: false
  create_branches: true
  run_tests: false
  test_command: "pytest"

  # Filter settings
  min_severity: medium
  fix_types:
    - refactor
    - security
    - documentation

  # LLM settings
  model: "gpt-4o"
  temperature: 0.2

  # RAG settings
  context_size: 5
  use_embeddings: true
```

## Examples

### Example 1: Fix Security Issues

```bash
# Find and fix security vulnerabilities
repotoire auto-fix ./repo --severity critical --fix-type security
```

**Output:**
- SQL injection fixes
- Path traversal fixes
- Hardcoded credential removals
- Unsafe deserialization fixes

### Example 2: Improve Code Quality

```bash
# Refactor complex functions
repotoire auto-fix ./repo --fix-type simplify --max-fixes 5
```

**Output:**
- Extract method refactorings
- Reduce cyclomatic complexity
- Remove dead code
- Simplify conditional logic

### Example 3: Add Documentation

```bash
# Add missing docstrings
repotoire auto-fix ./repo --fix-type documentation --auto-approve-high
```

**Output:**
- Function docstrings
- Class documentation
- Module-level docs
- Parameter descriptions

### Example 4: Batch Processing

```bash
# Fix multiple types in sequence
repotoire auto-fix ./repo --severity high --dry-run --output proposals.json

# Review proposals.json, then apply
repotoire auto-fix ./repo --severity high --auto-approve-high
```

## Troubleshooting

### Issue: "OpenAI API key not found"

**Solution:**
```bash
export OPENAI_API_KEY="sk-..."
```

### Issue: "Neo4j connection failed"

**Solution:**
```bash
# Check Neo4j is running
docker ps | grep neo4j

# Verify connection
export REPOTOIRE_NEO4J_URI="bolt://localhost:7687"
export REPOTOIRE_NEO4J_PASSWORD="your-password"

# Test connection
repotoire validate
```

### Issue: "No embeddings found for RAG"

**Solution:**
```bash
# Ingest codebase with embeddings
repotoire ingest /path/to/repo --generate-embeddings
```

### Issue: "Syntax validation failed"

This can happen if the LLM generates invalid code. The fix will be marked as invalid and not applied. You can:

1. Try again (LLM may generate better code)
2. Manually fix the issue
3. Report as a bug if it persists

### Issue: "Original code not found in file"

This happens when the file has changed since analysis. Solutions:

1. Re-run analysis to get fresh findings
2. Use incremental analysis to catch changes
3. Apply fixes immediately after analysis

### Issue: "Tests failed after applying fix"

If tests fail after applying a fix:

```bash
# Rollback changes
git reset --hard HEAD

# Re-run analysis
repotoire analyze /path/to/repo

# Try fix again or skip problematic fix
repotoire auto-fix /path/to/repo
```

## API Reference

### Python API

#### AutoFixEngine

```python
from repotoire.autofix import AutoFixEngine
from repotoire.graph import Neo4jClient

# Initialize
neo4j = Neo4jClient(uri="bolt://localhost:7687", password="password")
engine = AutoFixEngine(neo4j, openai_api_key="sk-...", model="gpt-4o")

# Generate fix
fix = await engine.generate_fix(finding, repository_path)
```

#### InteractiveReviewer

```python
from repotoire.autofix import InteractiveReviewer

reviewer = InteractiveReviewer()

# Review single fix
approved = reviewer.review_fix(fix_proposal)

# Review batch
approved_fixes = reviewer.review_batch(
    fixes,
    auto_approve_high=True
)

# Show summary
reviewer.show_summary(
    total=10,
    approved=7,
    applied=6,
    failed=1
)
```

#### FixApplicator

```python
from repotoire.autofix import FixApplicator

applicator = FixApplicator(
    repository_path=Path("/path/to/repo"),
    create_branch=True
)

# Apply single fix
success, error = applicator.apply_fix(fix_proposal, commit=True)

# Apply batch
successful, failed = applicator.apply_batch(
    fixes,
    commit_each=False
)

# Run tests
success, output = applicator.run_tests(test_command="pytest")

# Rollback
applicator.rollback()
```

### Models

#### FixProposal

```python
from repotoire.autofix.models import FixProposal, FixType, FixConfidence, FixStatus

fix = FixProposal(
    id="fix-123",
    finding=finding,
    fix_type=FixType.REFACTOR,
    confidence=FixConfidence.HIGH,
    changes=[change],
    title="Fix title",
    description="Detailed description",
    rationale="Why this fix is correct",
    evidence=evidence,
    syntax_valid=True,
    status=FixStatus.PENDING
)
```

#### Evidence

```python
from repotoire.autofix.models import Evidence

evidence = Evidence(
    documentation_refs=["PEP 8: ...", "Python docs: ..."],
    best_practices=["Why this is recommended"],
    similar_patterns=["Used in 10 files"],
    rag_context=["Related code snippet 1", "Related code snippet 2"]
)
```

#### CodeChange

```python
from repotoire.autofix.models import CodeChange

change = CodeChange(
    file_path=Path("src/utils.py"),
    original_code="if x == None:",
    fixed_code="if x is None:",
    start_line=15,
    end_line=15,
    description="Use 'is None' for None comparison"
)
```

## Performance

### Speed

- **Fix Generation**: ~2-5 seconds per fix (with RAG)
- **Syntax Validation**: <100ms per fix
- **File Application**: <50ms per file

### Cost (OpenAI)

- **Fix Generation**: ~$0.01-0.03 per fix (GPT-4o)
- **RAG Context**: Embeddings already cached from ingestion
- **Test Generation**: ~$0.005 per test

**Estimated cost for 100 fixes**: ~$2-4

### Recommendations

- Use `--max-fixes` to control costs
- Enable `--auto-approve-high` to reduce manual review time
- Use `--dry-run` to preview fixes before applying

## Limitations

1. **Language Support**: Currently Python only (TypeScript/Java coming soon)
2. **Complex Refactorings**: May struggle with large-scale architectural changes
3. **Context Window**: Limited by GPT-4o context window (128K tokens)
4. **Test Generation**: Generated tests may need manual refinement

## Future Enhancements

- [ ] Multi-language support (TypeScript, Java, Go)
- [ ] Custom fix templates
- [ ] Team-specific style enforcement
- [ ] Auto-fix scheduling and CI/CD integration
- [ ] Fix impact analysis
- [ ] Learning from accepted/rejected fixes

## Contributing

See [CONTRIBUTING.md](../CONTRIBUTING.md) for guidelines on contributing to auto-fix.

## License

See [LICENSE](../LICENSE) for details.

---

**Questions or Issues?** File an issue at https://github.com/your-org/repotoire/issues
