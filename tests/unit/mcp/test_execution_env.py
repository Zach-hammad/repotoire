"""Tests for MCP execution environment utilities (REPO-210/211/212).

These tests verify the token-efficient utilities for data filtering,
state persistence, and skill management.
"""

import json
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import patch

import pytest

from repotoire.mcp.execution_env import (
    # REPO-210: Data Filtering
    summarize,
    top_n,
    count_by,
    to_table,
    filter_by,
    field_stats,
    group_by,
    _get_value,
    # REPO-211: State Persistence
    store,
    get,
    delete,
    list_stored,
    clear_state,
    invalidate_cache,
    cache_info,
    cached,
    _state,
    _cache,
    # REPO-212: Skill Persistence
    save_skill,
    load_skill,
    list_skills,
    skill_info,
    delete_skill,
    search_skills,
    export_skills,
    import_skills,
    get_skills_directory,
    SKILLS_DIR,
)


# =============================================================================
# Test Fixtures
# =============================================================================


@dataclass
class MockEntity:
    """Mock entity for testing with object attributes."""

    name: str
    file: str
    score: float
    complexity: int
    severity: str = "MEDIUM"


@pytest.fixture
def sample_dicts() -> List[Dict[str, Any]]:
    """Sample dict data for testing."""
    return [
        {"name": "login", "file": "auth.py", "score": 0.95, "complexity": 15, "severity": "HIGH"},
        {"name": "logout", "file": "auth.py", "score": 0.89, "complexity": 5, "severity": "LOW"},
        {"name": "process", "file": "data.py", "score": 0.75, "complexity": 25, "severity": "HIGH"},
        {"name": "validate", "file": "utils.py", "score": 0.60, "complexity": 10, "severity": "MEDIUM"},
        {"name": "connect", "file": "db.py", "score": 0.50, "complexity": 8, "severity": "LOW"},
    ]


@pytest.fixture
def sample_objects() -> List[MockEntity]:
    """Sample object data for testing."""
    return [
        MockEntity("login", "auth.py", 0.95, 15, "HIGH"),
        MockEntity("logout", "auth.py", 0.89, 5, "LOW"),
        MockEntity("process", "data.py", 0.75, 25, "HIGH"),
        MockEntity("validate", "utils.py", 0.60, 10, "MEDIUM"),
        MockEntity("connect", "db.py", 0.50, 8, "LOW"),
    ]


@pytest.fixture(autouse=True)
def clear_state_before_test():
    """Clear state and cache before each test."""
    _state.clear()
    _cache.clear()
    yield
    _state.clear()
    _cache.clear()


@pytest.fixture
def temp_skills_dir(tmp_path: Path):
    """Use temporary directory for skills during tests."""
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    with patch("repotoire.mcp.execution_env.SKILLS_DIR", skills_dir):
        yield skills_dir


# =============================================================================
# REPO-210: Data Filtering Tests
# =============================================================================


class TestGetValue:
    """Tests for _get_value helper."""

    def test_get_value_from_dict(self):
        """Get value from dictionary."""
        d = {"name": "test", "value": 42}
        assert _get_value(d, "name") == "test"
        assert _get_value(d, "value") == 42
        assert _get_value(d, "missing") is None
        assert _get_value(d, "missing", "default") == "default"

    def test_get_value_from_object(self):
        """Get value from object attribute."""
        obj = MockEntity("test", "file.py", 0.5, 10)
        assert _get_value(obj, "name") == "test"
        assert _get_value(obj, "score") == 0.5
        assert _get_value(obj, "missing") is None
        assert _get_value(obj, "missing", "default") == "default"


class TestSummarize:
    """Tests for summarize function."""

    def test_summarize_dicts(self, sample_dicts):
        """Summarize list of dicts."""
        result = summarize(sample_dicts, ["name", "score"])
        assert len(result) == 5
        assert result[0] == {"name": "login", "score": 0.95}
        assert result[1] == {"name": "logout", "score": 0.89}

    def test_summarize_objects(self, sample_objects):
        """Summarize list of objects."""
        result = summarize(sample_objects, ["name", "complexity"])
        assert len(result) == 5
        assert result[0] == {"name": "login", "complexity": 15}

    def test_summarize_with_limit(self, sample_dicts):
        """Summarize with max_items limit."""
        result = summarize(sample_dicts, ["name"], max_items=2)
        assert len(result) == 2
        assert result[0] == {"name": "login"}
        assert result[1] == {"name": "logout"}

    def test_summarize_empty_list(self):
        """Summarize empty list."""
        result = summarize([], ["name", "score"])
        assert result == []

    def test_summarize_missing_field(self, sample_dicts):
        """Summarize with non-existent field."""
        result = summarize(sample_dicts, ["name", "nonexistent"])
        assert result[0] == {"name": "login", "nonexistent": None}


