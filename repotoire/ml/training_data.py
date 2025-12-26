"""Training data extraction from git history for ML bug prediction.

This module provides tools to extract labeled training data from git commit history
for machine learning bug prediction models. It identifies functions changed in
bug-fix commits (labeled as "buggy") and functions never involved in bugs
(labeled as "clean").

Features:
- Git history mining with configurable bug-fix keywords
- Diff parsing to identify changed functions
- Balanced dataset creation (50/50 buggy/clean)
- Active learning for human-in-the-loop refinement
"""

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple, Union
import ast
import hashlib
import json
import random
import re

from git import Repo, Commit, Diff
from pydantic import BaseModel, Field

# Try to use Rust implementation for ~5x speedup on diff parsing (REPO-244)
try:
    from repotoire_fast import parse_diff_changed_lines as _rust_parse_diff
    HAS_RUST_DIFF_PARSER = True
except ImportError:
    HAS_RUST_DIFF_PARSER = False

# Try to use Rust implementation for ~3-5x speedup on function boundary detection (REPO-245)
try:
    from repotoire_fast import extract_function_boundaries as _rust_extract_boundaries
    HAS_RUST_FUNCTION_PARSER = True
except ImportError:
    HAS_RUST_FUNCTION_PARSER = False

# Try to use Rust implementation for ~10x+ speedup on bug extraction (REPO-246)
try:
    from repotoire_fast import extract_buggy_functions_parallel as _rust_extract_buggy
    from repotoire_fast import PyBuggyFunction
    HAS_RUST_BUG_EXTRACTOR = True
except ImportError:
    HAS_RUST_BUG_EXTRACTOR = False

# Bug-fix commit keywords (case-insensitive matching)
DEFAULT_BUG_KEYWORDS = [
    "fix",
    "bug",
    "crash",
    "error",
    "hotfix",
    "patch",
    "vulnerability",
    "issue",
    "resolve",
    "repair",
    "broken",
    "fault",
    "defect",
    "problem",
]


class TrainingExample(BaseModel):
    """Single training example for bug prediction.

    Represents a function with its label (buggy/clean) and associated metadata
    for machine learning training.
    """

    qualified_name: str = Field(description="Fully qualified function name (e.g., module.Class.method)")
    file_path: str = Field(description="Relative path to the file containing the function")
    label: str = Field(description="Label: 'buggy' or 'clean'")
    commit_sha: Optional[str] = Field(default=None, description="SHA of the bug-fix commit (for buggy examples)")
    commit_message: Optional[str] = Field(default=None, description="Commit message (truncated)")
    commit_date: Optional[str] = Field(default=None, description="ISO format commit date")
    complexity: Optional[int] = Field(default=None, description="Cyclomatic complexity")
    loc: Optional[int] = Field(default=None, description="Lines of code")
    embedding: Optional[List[float]] = Field(default=None, description="Function embedding vector")
    confidence: float = Field(default=1.0, description="Label confidence (1.0 = verified, <1.0 = inferred)")
    source_code: Optional[str] = Field(default=None, description="Function source code (for display)")


class TrainingDataset(BaseModel):
    """Complete training dataset with metadata.

    Contains training examples along with extraction metadata for reproducibility.
    """

    examples: List[TrainingExample] = Field(default_factory=list)
    repository: str = Field(description="Path to the repository")
    extracted_at: str = Field(description="ISO format extraction timestamp")
    date_range: Tuple[str, str] = Field(description="(start_date, end_date) for commit range")
    statistics: Dict[str, Union[int, float, str]] = Field(default_factory=dict, description="Dataset statistics")
    version: str = Field(default="1.0.0", description="Dataset format version")


class FunctionInfo(BaseModel):
    """Information about a function extracted from a file."""

    name: str
    qualified_name: str
    file_path: str
    line_start: int
    line_end: int
    loc: int = 0
    complexity: int = 1
    source: Optional[str] = None


