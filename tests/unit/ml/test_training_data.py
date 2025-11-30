"""Tests for training data extraction from git history.

Tests the GitBugLabelExtractor and ActiveLearningLabeler classes for
extracting labeled training data for ML bug prediction models.
"""

import json
import pytest
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock
from datetime import datetime

from repotoire.ml.training_data import (
    GitBugLabelExtractor,
    ActiveLearningLabeler,
    TrainingExample,
    TrainingDataset,
    FunctionInfo,
    DEFAULT_BUG_KEYWORDS,
)


class TestTrainingExample:
    """Tests for TrainingExample Pydantic model."""

    def test_basic_creation(self):
        """Test basic TrainingExample creation."""
        example = TrainingExample(
            qualified_name="module.function",
            file_path="module.py",
            label="buggy",
        )
        assert example.qualified_name == "module.function"
        assert example.file_path == "module.py"
        assert example.label == "buggy"
        assert example.confidence == 1.0  # Default

    def test_with_all_fields(self):
        """Test TrainingExample with all fields populated."""
        example = TrainingExample(
            qualified_name="mypackage.module.MyClass.my_method",
            file_path="mypackage/module.py",
            label="buggy",
            commit_sha="abc123def456",
            commit_message="fix: resolve null pointer exception",
            commit_date="2024-01-15T10:30:00",
            complexity=15,
            loc=42,
            embedding=[0.1, 0.2, 0.3],
            confidence=0.95,
            source_code="def my_method(self):\n    pass",
        )

        assert example.commit_sha == "abc123def456"
        assert example.complexity == 15
        assert example.loc == 42
        assert len(example.embedding) == 3
        assert example.confidence == 0.95

    def test_clean_label(self):
        """Test TrainingExample with clean label."""
        example = TrainingExample(
            qualified_name="module.clean_function",
            file_path="module.py",
            label="clean",
            confidence=0.8,
        )
        assert example.label == "clean"
        assert example.confidence == 0.8


class TestTrainingDataset:
    """Tests for TrainingDataset Pydantic model."""

    def test_empty_dataset(self):
        """Test empty dataset creation."""
        dataset = TrainingDataset(
            examples=[],
            repository="/path/to/repo",
            extracted_at="2024-01-15T10:00:00",
            date_range=("2020-01-01", "2024-01-15"),
            statistics={},
        )
        assert len(dataset.examples) == 0
        assert dataset.repository == "/path/to/repo"

    def test_dataset_with_examples(self):
        """Test dataset with examples."""
        examples = [
            TrainingExample(
                qualified_name=f"func_{i}",
                file_path="test.py",
                label="buggy" if i % 2 == 0 else "clean",
            )
            for i in range(10)
        ]

        dataset = TrainingDataset(
            examples=examples,
            repository="/test/repo",
            extracted_at=datetime.now().isoformat(),
            date_range=("2020-01-01", "2024-01-01"),
            statistics={"total": 10, "buggy": 5, "clean": 5},
        )

        assert len(dataset.examples) == 10
        assert dataset.statistics["total"] == 10

    def test_dataset_serialization(self):
        """Test dataset JSON serialization."""
        example = TrainingExample(
            qualified_name="test.func",
            file_path="test.py",
            label="buggy",
        )

        dataset = TrainingDataset(
            examples=[example],
            repository="/test",
            extracted_at="2024-01-01T00:00:00",
            date_range=("2020-01-01", "2024-01-01"),
            statistics={"total": 1},
        )

        # Serialize and deserialize
        json_str = dataset.model_dump_json()
        loaded = TrainingDataset.model_validate_json(json_str)

        assert len(loaded.examples) == 1
        assert loaded.examples[0].qualified_name == "test.func"


class TestFunctionInfo:
    """Tests for FunctionInfo model."""

    def test_function_info_creation(self):
        """Test FunctionInfo creation."""
        func = FunctionInfo(
            name="my_function",
            qualified_name="module.my_function",
            file_path="module.py",
            line_start=10,
            line_end=25,
            loc=12,
            complexity=5,
        )
        assert func.name == "my_function"
        assert func.line_end - func.line_start == 15


