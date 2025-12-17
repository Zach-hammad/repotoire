"""Unit tests for Rust-based diff parsing (REPO-244).

Tests the fast unified diff parser used by GitBugLabelExtractor
for ML training data extraction.
"""

import pytest

# Skip tests if Rust module not available
try:
    from repotoire_fast import (
        parse_diff_changed_lines,
        parse_diff_changed_lines_batch,
    )
    RUST_AVAILABLE = True
except ImportError:
    RUST_AVAILABLE = False


@pytest.mark.skipif(not RUST_AVAILABLE, reason="Rust module not available")
class TestParseDiffChangedLines:
    """Test parse_diff_changed_lines function."""

    def test_single_addition(self):
        """Test parsing a diff with a single added line."""
        diff = """--- a/test.py
+++ b/test.py
@@ -1,3 +1,4 @@
 unchanged line 1
+added line 2
 unchanged line 3
 unchanged line 4"""

        result = parse_diff_changed_lines(diff)
        assert result == [2]

    def test_multiple_additions(self):
        """Test parsing a diff with multiple added lines."""
        diff = """@@ -1,2 +1,4 @@
 line 1
+added line 2
+added line 3
 line 4"""

        result = parse_diff_changed_lines(diff)
        assert result == [2, 3]

    def test_multiple_hunks(self):
        """Test parsing a diff with multiple hunks."""
        diff = """@@ -10,3 +12,4 @@
 context
+added at 13
 more context
@@ -50,2 +54,3 @@
 context
+added at 55"""

        result = parse_diff_changed_lines(diff)
        assert result == [13, 55]

    def test_deletion_tracking(self):
        """Test that deletions are tracked for overlap detection."""
        diff = """@@ -1,4 +1,3 @@
 line 1
-deleted line
 line 3
 line 4"""

        result = parse_diff_changed_lines(diff)
        # Deletions should be tracked at their position
        assert 2 in result

    def test_mixed_additions_and_deletions(self):
        """Test a diff with both additions and deletions."""
        diff = """@@ -1,4 +1,4 @@
 line 1
-old line 2
+new line 2
 line 3
 line 4"""

        result = parse_diff_changed_lines(diff)
        # Both the deletion position and addition should be tracked
        assert 2 in result

    def test_empty_diff(self):
        """Test parsing an empty diff."""
        result = parse_diff_changed_lines("")
        assert result == []

    def test_no_hunk_headers(self):
        """Test diff text without hunk headers."""
        diff = """--- a/file.py
+++ b/file.py
some text without hunks"""

        result = parse_diff_changed_lines(diff)
        assert result == []

    def test_hunk_without_count(self):
        """Test hunk headers without the optional count (defaults to 1)."""
        diff = """@@ -1 +1 @@
-old
+new"""

        result = parse_diff_changed_lines(diff)
        assert 1 in result

    def test_file_header_not_counted(self):
        """Test that +++ file header is not counted as an addition."""
        diff = """--- a/old.py
+++ b/new.py
@@ -1,2 +1,3 @@
 line 1
+actual addition
 line 2"""

        result = parse_diff_changed_lines(diff)
        # Should only have the actual addition, not the +++ header
        assert result == [2]

    def test_no_newline_at_eof_ignored(self):
        """Test that 'No newline at end of file' marker is handled."""
        diff = r"""@@ -1,2 +1,3 @@
 line 1
+added
 line 2
\ No newline at end of file"""

        result = parse_diff_changed_lines(diff)
        # Should not crash and should capture the addition
        assert 2 in result

    def test_large_line_numbers(self):
        """Test handling of large line numbers."""
        diff = """@@ -1000,3 +2000,4 @@
 context
+added at 2001
 more context"""

        result = parse_diff_changed_lines(diff)
        assert result == [2001]

    def test_consecutive_deletions(self):
        """Test multiple consecutive deleted lines."""
        diff = """@@ -1,5 +1,2 @@
 line 1
-deleted 1
-deleted 2
-deleted 3
 line 5"""

        result = parse_diff_changed_lines(diff)
        # All deletions should map to line 2 position (they don't advance the counter)
        assert 2 in result


@pytest.mark.skipif(not RUST_AVAILABLE, reason="Rust module not available")
class TestParseDiffChangedLinesBatch:
    """Test batch diff parsing."""

    def test_batch_multiple_diffs(self):
        """Test processing multiple diffs in batch."""
        diff1 = """@@ -1,2 +1,3 @@
 line 1
+added
 line 2"""

        diff2 = """@@ -10,2 +10,3 @@
 context
+another add"""

        results = parse_diff_changed_lines_batch([diff1, diff2])

        assert len(results) == 2
        assert results[0] == [2]
        assert results[1] == [11]

    def test_batch_empty_list(self):
        """Test batch processing with empty list."""
        results = parse_diff_changed_lines_batch([])
        assert results == []

    def test_batch_with_empty_diffs(self):
        """Test batch processing with some empty diffs."""
        diff1 = """@@ -1,2 +1,3 @@
 line 1
+added
 line 2"""

        results = parse_diff_changed_lines_batch([diff1, "", diff1])

        assert len(results) == 3
        assert results[0] == [2]
        assert results[1] == []
        assert results[2] == [2]


@pytest.mark.skipif(not RUST_AVAILABLE, reason="Rust module not available")
class TestDiffParsingEdgeCases:
    """Edge case tests for diff parsing."""

    def test_unicode_content(self):
        """Test diff with unicode content."""
        diff = """@@ -1,2 +1,3 @@
 def hello():
+    return "こんにちは"
     pass"""

        result = parse_diff_changed_lines(diff)
        assert result == [2]

    def test_binary_marker_in_diff(self):
        """Test handling of binary file markers."""
        diff = """Binary files a/image.png and b/image.png differ"""

        result = parse_diff_changed_lines(diff)
        assert result == []

    def test_context_lines_only(self):
        """Test diff with only context lines (no changes)."""
        diff = """@@ -1,3 +1,3 @@
 line 1
 line 2
 line 3"""

        result = parse_diff_changed_lines(diff)
        assert result == []

    def test_real_world_diff(self):
        """Test a realistic diff from a Python file."""
        diff = """--- a/src/utils.py
+++ b/src/utils.py
@@ -15,6 +15,8 @@ import os

 def process_data(data):
     \"\"\"Process the input data.\"\"\"
+    if data is None:
+        return []
     result = []
     for item in data:
         result.append(transform(item))"""

        result = parse_diff_changed_lines(diff)
        # Added at lines 18 and 19
        assert 18 in result
        assert 19 in result
