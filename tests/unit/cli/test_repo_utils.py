"""Tests for CLI repository detection utilities (REPO-397)."""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from repotoire.cli.repo_utils import (
    detect_repo_info,
    derive_repo_id,
    extract_repo_slug,
    get_git_remote_url,
    normalize_remote_url,
)


class TestNormalizeRemoteUrl:
    """Tests for normalize_remote_url function."""

    def test_ssh_url(self):
        """SSH URLs are normalized correctly."""
        url = "git@github.com:owner/repo.git"
        assert normalize_remote_url(url) == "github.com/owner/repo"

    def test_https_url(self):
        """HTTPS URLs are normalized correctly."""
        url = "https://github.com/owner/repo.git"
        assert normalize_remote_url(url) == "github.com/owner/repo"

    def test_https_without_git_suffix(self):
        """HTTPS URLs without .git suffix are handled."""
        url = "https://github.com/owner/repo"
        assert normalize_remote_url(url) == "github.com/owner/repo"

    def test_gitlab_url(self):
        """GitLab URLs are normalized correctly."""
        url = "git@gitlab.com:owner/repo.git"
        assert normalize_remote_url(url) == "gitlab.com/owner/repo"

    def test_http_url(self):
        """HTTP URLs are normalized correctly."""
        url = "http://github.com/owner/repo.git"
        assert normalize_remote_url(url) == "github.com/owner/repo"


class TestExtractRepoSlug:
    """Tests for extract_repo_slug function."""

    def test_github_ssh_url(self):
        """Extracts slug from GitHub SSH URL."""
        url = "git@github.com:myorg/myrepo.git"
        assert extract_repo_slug(url) == "myorg/myrepo"

    def test_github_https_url(self):
        """Extracts slug from GitHub HTTPS URL."""
        url = "https://github.com/myorg/myrepo.git"
        assert extract_repo_slug(url) == "myorg/myrepo"

    def test_nested_path(self):
        """Handles nested GitLab paths."""
        url = "https://gitlab.com/group/subgroup/repo.git"
        assert extract_repo_slug(url) == "subgroup/repo"

    def test_normalized_url(self):
        """Works with already-normalized URL."""
        url = "github.com/owner/repo"
        assert extract_repo_slug(url) == "owner/repo"


class TestDeriveRepoId:
    """Tests for derive_repo_id function."""

    def test_deterministic(self):
        """Same input always produces same output."""
        id1 = derive_repo_id("github.com/owner/repo")
        id2 = derive_repo_id("github.com/owner/repo")
        assert id1 == id2

    def test_different_inputs_different_outputs(self):
        """Different inputs produce different outputs."""
        id1 = derive_repo_id("github.com/owner/repo1")
        id2 = derive_repo_id("github.com/owner/repo2")
        assert id1 != id2

    def test_uuid_format(self):
        """Output is in UUID format."""
        repo_id = derive_repo_id("test")
        parts = repo_id.split("-")
        assert len(parts) == 5
        assert len(parts[0]) == 8
        assert len(parts[1]) == 4
        assert len(parts[2]) == 4
        assert len(parts[3]) == 4
        assert len(parts[4]) == 12


class TestGetGitRemoteUrl:
    """Tests for get_git_remote_url function."""

    def test_returns_url_from_git_repo(self, tmp_path):
        """Returns URL for a git repository with remote."""
        # Mock subprocess.run to simulate git command
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="https://github.com/owner/repo.git\n",
            )

            url = get_git_remote_url(tmp_path)
            assert url == "https://github.com/owner/repo.git"

    def test_returns_none_for_non_git_dir(self, tmp_path):
        """Returns None for non-git directory."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=128)

            url = get_git_remote_url(tmp_path)
            assert url is None

    def test_handles_timeout(self, tmp_path):
        """Handles subprocess timeout gracefully."""
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired("git", 5)

            url = get_git_remote_url(tmp_path)
            assert url is None


class TestDetectRepoInfo:
    """Tests for detect_repo_info function."""

    def test_detects_git_repo(self, tmp_path):
        """Detects info from git repository with remote."""
        with patch("repotoire.cli.repo_utils.get_git_remote_url") as mock_remote:
            with patch("repotoire.cli.repo_utils.get_git_default_branch") as mock_branch:
                mock_remote.return_value = "https://github.com/myorg/myrepo.git"
                mock_branch.return_value = "main"

                info = detect_repo_info(tmp_path)

                assert info.source == "git"
                assert info.repo_slug == "myorg/myrepo"
                assert info.remote_url == "https://github.com/myorg/myrepo.git"
                assert info.default_branch == "main"
                assert info.repo_id is not None

    def test_detects_local_repo(self, tmp_path):
        """Falls back to local path for non-git directory."""
        with patch("repotoire.cli.repo_utils.get_git_remote_url") as mock_remote:
            mock_remote.return_value = None

            info = detect_repo_info(tmp_path)

            assert info.source == "local"
            assert info.repo_slug == tmp_path.name
            assert info.remote_url is None
            assert info.repo_id is not None

    def test_repo_id_is_deterministic_for_same_remote(self, tmp_path):
        """Same remote URL produces same repo ID."""
        with patch("repotoire.cli.repo_utils.get_git_remote_url") as mock_remote:
            with patch("repotoire.cli.repo_utils.get_git_default_branch") as mock_branch:
                mock_remote.return_value = "https://github.com/owner/repo.git"
                mock_branch.return_value = "main"

                info1 = detect_repo_info(tmp_path)
                info2 = detect_repo_info(tmp_path)

                assert info1.repo_id == info2.repo_id

    def test_different_remotes_different_ids(self, tmp_path):
        """Different remote URLs produce different repo IDs."""
        with patch("repotoire.cli.repo_utils.get_git_remote_url") as mock_remote:
            with patch("repotoire.cli.repo_utils.get_git_default_branch") as mock_branch:
                mock_branch.return_value = "main"

                mock_remote.return_value = "https://github.com/owner/repo1.git"
                info1 = detect_repo_info(tmp_path)

                mock_remote.return_value = "https://github.com/owner/repo2.git"
                info2 = detect_repo_info(tmp_path)

                assert info1.repo_id != info2.repo_id