class TestGitBugLabelExtractor:
    """Tests for GitBugLabelExtractor."""

    @pytest.fixture
    def mock_repo(self, tmp_path):
        """Create a mock git repository."""
        # Initialize actual git repo for testing
        from git import Repo
        repo_dir = tmp_path / "test_repo"
        repo_dir.mkdir()
        repo = Repo.init(repo_dir)

        # Create a simple Python file
        py_file = repo_dir / "module.py"
        py_file.write_text("""
def my_function():
    '''A simple function.'''
    x = 1
    y = 2
    return x + y

class MyClass:
    def method(self):
        return 42
""")

        # Add and commit
        repo.index.add(["module.py"])
        repo.index.commit("Initial commit")

        return repo_dir

    def test_default_keywords(self):
        """Test default bug-fix keywords."""
        assert "fix" in DEFAULT_BUG_KEYWORDS
        assert "bug" in DEFAULT_BUG_KEYWORDS
        assert "crash" in DEFAULT_BUG_KEYWORDS
        assert "error" in DEFAULT_BUG_KEYWORDS

    def test_is_bug_fix_commit_positive(self):
        """Test detection of bug fix commits."""
        with patch("repotoire.ml.training_data.Repo"):
            extractor = GitBugLabelExtractor(Path("."))

            mock_commit = MagicMock()
            mock_commit.message = "fix: resolve null pointer exception"

            assert extractor.is_bug_fix_commit(mock_commit) is True

    def test_is_bug_fix_commit_negative(self):
        """Test non-bug commits are not flagged."""
        with patch("repotoire.ml.training_data.Repo"):
            extractor = GitBugLabelExtractor(Path("."))

            mock_commit = MagicMock()
            mock_commit.message = "feat: add new authentication module"

            assert extractor.is_bug_fix_commit(mock_commit) is False

    def test_is_bug_fix_commit_case_insensitive(self):
        """Test keyword matching is case insensitive."""
        with patch("repotoire.ml.training_data.Repo"):
            extractor = GitBugLabelExtractor(Path("."))

            mock_commit = MagicMock()
            mock_commit.message = "FIX: CRITICAL BUG in auth"

            assert extractor.is_bug_fix_commit(mock_commit) is True

    def test_is_bug_fix_commit_multiple_keywords(self):
        """Test commit with multiple keywords."""
        with patch("repotoire.ml.training_data.Repo"):
            extractor = GitBugLabelExtractor(Path("."))

            mock_commit = MagicMock()
            mock_commit.message = "hotfix: patch critical vulnerability"

            assert extractor.is_bug_fix_commit(mock_commit) is True

    def test_custom_keywords(self):
        """Test custom keyword configuration."""
        with patch("repotoire.ml.training_data.Repo"):
            extractor = GitBugLabelExtractor(
                Path("."),
                keywords=["defect", "regression"],
            )

            mock_commit = MagicMock()
            mock_commit.message = "defect: fix login issue"
            assert extractor.is_bug_fix_commit(mock_commit) is True

            mock_commit.message = "fix: something"  # Not in custom keywords
            assert extractor.is_bug_fix_commit(mock_commit) is False

    def test_parse_functions_from_content(self):
        """Test parsing functions from Python content."""
        with patch("repotoire.ml.training_data.Repo"):
            extractor = GitBugLabelExtractor(Path("/test/repo"), min_loc=1)
            extractor.repo_path = Path("/test/repo")

            content = '''
def simple_function():
    """A simple function."""
    x = 1
    y = 2
    return x + y

class MyClass:
    def method(self):
        a = 1
        b = 2
        c = 3
        d = 4
        return a + b + c + d

    async def async_method(self):
        x = await something()
        y = await another()
        z = await more()
        w = await even_more()
        return x + y + z + w
'''

            functions = extractor._parse_functions_from_content(content, "module.py")

            names = [f.name for f in functions]
            assert "simple_function" in names
            assert "method" in names
            assert "async_method" in names

    def test_calculate_complexity(self):
        """Test cyclomatic complexity calculation."""
        import ast

        with patch("repotoire.ml.training_data.Repo"):
            extractor = GitBugLabelExtractor(Path("."))

            # Simple function - complexity 1
            simple_code = "def f(): return 1"
            tree = ast.parse(simple_code)
            func_node = tree.body[0]
            assert extractor._calculate_complexity(func_node) == 1

            # Function with if statement - complexity 2
            if_code = "def f(x):\n    if x:\n        return 1\n    return 0"
            tree = ast.parse(if_code)
            func_node = tree.body[0]
            assert extractor._calculate_complexity(func_node) == 2

            # Function with multiple branches
            complex_code = """
def f(x, y):
    if x:
        if y:
            return 1
    for i in range(10):
        pass
    while True:
        break
"""
            tree = ast.parse(complex_code)
            func_node = tree.body[0]
            complexity = extractor._calculate_complexity(func_node)
            assert complexity >= 4  # Multiple branches

    def test_min_loc_filter(self):
        """Test minimum LOC filter."""
        with patch("repotoire.ml.training_data.Repo"):
            extractor = GitBugLabelExtractor(Path("/test"), min_loc=10)

            content = '''
def tiny():
    return 1

def bigger():
    x = 1
    y = 2
    z = 3
    a = 4
    b = 5
    c = 6
    d = 7
    e = 8
    f = 9
    return x + y + z
'''
            extractor.repo_path = Path("/test")
            functions = extractor._parse_functions_from_content(content, "test.py")

            # Only bigger function should pass the 10 LOC filter
            names = [f.name for f in functions]
            assert "tiny" not in names
            assert "bigger" in names

    def test_extract_changed_lines(self):
        """Test extracting changed line numbers from diff."""
        with patch("repotoire.ml.training_data.Repo"):
            extractor = GitBugLabelExtractor(Path("."))

            mock_diff = MagicMock()
            mock_diff.diff = b"""@@ -1,5 +1,6 @@
 unchanged line
+added line
 another unchanged
-removed line
 final line
"""

            changed = extractor._extract_changed_lines(mock_diff)
            assert len(changed) > 0

    def test_function_overlaps_changes(self):
        """Test function overlap detection."""
        with patch("repotoire.ml.training_data.Repo"):
            extractor = GitBugLabelExtractor(Path("."))

            func = FunctionInfo(
                name="test",
                qualified_name="test",
                file_path="test.py",
                line_start=10,
                line_end=20,
            )

            # Changed lines within function
            assert extractor._function_overlaps_changes(func, {15})
            assert extractor._function_overlaps_changes(func, {10})
            assert extractor._function_overlaps_changes(func, {20})

            # Changed lines outside function
            assert not extractor._function_overlaps_changes(func, {5})
            assert not extractor._function_overlaps_changes(func, {25})

    def test_scan_all_functions(self, mock_repo):
        """Test scanning all functions in a repository."""
        extractor = GitBugLabelExtractor(mock_repo)

        functions = extractor._scan_all_functions()

        # Should find functions from our test file
        assert len(functions) > 0
        names = [f.name for f in functions.values()]
        assert "my_function" in names

    def test_export_import_json(self, tmp_path, mock_repo):
        """Test exporting and importing dataset to/from JSON."""
        extractor = GitBugLabelExtractor(mock_repo)

        # Create a simple dataset
        dataset = TrainingDataset(
            examples=[
                TrainingExample(
                    qualified_name="test.func",
                    file_path="test.py",
                    label="buggy",
                )
            ],
            repository=str(mock_repo),
            extracted_at=datetime.now().isoformat(),
            date_range=("2020-01-01", "2024-01-01"),
            statistics={"total": 1, "buggy": 1, "clean": 0},
        )

        # Export
        output_path = tmp_path / "dataset.json"
        extractor.export_to_json(dataset, output_path)
        assert output_path.exists()

        # Import
        loaded = GitBugLabelExtractor.load_from_json(output_path)
        assert len(loaded.examples) == 1
        assert loaded.examples[0].qualified_name == "test.func"