class TestTopN:
    """Tests for top_n function."""

    def test_top_n_dicts(self, sample_dicts):
        """Get top N from dicts."""
        result = top_n(sample_dicts, 3, "complexity")
        assert len(result) == 3
        assert result[0]["name"] == "process"  # complexity: 25
        assert result[1]["name"] == "login"  # complexity: 15
        assert result[2]["name"] == "validate"  # complexity: 10

    def test_top_n_objects(self, sample_objects):
        """Get top N from objects."""
        result = top_n(sample_objects, 2, "score")
        assert len(result) == 2
        assert result[0].name == "login"  # score: 0.95
        assert result[1].name == "logout"  # score: 0.89

    def test_top_n_ascending(self, sample_dicts):
        """Get bottom N (ascending sort)."""
        result = top_n(sample_dicts, 2, "complexity", reverse=False)
        assert len(result) == 2
        assert result[0]["name"] == "logout"  # complexity: 5
        assert result[1]["name"] == "connect"  # complexity: 8

    def test_top_n_empty_list(self):
        """Top N from empty list."""
        result = top_n([], 5, "score")
        assert result == []

    def test_top_n_n_larger_than_list(self, sample_dicts):
        """N larger than list size."""
        result = top_n(sample_dicts, 100, "score")
        assert len(result) == 5

    def test_top_n_with_none_values(self):
        """Handle None values in sort field."""
        data = [
            {"name": "a", "score": None},
            {"name": "b", "score": 10},
            {"name": "c", "score": 5},
        ]
        result = top_n(data, 3, "score")
        assert result[0]["name"] == "b"  # score: 10
        assert result[1]["name"] == "c"  # score: 5
        assert result[2]["name"] == "a"  # score: None (sorted last)


class TestCountBy:
    """Tests for count_by function."""

    def test_count_by_dicts(self, sample_dicts):
        """Count by field in dicts."""
        result = count_by(sample_dicts, "severity")
        assert result["HIGH"] == 2
        assert result["LOW"] == 2
        assert result["MEDIUM"] == 1

    def test_count_by_objects(self, sample_objects):
        """Count by field in objects."""
        result = count_by(sample_objects, "file")
        assert result["auth.py"] == 2
        assert result["data.py"] == 1
        assert result["utils.py"] == 1
        assert result["db.py"] == 1

    def test_count_by_empty_list(self):
        """Count by on empty list."""
        result = count_by([], "field")
        assert result == {}

    def test_count_by_missing_field(self, sample_dicts):
        """Count by non-existent field."""
        result = count_by(sample_dicts, "nonexistent")
        assert result["unknown"] == 5


class TestToTable:
    """Tests for to_table function."""

    def test_to_table_basic(self, sample_dicts):
        """Generate markdown table."""
        result = to_table(sample_dicts[:2], ["name", "score"])
        lines = result.split("\n")
        assert lines[0] == "| name | score |"
        assert lines[1] == "|---|---|"
        assert "login" in lines[2]
        assert "0.95" in lines[2]

    def test_to_table_max_rows(self, sample_dicts):
        """Limit rows in table."""
        result = to_table(sample_dicts, ["name"], max_rows=2)
        lines = [l for l in result.split("\n") if l.strip()]
        assert len(lines) == 4  # header + separator + 2 rows

    def test_to_table_truncation(self):
        """Truncate long values."""
        data = [{"name": "a" * 100}]
        result = to_table(data, ["name"], max_width=10)
        assert "aaaaaaa..." in result

    def test_to_table_empty_list(self):
        """Table from empty list."""
        result = to_table([], ["name", "score"])
        lines = result.split("\n")
        assert len(lines) == 2  # Just header and separator


