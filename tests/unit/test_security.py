"""Security tests for path traversal protection."""

import pytest
import tempfile
import os
from pathlib import Path
from unittest.mock import Mock

from repotoire.pipeline import IngestionPipeline, SecurityError
from repotoire.graph import Neo4jClient


class TestPathTraversalProtection:
    """Test path traversal attack protection."""

    @pytest.fixture
    def mock_db(self):
        """Create a mock Neo4j client."""
        return Mock(spec=Neo4jClient)

    @pytest.fixture
    def temp_repo(self):
        """Create a temporary repository."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_path = Path(tmpdir) / "test_repo"
            repo_path.mkdir()

            # Create some test files
            (repo_path / "file1.py").write_text("# test file 1")
            (repo_path / "file2.py").write_text("# test file 2")

            yield repo_path

    def test_repository_path_validation_nonexistent(self, mock_db):
        """Test that nonexistent repository path raises ValueError."""
        with pytest.raises(ValueError, match="Repository does not exist"):
            IngestionPipeline("/nonexistent/path", mock_db)

    def test_repository_path_validation_not_directory(self, mock_db):
        """Test that file path raises ValueError."""
        with tempfile.NamedTemporaryFile() as tmp:
            with pytest.raises(ValueError, match="Repository must be a directory"):
                IngestionPipeline(tmp.name, mock_db)

    def test_repository_path_symlink_rejected(self, mock_db, temp_repo):
        """Test that symlinked repository root is rejected."""
        with tempfile.TemporaryDirectory() as tmpdir:
            symlink_path = Path(tmpdir) / "symlink_repo"
            symlink_path.symlink_to(temp_repo)

            with pytest.raises(SecurityError, match="cannot be a symbolic link"):
                IngestionPipeline(str(symlink_path), mock_db)

    def test_file_outside_repository_rejected(self, mock_db, temp_repo):
        """Test that files outside repository boundary are rejected."""
        pipeline = IngestionPipeline(str(temp_repo), mock_db)

        # Try to access file outside repo
        outside_file = temp_repo.parent / "outside.py"
        outside_file.write_text("# outside file")

        with pytest.raises(SecurityError, match="outside repository boundary"):
            pipeline._validate_file_path(outside_file)

    def test_path_traversal_attack_blocked(self, mock_db, temp_repo):
        """Test that path traversal attempts are blocked."""
        pipeline = IngestionPipeline(str(temp_repo), mock_db)

        # Create a file outside the repo
        outside_file = temp_repo.parent / "secret.py"
        outside_file.write_text("# secret file")

        # Try to access via path traversal
        traversal_path = temp_repo / ".." / "secret.py"

        with pytest.raises(SecurityError, match="outside repository boundary"):
            pipeline._validate_file_path(traversal_path)

    def test_symlink_file_skipped_by_default(self, mock_db, temp_repo):
        """Test that symlinked files are skipped by default."""
        pipeline = IngestionPipeline(str(temp_repo), mock_db)

        # Create a symlink to a file
        real_file = temp_repo / "real_file.py"
        real_file.write_text("# real file")

        symlink_file = temp_repo / "symlink_file.py"
        symlink_file.symlink_to(real_file)

        # Symlink should be skipped
        assert pipeline._should_skip_file(symlink_file) is True
        assert len(pipeline.skipped_files) == 1
        assert pipeline.skipped_files[0]["reason"] == "Symbolic link (security)"

    def test_symlink_file_included_with_flag(self, mock_db, temp_repo):
        """Test that symlinks are included when follow_symlinks=True."""
        pipeline = IngestionPipeline(str(temp_repo), mock_db, follow_symlinks=True)

        # Create a symlink to a file
        real_file = temp_repo / "real_file.py"
        real_file.write_text("# real file")

        symlink_file = temp_repo / "symlink_file.py"
        symlink_file.symlink_to(real_file)

        # Symlink should NOT be skipped when follow_symlinks=True
        # But we need to check the file is valid first
        assert symlink_file.is_symlink()
        # The method should not skip it
        skipped = pipeline._should_skip_file(symlink_file)
        # It should not be skipped for being a symlink
        # (it might still be skipped for size, but not for being a symlink)
        assert not any(
            s["reason"] == "Symbolic link (security)"
            for s in pipeline.skipped_files
        )


class TestFileSizeLimits:
    """Test file size limit protection."""

    @pytest.fixture
    def mock_db(self):
        """Create a mock Neo4j client."""
        return Mock(spec=Neo4jClient)

    @pytest.fixture
    def temp_repo(self):
        """Create a temporary repository."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_path = Path(tmpdir) / "test_repo"
            repo_path.mkdir()
            yield repo_path

    def test_large_file_skipped(self, mock_db, temp_repo):
        """Test that files exceeding size limit are skipped."""
        # Set max size to 0.001 MB (1KB)
        pipeline = IngestionPipeline(str(temp_repo), mock_db, max_file_size_mb=0.001)

        # Create a file larger than 1KB
        large_file = temp_repo / "large_file.py"
        large_file.write_text("# " + "x" * 2000)  # 2KB file

        # File should be skipped
        assert pipeline._should_skip_file(large_file) is True
        assert len(pipeline.skipped_files) == 1
        assert "too large" in pipeline.skipped_files[0]["reason"].lower()

    def test_small_file_processed(self, mock_db, temp_repo):
        """Test that files within size limit are processed."""
        pipeline = IngestionPipeline(str(temp_repo), mock_db, max_file_size_mb=10.0)

        # Create a small file
        small_file = temp_repo / "small_file.py"
        small_file.write_text("# small file")

        # File should not be skipped for size
        assert pipeline._validate_file_size(small_file) is True
        assert len(pipeline.skipped_files) == 0

    def test_custom_size_limit(self, mock_db, temp_repo):
        """Test custom file size limits."""
        # Set custom limit of 5MB
        pipeline = IngestionPipeline(str(temp_repo), mock_db, max_file_size_mb=5.0)

        assert pipeline.max_file_size_mb == 5.0