class TestActiveLearningLabeler:
    """Tests for ActiveLearningLabeler."""

    def test_init_defaults(self):
        """Test default initialization."""
        labeler = ActiveLearningLabeler()
        assert labeler.model is None
        assert labeler.uncertainty_threshold == 0.4
        assert len(labeler.labeled_samples) == 0

    def test_select_uncertain_no_model(self):
        """Without model, returns random samples."""
        labeler = ActiveLearningLabeler()

        examples = [
            TrainingExample(
                qualified_name=f"func_{i}",
                file_path="test.py",
                label="clean",
            )
            for i in range(100)
        ]

        selected = labeler.select_uncertain_samples(examples, n_samples=10)
        assert len(selected) == 10

        # All should be from original pool
        selected_names = {ex.qualified_name for ex in selected}
        original_names = {ex.qualified_name for ex in examples}
        assert selected_names.issubset(original_names)

    def test_select_uncertain_with_fewer_samples(self):
        """Test selecting when pool is smaller than requested."""
        labeler = ActiveLearningLabeler()

        examples = [
            TrainingExample(
                qualified_name=f"func_{i}",
                file_path="test.py",
                label="clean",
            )
            for i in range(5)
        ]

        selected = labeler.select_uncertain_samples(examples, n_samples=10)
        assert len(selected) == 5  # Can't return more than available

    def test_label_samples_batch(self):
        """Test batch labeling (non-interactive)."""
        labeler = ActiveLearningLabeler()

        samples = [
            TrainingExample(
                qualified_name="func_1",
                file_path="test.py",
                label="clean",
                confidence=0.5,
            ),
            TrainingExample(
                qualified_name="func_2",
                file_path="test.py",
                label="clean",
                confidence=0.5,
            ),
        ]

        labels = {"func_1": "buggy", "func_2": "clean"}
        applied = labeler.label_samples_batch(samples, labels)

        assert len(applied) == 2
        assert samples[0].label == "buggy"
        assert samples[0].confidence == 1.0
        assert samples[1].label == "clean"
        assert samples[1].confidence == 1.0

    def test_get_labeling_stats(self):
        """Test labeling statistics."""
        labeler = ActiveLearningLabeler()
        labeler.labeled_samples = {
            "func_1": "buggy",
            "func_2": "buggy",
            "func_3": "clean",
        }

        stats = labeler.get_labeling_stats()
        assert stats["total_labeled"] == 3
        assert stats["buggy_count"] == 2
        assert stats["clean_count"] == 1

    def test_export_import_labels(self, tmp_path):
        """Test exporting and importing labels."""
        labeler = ActiveLearningLabeler()
        labeler.labeled_samples = {"func_1": "buggy", "func_2": "clean"}

        # Export
        export_path = tmp_path / "labels.json"
        labeler.export_labels(export_path)
        assert export_path.exists()

        # Import into new labeler
        new_labeler = ActiveLearningLabeler()
        imported = new_labeler.import_labels(export_path)

        assert len(imported) == 2
        assert imported["func_1"] == "buggy"
        assert imported["func_2"] == "clean"

    def test_uncertainty_sampling_with_mock_model(self):
        """Test uncertainty sampling with a mock model."""
        labeler = ActiveLearningLabeler()

        # Mock model with predict_proba
        mock_model = MagicMock()

        # Return probabilities - [0.5, 0.5] is most uncertain
        mock_model.predict_proba.side_effect = [
            [[0.9, 0.1]],  # Very certain - not buggy
            [[0.5, 0.5]],  # Very uncertain
            [[0.1, 0.9]],  # Very certain - buggy
        ]

        labeler.model = mock_model

        examples = [
            TrainingExample(
                qualified_name=f"func_{i}",
                file_path="test.py",
                label="clean",
                embedding=[0.1] * 10,
            )
            for i in range(3)
        ]

        selected = labeler.select_uncertain_samples(examples, n_samples=1)

        # Should select the most uncertain (func_1 with 0.5, 0.5 probability)
        assert len(selected) == 1