class TestFilterBy:
    """Tests for filter_by function."""

    def test_filter_by_equality(self, sample_dicts):
        """Filter by equality."""
        result = filter_by(sample_dicts, severity="HIGH")
        assert len(result) == 2
        assert all(r["severity"] == "HIGH" for r in result)

    def test_filter_by_lambda(self, sample_dicts):
        """Filter by lambda condition."""
        result = filter_by(sample_dicts, complexity=lambda x: x > 10)
        assert len(result) == 2
        assert result[0]["name"] == "login"  # complexity: 15
        assert result[1]["name"] == "process"  # complexity: 25

    def test_filter_by_multiple_conditions(self, sample_dicts):
        """Filter by multiple conditions (AND)."""
        result = filter_by(
            sample_dicts, severity="HIGH", complexity=lambda x: x > 10
        )
        assert len(result) == 2

    def test_filter_by_no_match(self, sample_dicts):
        """Filter with no matches."""
        result = filter_by(sample_dicts, severity="CRITICAL")
        assert len(result) == 0

    def test_filter_by_objects(self, sample_objects):
        """Filter objects."""
        result = filter_by(sample_objects, score=lambda x: x > 0.8)
        assert len(result) == 2
        assert result[0].name == "login"
        assert result[1].name == "logout"


class TestFieldStats:
    """Tests for field_stats function."""

    def test_field_stats_basic(self, sample_dicts):
        """Calculate basic statistics."""
        result = field_stats(sample_dicts, "complexity")
        assert result["min"] == 5
        assert result["max"] == 25
        assert result["count"] == 5
        assert result["sum"] == 63  # 15 + 5 + 25 + 10 + 8
        assert result["mean"] == 12.6  # 63 / 5
        assert result["median"] == 10

    def test_field_stats_empty_list(self):
        """Stats from empty list."""
        result = field_stats([], "score")
        assert result == {"min": 0, "max": 0, "mean": 0, "median": 0, "sum": 0, "count": 0}

    def test_field_stats_non_numeric(self):
        """Stats with non-numeric values."""
        data = [{"val": "string"}, {"val": None}, {"val": 10}]
        result = field_stats(data, "val")
        assert result["count"] == 1
        assert result["sum"] == 10


class TestGroupBy:
    """Tests for group_by function."""

    def test_group_by_basic(self, sample_dicts):
        """Group by field."""
        result = group_by(sample_dicts, "severity")
        assert len(result["HIGH"]) == 2
        assert len(result["LOW"]) == 2
        assert len(result["MEDIUM"]) == 1

    def test_group_by_objects(self, sample_objects):
        """Group objects."""
        result = group_by(sample_objects, "file")
        assert len(result["auth.py"]) == 2

    def test_group_by_empty_list(self):
        """Group empty list."""
        result = group_by([], "field")
        assert result == {}


# =============================================================================
# REPO-211: State Persistence Tests
# =============================================================================


class TestStateManagement:
    """Tests for store/get/delete/list_stored/clear_state."""

    def test_store_and_get(self):
        """Store and retrieve value."""
        store("key1", "value1")
        assert get("key1") == "value1"

    def test_get_default(self):
        """Get with default for missing key."""
        assert get("missing") is None
        assert get("missing", "default") == "default"

    def test_store_overwrites(self):
        """Store overwrites existing value."""
        store("key", "value1")
        store("key", "value2")
        assert get("key") == "value2"

    def test_delete_existing(self):
        """Delete existing key."""
        store("key", "value")
        assert delete("key") is True
        assert get("key") is None

    def test_delete_missing(self):
        """Delete non-existent key."""
        assert delete("missing") is False

    def test_list_stored(self):
        """List all stored keys."""
        store("a", 1)
        store("b", 2)
        store("c", 3)
        keys = list_stored()
        assert set(keys) == {"a", "b", "c"}

    def test_clear_state(self):
        """Clear all state."""
        store("a", 1)
        store("b", 2)
        count = clear_state()
        assert count == 2
        assert list_stored() == []

    def test_store_complex_types(self):
        """Store complex Python objects."""
        data = {"nested": {"list": [1, 2, 3]}}
        store("complex", data)
        assert get("complex") == data