class GitBugLabelExtractor:
    """Extract bug labels from git history.

    Analyzes commit history to identify functions changed in bug-fix commits
    (labeled as "buggy") vs functions never involved in bugs (labeled as "clean").

    Example:
        >>> extractor = GitBugLabelExtractor(Path("/path/to/repo"))
        >>> dataset = extractor.create_balanced_dataset(since_date="2020-01-01")
        >>> print(f"Extracted {len(dataset.examples)} examples")
    """

    def __init__(
        self,
        repo_path: Path,
        keywords: Optional[List[str]] = None,
        min_loc: int = 5,
    ):
        """Initialize extractor.

        Args:
            repo_path: Path to git repository
            keywords: Bug-fix keywords to search in commit messages (case-insensitive)
            min_loc: Minimum lines of code for function to be included
        """
        self.repo_path = Path(repo_path).resolve()
        self.repo = Repo(repo_path)
        self.keywords = [kw.lower() for kw in (keywords or DEFAULT_BUG_KEYWORDS)]
        self.min_loc = min_loc

        # Will be populated during extraction
        self._buggy_functions: Dict[str, TrainingExample] = {}
        self._all_functions: Dict[str, FunctionInfo] = {}
        self._commit_function_cache: Dict[str, List[FunctionInfo]] = {}

    def is_bug_fix_commit(self, commit: Commit) -> bool:
        """Check if commit message indicates a bug fix.

        Uses case-insensitive keyword matching against the commit message.

        Args:
            commit: Git commit object

        Returns:
            True if commit message contains any bug-fix keywords
        """
        message = commit.message.lower()
        return any(kw in message for kw in self.keywords)

    def extract_changed_functions(
        self,
        commit: Commit,
    ) -> List[Tuple[str, str, str]]:
        """Extract function names changed in a commit.

        Parses the diff to identify Python functions that were modified.

        Args:
            commit: Git commit object

        Returns:
            List of (qualified_name, file_path, change_type) tuples
            where change_type is 'added', 'deleted', or 'modified'
        """
        changed_functions: List[Tuple[str, str, str]] = []

        # Skip merge commits
        if len(commit.parents) > 1:
            return changed_functions

        # Get parent commit (or empty tree for initial commit)
        parent = commit.parents[0] if commit.parents else None

        # Get diffs
        if parent:
            diffs = parent.diff(commit, create_patch=True)
        else:
            diffs = commit.diff(None, create_patch=True)

        for diff in diffs:
            # Only process Python files
            file_path = diff.b_path or diff.a_path
            if not file_path or not file_path.endswith(".py"):
                continue

            # Skip test files and __pycache__
            if "test" in file_path.lower() or "__pycache__" in file_path:
                continue

            # Determine change type
            if diff.new_file:
                change_type = "added"
            elif diff.deleted_file:
                change_type = "deleted"
            else:
                change_type = "modified"

            # Get the changed line numbers
            changed_lines = self._extract_changed_lines(diff)

            # Parse the file to find functions at those lines
            try:
                if diff.b_blob:
                    # Get new version of file
                    content = diff.b_blob.data_stream.read().decode("utf-8", errors="ignore")
                elif diff.a_blob:
                    # File was deleted, use old version
                    content = diff.a_blob.data_stream.read().decode("utf-8", errors="ignore")
                else:
                    continue

                functions = self._parse_functions_from_content(content, file_path)

                # Find functions that overlap with changed lines
                for func in functions:
                    if self._function_overlaps_changes(func, changed_lines):
                        changed_functions.append(
                            (func.qualified_name, file_path, change_type)
                        )

            except Exception:
                # Skip files that can't be parsed
                continue

        return changed_functions

    def _extract_changed_lines(self, diff: Diff) -> Set[int]:
        """Extract line numbers that were changed in a diff.

        Args:
            diff: Git diff object

        Returns:
            Set of line numbers that were modified
        """
        changed_lines: Set[int] = set()

        if not diff.diff:
            return changed_lines

        # Parse unified diff format
        try:
            diff_text = diff.diff.decode("utf-8", errors="ignore")
        except (AttributeError, UnicodeDecodeError):
            return changed_lines

        # Use Rust implementation if available (~5x faster) - REPO-244
        if HAS_RUST_DIFF_PARSER:
            return set(_rust_parse_diff(diff_text))

        # Fallback to Python implementation
        # Match diff hunks: @@ -start,count +start,count @@
        hunk_pattern = re.compile(r"@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@")

        current_line = 0
        for line in diff_text.split("\n"):
            hunk_match = hunk_pattern.match(line)
            if hunk_match:
                current_line = int(hunk_match.group(2))
                continue

            if current_line > 0:
                if line.startswith("+") and not line.startswith("+++"):
                    changed_lines.add(current_line)
                    current_line += 1
                elif line.startswith("-") and not line.startswith("---"):
                    # Deleted line - we track the line number for matching
                    changed_lines.add(current_line)
                else:
                    current_line += 1

        return changed_lines

    def _function_overlaps_changes(
        self, func: FunctionInfo, changed_lines: Set[int]
    ) -> bool:
        """Check if function's line range overlaps with changed lines.

        Args:
            func: Function information
            changed_lines: Set of changed line numbers

        Returns:
            True if any changed line falls within the function's range
        """
        func_range = set(range(func.line_start, func.line_end + 1))
        return bool(func_range & changed_lines)

    def _parse_functions_from_content(
        self, content: str, file_path: str
    ) -> List[FunctionInfo]:
        """Parse Python content to extract function information.

        Uses Rust implementation for ~3-5x speedup when available (REPO-245),
        falling back to Python AST if not installed.

        Args:
            content: Python source code
            file_path: Path to the file (for qualified names)

        Returns:
            List of FunctionInfo objects
        """
        functions: List[FunctionInfo] = []

        # Calculate module name for qualified names
        # file_path from git diff is already relative to repo root (e.g., "repotoire/api/routes/fixes.py")
        # Convert to module-style path: "repotoire.api.routes.fixes"
        file_path_obj = Path(file_path)
        if file_path_obj.is_absolute():
            # If absolute, try to make relative to repo_path
            try:
                rel_path = file_path_obj.relative_to(self.repo_path)
                module_name = str(rel_path.with_suffix("")).replace("/", ".")
            except ValueError:
                module_name = file_path_obj.stem
        else:
            # Already relative - use as-is (this is the normal case from git diffs)
            module_name = str(file_path_obj.with_suffix("")).replace("/", ".")

        # Use Rust implementation if available (~3-5x faster)
        if HAS_RUST_FUNCTION_PARSER:
            try:
                boundaries = _rust_extract_boundaries(content)
                for name, line_start, line_end in boundaries:
                    # name is like "ClassName.method" or "function_name"
                    qualified_name = f"{module_name}.{name}"

                    # Calculate LOC (non-empty, non-comment lines)
                    lines = content.split("\n")[line_start - 1 : line_end]
                    loc = sum(
                        1
                        for line in lines
                        if line.strip() and not line.strip().startswith("#")
                    )

                    if loc >= self.min_loc:
                        # Extract source code
                        source = "\n".join(lines)

                        functions.append(FunctionInfo(
                            name=name.split(".")[-1],  # Just the function name
                            qualified_name=qualified_name,
                            file_path=file_path,
                            line_start=line_start,
                            line_end=line_end,
                            loc=loc,
                            complexity=1,  # Simplified - could add Rust complexity later
                            source=source[:2000] if source else None,
                        ))
                return functions
            except Exception:
                # Fall back to Python AST on any error
                pass

        # Fallback: Python AST implementation
        try:
            tree = ast.parse(content, filename=file_path)
        except SyntaxError:
            return functions

        # Walk AST to find functions and methods
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                class_name = node.name
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        qualified_name = f"{module_name}.{class_name}.{item.name}"
                        func_info = self._create_function_info(
                            item, file_path, qualified_name, content
                        )
                        if func_info and func_info.loc >= self.min_loc:
                            functions.append(func_info)

            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                # Check if top-level
                if hasattr(tree, "body") and node in tree.body:
                    qualified_name = f"{module_name}.{node.name}"
                    func_info = self._create_function_info(
                        node, file_path, qualified_name, content
                    )
                    if func_info and func_info.loc >= self.min_loc:
                        functions.append(func_info)

        return functions

    def _create_function_info(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        file_path: str,
        qualified_name: str,
        content: str,
    ) -> Optional[FunctionInfo]:
        """Create FunctionInfo from AST node.

        Args:
            node: Function AST node
            file_path: Path to the file
            qualified_name: Fully qualified function name
            content: Full file content for extracting source

        Returns:
            FunctionInfo object or None if invalid
        """
        line_start = node.lineno
        line_end = node.end_lineno or line_start

        # Calculate LOC (non-empty, non-comment lines)
        lines = content.split("\n")[line_start - 1 : line_end]
        loc = sum(
            1
            for line in lines
            if line.strip() and not line.strip().startswith("#")
        )

        # Calculate complexity
        complexity = self._calculate_complexity(node)

        # Extract source code
        source = "\n".join(lines)

        return FunctionInfo(
            name=node.name,
            qualified_name=qualified_name,
            file_path=file_path,
            line_start=line_start,
            line_end=line_end,
            loc=loc,
            complexity=complexity,
            source=source[:2000] if source else None,  # Limit source size
        )

    def _calculate_complexity(self, node: ast.AST) -> int:
        """Calculate cyclomatic complexity of a function.

        Args:
            node: AST node

        Returns:
            Cyclomatic complexity score
        """
        complexity = 1  # Base complexity

        for child in ast.walk(node):
            if isinstance(
                child,
                (
                    ast.If,
                    ast.While,
                    ast.For,
                    ast.ExceptHandler,
                    ast.With,
                    ast.Assert,
                ),
            ):
                complexity += 1
            elif isinstance(child, ast.BoolOp):
                complexity += len(child.values) - 1

        return complexity

    def extract_buggy_functions(
        self,
        since_date: str = "2020-01-01",
        max_commits: Optional[int] = None,
        use_rust: bool = True,
    ) -> List[TrainingExample]:
        """Find functions fixed in bug commits.

        Uses Rust implementation for ~10x+ speedup when available (REPO-246).

        Args:
            since_date: Only consider commits after this date (YYYY-MM-DD)
            max_commits: Limit number of commits to process
            use_rust: Whether to use Rust implementation if available (default True)

        Returns:
            List of TrainingExample with label="buggy"
        """
        # Try Rust implementation for ~10x+ speedup (REPO-246)
        if use_rust and HAS_RUST_BUG_EXTRACTOR:
            try:
                rust_results = _rust_extract_buggy(
                    str(self.repo_path),
                    self.keywords,
                    since_date=since_date,
                    max_commits=max_commits,
                )
                buggy_functions: Dict[str, TrainingExample] = {}
                for func in rust_results:
                    buggy_functions[func.qualified_name] = TrainingExample(
                        qualified_name=func.qualified_name,
                        file_path=func.file_path,
                        label="buggy",
                        commit_sha=func.commit_sha,
                        commit_message=func.commit_message[:200],
                        commit_date=func.commit_date,
                        confidence=1.0,
                    )
                self._buggy_functions = buggy_functions
                return list(buggy_functions.values())
            except Exception:
                # Fall back to Python implementation on any error
                pass

        # Fallback: Python implementation using GitPython
        since = datetime.fromisoformat(since_date)
        buggy_functions: Dict[str, TrainingExample] = {}

        # Iterate through commits
        commits = list(self.repo.iter_commits(since=since))
        if max_commits:
            commits = commits[:max_commits]

        for commit in commits:
            if not self.is_bug_fix_commit(commit):
                continue

            changed = self.extract_changed_functions(commit)
            for qualified_name, file_path, _ in changed:
                # Only keep first bug-fix occurrence (most recent)
                if qualified_name not in buggy_functions:
                    buggy_functions[qualified_name] = TrainingExample(
                        qualified_name=qualified_name,
                        file_path=file_path,
                        label="buggy",
                        commit_sha=commit.hexsha,
                        commit_message=commit.message.strip()[:200],
                        commit_date=commit.committed_datetime.isoformat(),
                        confidence=1.0,
                    )

        self._buggy_functions = buggy_functions
        return list(buggy_functions.values())

    def extract_clean_functions(
        self,
        buggy_names: Optional[Set[str]] = None,
        sample_ratio: float = 1.0,
    ) -> List[TrainingExample]:
        """Find functions not involved in bugs.

        Args:
            buggy_names: Set of buggy function names to exclude
            sample_ratio: Ratio to sample (for balancing, 0.0-1.0)

        Returns:
            List of TrainingExample with label="clean"
        """
        if buggy_names is None:
            buggy_names = set(self._buggy_functions.keys())

        # Parse current codebase for all functions
        all_functions = self._scan_all_functions()

        # Filter out buggy functions
        clean_names = [name for name in all_functions if name not in buggy_names]

        # Sample if needed
        if sample_ratio < 1.0:
            sample_size = max(1, int(len(clean_names) * sample_ratio))
            clean_names = random.sample(clean_names, sample_size)

        clean_examples = []
        for name in clean_names:
            func_info = all_functions[name]
            clean_examples.append(
                TrainingExample(
                    qualified_name=name,
                    file_path=func_info.file_path,
                    label="clean",
                    complexity=func_info.complexity,
                    loc=func_info.loc,
                    source_code=func_info.source,
                    confidence=0.8,  # Lower confidence - clean is harder to verify
                )
            )

        return clean_examples

    def _scan_all_functions(self) -> Dict[str, FunctionInfo]:
        """Scan repository for all function definitions.

        Returns:
            Dict mapping qualified_name to FunctionInfo
        """
        if self._all_functions:
            return self._all_functions

        functions: Dict[str, FunctionInfo] = {}

        for py_file in self.repo_path.rglob("*.py"):
            # Skip test files, __pycache__, venv directories
            path_str = str(py_file)
            if (
                "test" in py_file.name.lower()
                or "__pycache__" in path_str
                or ".venv" in path_str
                or "venv" in path_str
                or "node_modules" in path_str
                or ".git" in path_str
            ):
                continue

            try:
                content = py_file.read_text(encoding="utf-8")
                rel_path = str(py_file.relative_to(self.repo_path))
                file_functions = self._parse_functions_from_content(content, rel_path)

                for func in file_functions:
                    functions[func.qualified_name] = func

            except Exception:
                continue

        self._all_functions = functions
        return functions

    def create_balanced_dataset(
        self,
        since_date: str = "2020-01-01",
        max_examples: Optional[int] = None,
    ) -> TrainingDataset:
        """Create balanced 50/50 buggy/clean dataset.

        Args:
            since_date: Only consider commits after this date
            max_examples: Maximum total examples (half buggy, half clean)

        Returns:
            Balanced TrainingDataset
        """
        # Extract buggy functions
        buggy = self.extract_buggy_functions(since_date=since_date)

        # Determine target count
        if max_examples:
            target_each = max_examples // 2
            buggy = buggy[:target_each]
        else:
            target_each = len(buggy)

        # Extract matching number of clean functions
        buggy_names = {ex.qualified_name for ex in buggy}
        clean = self.extract_clean_functions(buggy_names=buggy_names)
        random.shuffle(clean)
        clean = clean[:target_each]

        # Enrich buggy examples with complexity/LOC from current codebase
        all_functions = self._scan_all_functions()
        for ex in buggy:
            if ex.qualified_name in all_functions:
                func_info = all_functions[ex.qualified_name]
                ex.complexity = func_info.complexity
                ex.loc = func_info.loc
                ex.source_code = func_info.source

        # Combine and shuffle
        all_examples = buggy + clean
        random.shuffle(all_examples)

        # Calculate statistics
        total = len(all_examples)
        buggy_count = len(buggy)
        clean_count = len(clean)

        stats = {
            "total": total,
            "buggy": buggy_count,
            "clean": clean_count,
            "buggy_pct": round(buggy_count / total * 100, 1) if total > 0 else 0,
            "avg_complexity": round(
                sum(ex.complexity or 0 for ex in all_examples) / total, 2
            )
            if total > 0
            else 0,
            "avg_loc": round(sum(ex.loc or 0 for ex in all_examples) / total, 2)
            if total > 0
            else 0,
        }

        return TrainingDataset(
            examples=all_examples,
            repository=str(self.repo_path),
            extracted_at=datetime.now().isoformat(),
            date_range=(since_date, datetime.now().strftime("%Y-%m-%d")),
            statistics=stats,
        )

    def export_to_json(
        self, dataset: TrainingDataset, output_path: Path
    ) -> Path:
        """Export dataset to JSON file.

        Args:
            dataset: TrainingDataset to export
            output_path: Path to output file

        Returns:
            Path to the exported file
        """
        with open(output_path, "w") as f:
            json.dump(dataset.model_dump(), f, indent=2)
        return output_path

    @staticmethod
    def load_from_json(input_path: Path) -> TrainingDataset:
        """Load dataset from JSON file.

        Args:
            input_path: Path to JSON file

        Returns:
            TrainingDataset object
        """
        with open(input_path) as f:
            data = json.load(f)
        return TrainingDataset(**data)