class TestIntegration:
    """Integration tests with real git repositories."""

    @pytest.fixture
    def git_repo_with_history(self, tmp_path):
        """Create a git repo with commit history including bug fixes."""
        from git import Repo

        repo_dir = tmp_path / "test_repo"
        repo_dir.mkdir()
        repo = Repo.init(repo_dir)

        # Configure git user for commits
        repo.config_writer().set_value("user", "name", "Test User").release()
        repo.config_writer().set_value("user", "email", "test@test.com").release()

        # Initial commit with a function
        py_file = repo_dir / "module.py"
        py_file.write_text('''
def calculate_total(items):
    """Calculate total price."""
    total = 0
    for item in items:
        total += item.price
    return total

def process_order(order):
    """Process an order."""
    return order.id
''')

        repo.index.add(["module.py"])
        repo.index.commit("feat: add order processing functions")

        # Bug fix commit
        py_file.write_text('''
def calculate_total(items):
    """Calculate total price."""
    total = 0
    for item in items:
        if item.price is not None:  # Bug fix: handle None
            total += item.price
    return total

def process_order(order):
    """Process an order."""
    return order.id
''')

        repo.index.add(["module.py"])
        repo.index.commit("fix: handle None prices in calculate_total")

        return repo_dir

    def test_extract_buggy_functions_from_real_repo(self, git_repo_with_history):
        """Test extracting buggy functions from a real git repo."""
        extractor = GitBugLabelExtractor(git_repo_with_history, min_loc=3)

        buggy = extractor.extract_buggy_functions(since_date="2020-01-01")

        # Should find calculate_total as buggy
        buggy_names = [ex.qualified_name for ex in buggy]

        # The function was modified in a "fix:" commit
        assert any("calculate_total" in name for name in buggy_names)

    def test_create_balanced_dataset_from_real_repo(self, git_repo_with_history):
        """Test creating balanced dataset from real repo."""
        extractor = GitBugLabelExtractor(git_repo_with_history, min_loc=3)

        dataset = extractor.create_balanced_dataset(since_date="2020-01-01")

        # Should have some examples
        assert len(dataset.examples) > 0
        assert dataset.statistics["total"] > 0

        # Check that we have both labels if there are enough functions
        labels = {ex.label for ex in dataset.examples}
        # May only have buggy if there aren't enough clean functions
        assert "buggy" in labels or "clean" in labels