class TestCaching:
    """Tests for cache_query, invalidate_cache, cache_info, @cached."""

    def test_invalidate_cache_single(self):
        """Invalidate single cache entry."""
        _cache["key1"] = {"data": "value1", "time": time.time()}
        _cache["key2"] = {"data": "value2", "time": time.time()}
        count = invalidate_cache("key1")
        assert count == 1
        assert "key1" not in _cache
        assert "key2" in _cache

    def test_invalidate_cache_all(self):
        """Invalidate all cache."""
        _cache["key1"] = {"data": "value1", "time": time.time()}
        _cache["key2"] = {"data": "value2", "time": time.time()}
        count = invalidate_cache()
        assert count == 2
        assert len(_cache) == 0

    def test_invalidate_cache_missing(self):
        """Invalidate non-existent key."""
        count = invalidate_cache("missing")
        assert count == 0

    def test_cache_info(self):
        """Get cache statistics."""
        _cache["key1"] = {"data": "value1", "time": time.time()}
        info = cache_info()
        assert info["count"] == 1
        assert "key1" in info["keys"]
        assert "key1" in info["entries"]
        assert "age_seconds" in info["entries"]["key1"]

    def test_cached_decorator(self):
        """Test @cached decorator."""
        call_count = 0

        @cached("test_func", ttl=10)
        def expensive_function():
            nonlocal call_count
            call_count += 1
            return "result"

        # First call computes
        result1 = expensive_function()
        assert result1 == "result"
        assert call_count == 1

        # Second call uses cache
        result2 = expensive_function()
        assert result2 == "result"
        assert call_count == 1  # Not incremented

    def test_cached_decorator_default_key(self):
        """Test @cached with default key (function name)."""
        @cached(ttl=10)
        def my_function():
            return "result"

        my_function()
        assert "my_function" in _cache

    def test_cached_ttl_expiry(self):
        """Test cache expiry."""
        call_count = 0

        @cached("expiring", ttl=0.1)  # 100ms TTL
        def quick_expire():
            nonlocal call_count
            call_count += 1
            return "result"

        quick_expire()
        assert call_count == 1

        time.sleep(0.15)  # Wait for expiry

        quick_expire()
        assert call_count == 2  # Called again after expiry


# =============================================================================
# REPO-212: Skill Persistence Tests
# =============================================================================


class TestSkillManagement:
    """Tests for skill persistence functions."""

    def test_save_and_load_skill(self, temp_skills_dir):
        """Save and load a skill."""
        code = """
def my_function():
    return "hello"
"""
        path = save_skill("test_skill", code, description="Test skill")
        assert Path(path).exists()

        # Load into a test namespace
        namespace = {}
        load_skill("test_skill", namespace)
        assert "my_function" in namespace
        assert namespace["my_function"]() == "hello"

    def test_save_skill_with_tags(self, temp_skills_dir):
        """Save skill with tags."""
        save_skill(
            "tagged_skill",
            "x = 1",
            description="Tagged skill",
            tags=["analysis", "code-smell"],
        )
        info = skill_info("tagged_skill")
        assert info["tags"] == ["analysis", "code-smell"]

    def test_save_skill_overwrite(self, temp_skills_dir):
        """Overwrite existing skill."""
        save_skill("overwrite_test", "x = 1")

        with pytest.raises(ValueError, match="already exists"):
            save_skill("overwrite_test", "x = 2")

        save_skill("overwrite_test", "x = 2", overwrite=True)
        info = skill_info("overwrite_test")
        assert "x = 2" in info["preview"]

    def test_save_skill_invalid_name(self, temp_skills_dir):
        """Reject invalid skill names."""
        with pytest.raises(ValueError, match="Invalid skill name"):
            save_skill("invalid-name", "x = 1")  # Hyphen not allowed

        with pytest.raises(ValueError, match="Invalid skill name"):
            save_skill("", "x = 1")  # Empty name

    def test_load_skill_not_found(self, temp_skills_dir):
        """Load non-existent skill."""
        with pytest.raises(FileNotFoundError, match="not found"):
            load_skill("nonexistent")

    def test_list_skills(self, temp_skills_dir):
        """List all skills."""
        save_skill("skill1", "x = 1")
        save_skill("skill2", "y = 2")

        skills = list_skills()
        assert set(skills) == {"skill1", "skill2"}

    def test_list_skills_by_tag(self, temp_skills_dir):
        """List skills filtered by tag."""
        save_skill("tagged", "x = 1", tags=["analysis"])
        save_skill("untagged", "y = 2")

        skills = list_skills(tag="analysis")
        assert skills == ["tagged"]

    def test_skill_info(self, temp_skills_dir):
        """Get skill info."""
        save_skill(
            "info_test",
            "def foo(): pass",
            description="Info test skill",
            tags=["test"],
        )

        info = skill_info("info_test")
        assert info["name"] == "info_test"
        assert info["description"] == "Info test skill"
        assert info["tags"] == ["test"]
        assert "lines" in info
        assert "size_bytes" in info
        assert "preview" in info

    def test_skill_info_not_found(self, temp_skills_dir):
        """Skill info for non-existent skill."""
        with pytest.raises(FileNotFoundError):
            skill_info("nonexistent")

    def test_delete_skill(self, temp_skills_dir):
        """Delete a skill."""
        save_skill("to_delete", "x = 1")
        assert delete_skill("to_delete") is True
        assert "to_delete" not in list_skills()

    def test_delete_skill_not_found(self, temp_skills_dir):
        """Delete non-existent skill."""
        assert delete_skill("nonexistent") is False

    def test_search_skills(self, temp_skills_dir):
        """Search skills."""
        save_skill("find_god_classes", "x = 1", description="Find god classes", tags=["classes"])
        save_skill("check_naming", "y = 2", description="Check naming conventions")

        results = search_skills("class")
        assert len(results) == 1
        assert results[0]["name"] == "find_god_classes"

        results = search_skills("naming")
        assert len(results) == 1
        assert results[0]["name"] == "check_naming"

    def test_export_import_skills(self, temp_skills_dir, tmp_path):
        """Export and import skills."""
        save_skill("export_test", "x = 1", description="Export test")

        # Export
        export_path = str(tmp_path / "skills_export.zip")
        result_path = export_skills(export_path)
        assert Path(result_path).exists()

        # Clear and import
        delete_skill("export_test")
        assert list_skills() == []

        count = import_skills(export_path)
        assert count >= 1
        assert "export_test" in list_skills()

    def test_import_skills_no_overwrite(self, temp_skills_dir, tmp_path):
        """Import without overwriting existing."""
        save_skill("existing", "x = 1")
        original_info = skill_info("existing")

        # Create archive with same skill name
        export_path = str(tmp_path / "export.zip")
        export_skills(export_path)

        # Modify the existing skill
        save_skill("existing", "y = 2", overwrite=True)

        # Import without overwrite
        import_skills(export_path, overwrite=False)

        # Should still have modified version
        new_info = skill_info("existing")
        assert "y = 2" in new_info["preview"]

    def test_get_skills_directory(self, temp_skills_dir):
        """Get skills directory path."""
        # With patch, this returns the temp dir
        # Without patch, returns default
        path = get_skills_directory()
        assert isinstance(path, Path)