class ActiveLearningLabeler:
    """Iterative labeling with human-in-the-loop.

    Uses uncertainty sampling to select functions where the model
    is least confident, presenting them to humans for labeling.

    Example:
        >>> labeler = ActiveLearningLabeler()
        >>> dataset = TrainingDataset(...)
        >>> refined = labeler.iterative_training(dataset, n_iterations=3)
    """

    def __init__(
        self,
        model=None,
        uncertainty_threshold: float = 0.4,
    ):
        """Initialize active learning labeler.

        Args:
            model: Trained classifier with predict_proba method (e.g., sklearn)
            uncertainty_threshold: Threshold for uncertainty (0.5 = most uncertain)
        """
        self.model = model
        self.uncertainty_threshold = uncertainty_threshold
        self.labeled_samples: Dict[str, str] = {}
        self._label_history: List[Dict] = []

    def select_uncertain_samples(
        self,
        examples: List[TrainingExample],
        n_samples: int = 20,
    ) -> List[TrainingExample]:
        """Select functions where model is uncertain.

        Uses uncertainty sampling: selects examples where predicted
        probability is closest to 0.5 (maximum uncertainty).

        Args:
            examples: Pool of unlabeled/low-confidence examples
            n_samples: Number of samples to select

        Returns:
            Most uncertain examples for human review
        """
        if self.model is None:
            # No model yet - return random samples
            return random.sample(examples, min(n_samples, len(examples)))

        # Get predictions and calculate uncertainty
        uncertainties: List[Tuple[TrainingExample, float]] = []

        for ex in examples:
            if ex.embedding is not None:
                try:
                    prob = self.model.predict_proba([ex.embedding])[0]
                    # Uncertainty = how close to 0.5 (max at 0.5, min at 0 or 1)
                    uncertainty = 1 - abs(prob[1] - 0.5) * 2
                    uncertainties.append((ex, uncertainty))
                except Exception:
                    # If prediction fails, use default uncertainty
                    uncertainties.append((ex, 0.5))
            else:
                # No embedding - medium uncertainty
                uncertainties.append((ex, 0.5))

        # Sort by uncertainty (highest first)
        uncertainties.sort(key=lambda x: x[1], reverse=True)

        return [ex for ex, _ in uncertainties[:n_samples]]

    def label_samples_interactively(
        self,
        samples: List[TrainingExample],
        show_source: bool = True,
    ) -> Dict[str, str]:
        """Present samples to human for labeling via terminal UI.

        Requires questionary package for interactive prompts.

        Args:
            samples: Examples to label
            show_source: Whether to show function source code

        Returns:
            Dict mapping qualified_name to label ("buggy" or "clean")
        """
        try:
            import questionary
        except ImportError:
            raise ImportError(
                "questionary required for interactive labeling: pip install questionary"
            )

        labels: Dict[str, str] = {}

        print("\n" + "=" * 60)
        print("Active Learning: Human Labeling Session")
        print("=" * 60)

        for i, sample in enumerate(samples):
            print(f"\n--- Sample {i + 1}/{len(samples)} ---")
            print(f"Function: {sample.qualified_name}")
            print(f"File: {sample.file_path}")

            if sample.commit_message:
                print(f"Related commit: {sample.commit_message[:100]}")

            if sample.complexity:
                print(f"Complexity: {sample.complexity}")
            if sample.loc:
                print(f"Lines of code: {sample.loc}")

            # Show function code
            if show_source and sample.source_code:
                print("\nSource code (first 20 lines):")
                print("-" * 40)
                lines = sample.source_code.split("\n")[:20]
                for line in lines:
                    print(f"  {line}")
                if len(sample.source_code.split("\n")) > 20:
                    print("  ...")
                print("-" * 40)

            choice = questionary.select(
                "Is this function likely to contain bugs?",
                choices=[
                    "buggy - Yes, likely has/had bugs",
                    "clean - No, appears clean",
                    "skip - Uncertain, skip this one",
                    "quit - Stop labeling session",
                ],
            ).ask()

            if choice is None or choice.startswith("quit"):
                print("Labeling session ended early.")
                break

            if choice and not choice.startswith("skip"):
                label = choice.split(" - ")[0]
                labels[sample.qualified_name] = label
                sample.label = label
                sample.confidence = 1.0  # Human-verified

                # Record in history
                self._label_history.append(
                    {
                        "qualified_name": sample.qualified_name,
                        "label": label,
                        "timestamp": datetime.now().isoformat(),
                    }
                )

        self.labeled_samples.update(labels)
        return labels

    def label_samples_batch(
        self,
        samples: List[TrainingExample],
        labels: Dict[str, str],
    ) -> Dict[str, str]:
        """Apply batch labels to samples (non-interactive).

        Args:
            samples: Examples to label
            labels: Dict mapping qualified_name to label

        Returns:
            Applied labels
        """
        applied: Dict[str, str] = {}

        for sample in samples:
            if sample.qualified_name in labels:
                label = labels[sample.qualified_name]
                sample.label = label
                sample.confidence = 1.0
                applied[sample.qualified_name] = label

        self.labeled_samples.update(applied)
        return applied

    def iterative_training(
        self,
        dataset: TrainingDataset,
        n_iterations: int = 5,
        samples_per_iteration: int = 20,
    ) -> TrainingDataset:
        """Active learning loop.

        1. Train model on current labels
        2. Select uncertain samples
        3. Human labels uncertain samples
        4. Retrain model
        5. Repeat

        Args:
            dataset: Initial training dataset
            n_iterations: Number of active learning iterations
            samples_per_iteration: Samples to label per iteration

        Returns:
            Improved dataset with human-refined labels
        """
        examples = dataset.examples.copy()

        for iteration in range(n_iterations):
            print(f"\n{'=' * 60}")
            print(f"Active Learning Iteration {iteration + 1}/{n_iterations}")
            print("=" * 60)

            # Train model on current labels
            self._train_model(examples)

            # Select uncertain samples (low confidence or uncertain predictions)
            unlabeled = [ex for ex in examples if ex.confidence < 1.0]

            if not unlabeled:
                print("All samples have been human-labeled!")
                break

            uncertain = self.select_uncertain_samples(
                unlabeled, n_samples=samples_per_iteration
            )

            if not uncertain:
                print("No more uncertain samples!")
                break

            # Human labeling
            print(f"Selecting {len(uncertain)} uncertain samples for labeling...")
            labels = self.label_samples_interactively(uncertain)

            print(f"Labeled {len(labels)} samples in this iteration")

            # Check if user wants to continue
            if len(labels) == 0:
                print("No labels provided. Stopping active learning.")
                break

        # Update statistics
        human_labeled = sum(1 for ex in examples if ex.confidence == 1.0)
        dataset.examples = examples
        dataset.statistics["human_labeled"] = human_labeled
        dataset.statistics["active_learning_iterations"] = iteration + 1

        return dataset

    def _train_model(self, examples: List[TrainingExample]) -> None:
        """Train classifier on current examples.

        Uses RandomForestClassifier for uncertainty estimation.

        Args:
            examples: Training examples with labels and embeddings
        """
        try:
            from sklearn.ensemble import RandomForestClassifier
        except ImportError:
            print("sklearn not available - using random sampling for uncertainty")
            return

        # Filter examples with embeddings
        labeled = [ex for ex in examples if ex.embedding is not None]
        if len(labeled) < 10:
            print(f"Only {len(labeled)} examples with embeddings - need at least 10")
            return

        X = [ex.embedding for ex in labeled]
        y = [1 if ex.label == "buggy" else 0 for ex in labeled]

        self.model = RandomForestClassifier(n_estimators=50, random_state=42)
        self.model.fit(X, y)
        print(f"Trained model on {len(labeled)} examples")

    def get_labeling_stats(self) -> Dict:
        """Get statistics about the labeling session.

        Returns:
            Dict with labeling statistics
        """
        return {
            "total_labeled": len(self.labeled_samples),
            "buggy_count": sum(
                1 for label in self.labeled_samples.values() if label == "buggy"
            ),
            "clean_count": sum(
                1 for label in self.labeled_samples.values() if label == "clean"
            ),
            "label_history_count": len(self._label_history),
        }

    def export_labels(self, output_path: Path) -> Path:
        """Export labeled samples to JSON file.

        Args:
            output_path: Path to output file

        Returns:
            Path to the exported file
        """
        export_data = {
            "labels": self.labeled_samples,
            "history": self._label_history,
            "stats": self.get_labeling_stats(),
            "exported_at": datetime.now().isoformat(),
        }

        with open(output_path, "w") as f:
            json.dump(export_data, f, indent=2)

        return output_path

    def import_labels(self, input_path: Path) -> Dict[str, str]:
        """Import previously exported labels.

        Args:
            input_path: Path to JSON file with labels

        Returns:
            Imported labels dict
        """
        with open(input_path) as f:
            data = json.load(f)

        self.labeled_samples.update(data.get("labels", {}))
        self._label_history.extend(data.get("history", []))

        return data.get("labels", {})