class TestRelativePathStorage:
    """Test that relative paths are stored instead of absolute paths."""

    @pytest.fixture
    def mock_db(self):
        """Create a mock Neo4j client."""
        return Mock(spec=Neo4jClient)

    @pytest.fixture
    def temp_repo(self):
        """Create a temporary repository."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_path = Path(tmpdir) / "test_repo"
            repo_path.mkdir()
            yield repo_path

    def test_relative_path_conversion(self, mock_db, temp_repo):
        """Test that absolute paths are converted to relative."""
        pipeline = IngestionPipeline(str(temp_repo), mock_db)

        # Test with file in root
        file1 = temp_repo / "file1.py"
        assert pipeline._get_relative_path(file1) == "file1.py"

        # Test with file in subdirectory
        subdir = temp_repo / "subdir"
        subdir.mkdir()
        file2 = subdir / "file2.py"
        assert pipeline._get_relative_path(file2) == "subdir/file2.py"

        # Test with nested subdirectories
        nested = temp_repo / "a" / "b" / "c"
        nested.mkdir(parents=True)
        file3 = nested / "file3.py"
        assert pipeline._get_relative_path(file3) == "a/b/c/file3.py"

    def test_absolute_path_not_stored(self, mock_db, temp_repo):
        """Test that absolute system paths are not stored."""
        pipeline = IngestionPipeline(str(temp_repo), mock_db)

        file_path = temp_repo / "test.py"
        relative = pipeline._get_relative_path(file_path)

        # Relative path should not contain system-specific paths
        assert not relative.startswith("/")
        assert not relative.startswith("C:")
        assert str(temp_repo) not in relative


class TestSecurityIntegration:
    """Integration tests for security features."""

    @pytest.fixture
    def mock_db(self):
        """Create a mock Neo4j client."""
        db = Mock(spec=Neo4jClient)
        db.batch_create_nodes = Mock(return_value={})
        db.batch_create_relationships = Mock()
        return db

    @pytest.fixture
    def temp_repo(self):
        """Create a temporary repository with various test cases."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_path = Path(tmpdir) / "test_repo"
            repo_path.mkdir()

            # Create normal files
            (repo_path / "normal1.py").write_text("# normal file 1")
            (repo_path / "normal2.py").write_text("# normal file 2")

            # Create a large file (if testing with low limit)
            # (repo_path / "large.py").write_text("# " + "x" * 20000)

            yield repo_path

    def test_scan_respects_security_checks(self, mock_db, temp_repo):
        """Test that scan() applies all security checks."""
        pipeline = IngestionPipeline(str(temp_repo), mock_db)

        files = pipeline.scan()

        # All returned files should be within repo
        for file in files:
            # Should not raise
            pipeline._validate_file_path(file)

        # All returned files should be regular files (not symlinks by default)
        for file in files:
            assert file.is_file()

    def test_skipped_files_reporting(self, mock_db, temp_repo):
        """Test that skipped files are tracked and reported."""
        pipeline = IngestionPipeline(str(temp_repo), mock_db, max_file_size_mb=0.001)

        # Create a large file that will be skipped
        large_file = temp_repo / "large.py"
        large_file.write_text("# " + "x" * 2000)

        # Scan should skip the large file
        files = pipeline.scan()

        # Large file should be in skipped list
        assert len(pipeline.skipped_files) >= 1
        assert any("large.py" in s["file"] for s in pipeline.skipped_files)

    def test_defense_in_depth(self, mock_db, temp_repo):
        """Test that multiple security layers work together."""
        # Create symlink outside repo pointing to sensitive file
        sensitive_file = temp_repo.parent / "sensitive.py"
        sensitive_file.write_text("# sensitive data")

        symlink = temp_repo / "sneaky_link.py"
        symlink.symlink_to(sensitive_file)

        # Even with follow_symlinks=True, file outside boundary should fail
        pipeline = IngestionPipeline(str(temp_repo), mock_db, follow_symlinks=True)

        # The symlink resolution should reveal it's outside the repo
        resolved = symlink.resolve()
        with pytest.raises(SecurityError):
            pipeline._validate_file_path(resolved)