# =============================================================================
# Integration Tests
# =============================================================================


class TestIntegration:
    """Integration tests combining multiple utilities."""

    def test_filter_summarize_table(self, sample_dicts):
        """Pipeline: filter → summarize → table."""
        # Filter high severity
        high = filter_by(sample_dicts, severity="HIGH")
        # Summarize to key fields
        summary = summarize(high, ["name", "complexity"])
        # Format as table
        table = to_table(summary, ["name", "complexity"])

        assert "login" in table
        assert "process" in table
        assert "logout" not in table  # LOW severity

    def test_top_n_stats(self, sample_dicts):
        """Get top N then calculate stats."""
        top = top_n(sample_dicts, 3, "complexity")
        stats = field_stats(top, "complexity")

        assert stats["count"] == 3
        assert stats["max"] == 25
        assert stats["min"] == 10

    def test_store_filtered_results(self, sample_dicts):
        """Store filtered results for later use."""
        # First analysis
        critical = filter_by(sample_dicts, severity="HIGH")
        store("critical_issues", critical)

        # Later retrieval
        stored = get("critical_issues")
        assert len(stored) == 2

        # Further analysis
        counts = count_by(stored, "file")
        assert counts["auth.py"] == 1
        assert counts["data.py"] == 1


# =============================================================================
# Thread Safety Tests
# =============================================================================


class TestThreadSafety:
    """Tests for thread safety of state operations."""

    def test_concurrent_store_get(self):
        """Concurrent store/get operations."""
        import threading

        errors = []

        def worker(i):
            try:
                for _ in range(100):
                    store(f"key_{i}", i)
                    val = get(f"key_{i}")
                    if val != i:
                        errors.append(f"Expected {i}, got {val}")
            except Exception as e:
                errors.append(str(e))

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0, f"Errors: {errors}"

    def test_concurrent_cache_operations(self):
        """Concurrent cache operations."""
        import threading

        errors = []

        def worker(i):
            try:
                for _ in range(50):
                    _cache[f"key_{i}"] = {"data": i, "time": time.time()}
                    invalidate_cache(f"key_{i}")
            except Exception as e:
                errors.append(str(e))

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0, f"Errors: {errors}"