class TestConfigurationOptions:
    """Test security configuration options."""

    @pytest.fixture
    def mock_db(self):
        """Create a mock Neo4j client."""
        return Mock(spec=Neo4jClient)

    @pytest.fixture
    def temp_repo(self):
        """Create a temporary repository."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_path = Path(tmpdir) / "test_repo"
            repo_path.mkdir()
            (repo_path / "test.py").write_text("# test")
            yield repo_path

    def test_default_security_settings(self, mock_db, temp_repo):
        """Test default security settings are secure."""
        pipeline = IngestionPipeline(str(temp_repo), mock_db)

        # Symlinks should be disabled by default
        assert pipeline.follow_symlinks is False

        # Default file size limit should be set
        assert pipeline.max_file_size_mb == IngestionPipeline.MAX_FILE_SIZE_MB

    def test_custom_security_settings(self, mock_db, temp_repo):
        """Test custom security settings."""
        pipeline = IngestionPipeline(
            str(temp_repo),
            mock_db,
            follow_symlinks=True,
            max_file_size_mb=20.0
        )

        assert pipeline.follow_symlinks is True
        assert pipeline.max_file_size_mb == 20.0

    def test_security_disabled_requires_explicit_flag(self, mock_db, temp_repo):
        """Test that disabling security requires explicit flags."""
        # Default pipeline should have security enabled
        default_pipeline = IngestionPipeline(str(temp_repo), mock_db)

        # Symlink protection should be enabled by default
        symlink_file = temp_repo / "link.py"
        real_file = temp_repo / "real.py"
        real_file.write_text("# real")
        symlink_file.symlink_to(real_file)

        assert default_pipeline._should_skip_file(symlink_file) is True

        # Enabling symlinks requires explicit flag
        permissive_pipeline = IngestionPipeline(
            str(temp_repo),
            mock_db,
            follow_symlinks=True
        )

        # Reset skipped files before checking
        permissive_pipeline.skipped_files = []
        permissive_pipeline._should_skip_file(symlink_file)

        # Should not skip for being a symlink
        symlink_skips = [
            s for s in permissive_pipeline.skipped_files
            if "Symbolic link" in s["reason"]
        ]
        assert len(symlink_skips) == 0
