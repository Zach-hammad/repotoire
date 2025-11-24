"""Fix application with git integration."""

import os
import subprocess
from pathlib import Path
from typing import List, Optional, Tuple
from datetime import datetime

import git

from repotoire.autofix.models import FixProposal, FixStatus, CodeChange
from repotoire.logging_config import get_logger

logger = get_logger(__name__)


class FixApplicator:
    """Apply approved fixes to codebase with git integration."""

    def __init__(self, repository_path: Path, create_branch: bool = True):
        """Initialize fix applicator.

        Args:
            repository_path: Path to git repository
            create_branch: Whether to create git branch for fixes
        """
        self.repository_path = Path(repository_path)
        self.create_branch = create_branch

        # Initialize git repo
        try:
            self.repo = git.Repo(repository_path)
        except git.exc.InvalidGitRepositoryError:
            logger.warning(f"{repository_path} is not a git repository")
            self.repo = None

    def apply_fix(
        self, fix: FixProposal, commit: bool = True
    ) -> Tuple[bool, Optional[str]]:
        """Apply a single fix to the codebase.

        Args:
            fix: Fix proposal to apply
            commit: Whether to create git commit

        Returns:
            Tuple of (success, error_message)
        """
        try:
            # Create branch if requested
            if self.create_branch and self.repo:
                self._create_branch(fix)

            # Apply each code change
            for change in fix.changes:
                success, error = self._apply_change(change)
                if not success:
                    fix.status = FixStatus.FAILED
                    return False, error

            # Create commit if requested
            if commit and self.repo:
                self._create_commit(fix)

            # Mark as applied
            fix.status = FixStatus.APPLIED
            fix.applied_at = datetime.utcnow()

            logger.info(f"Successfully applied fix {fix.id}")
            return True, None

        except Exception as e:
            error_msg = f"Failed to apply fix: {str(e)}"
            logger.error(error_msg, exc_info=True)
            fix.status = FixStatus.FAILED
            return False, error_msg

    def apply_batch(
        self, fixes: List[FixProposal], commit_each: bool = False
    ) -> Tuple[List[FixProposal], List[Tuple[FixProposal, str]]]:
        """Apply multiple fixes.

        Args:
            fixes: List of approved fixes
            commit_each: Create commit for each fix (vs one commit for all)

        Returns:
            Tuple of (successful_fixes, failed_fixes_with_errors)
        """
        successful = []
        failed = []

        for fix in fixes:
            if fix.status != FixStatus.APPROVED:
                continue

            success, error = self.apply_fix(fix, commit=commit_each)

            if success:
                successful.append(fix)
            else:
                failed.append((fix, error))

        # Create single commit for all if not committing individually
        if successful and not commit_each and self.repo:
            self._create_batch_commit(successful)

        return successful, failed

    def _apply_change(self, change: CodeChange) -> Tuple[bool, Optional[str]]:
        """Apply a single code change to a file.

        Args:
            change: Code change to apply

        Returns:
            Tuple of (success, error_message)
        """
        file_path = self.repository_path / change.file_path

        try:
            # Read current file content
            if not file_path.exists():
                return False, f"File not found: {file_path}"

            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()

            # Verify original code exists
            original = change.original_code.strip()
            if original not in content:
                return (
                    False,
                    f"Original code not found in {change.file_path}. File may have changed.",
                )

            # Apply the change
            new_content = content.replace(original, change.fixed_code.strip(), 1)

            # Write back to file
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(new_content)

            logger.debug(f"Applied change to {change.file_path}")
            return True, None

        except Exception as e:
            error_msg = f"Error applying change to {change.file_path}: {str(e)}"
            logger.error(error_msg, exc_info=True)
            return False, error_msg

    def _create_branch(self, fix: FixProposal) -> None:
        """Create git branch for fix.

        Args:
            fix: Fix proposal
        """
        if not self.repo:
            return

        branch_name = fix.branch_name or f"autofix/{fix.fix_type.value}/{fix.id}"

        try:
            # Check if branch exists
            if branch_name in self.repo.heads:
                logger.warning(f"Branch {branch_name} already exists, checking out")
                self.repo.heads[branch_name].checkout()
            else:
                # Create new branch
                new_branch = self.repo.create_head(branch_name)
                new_branch.checkout()
                logger.info(f"Created and checked out branch: {branch_name}")

            fix.branch_name = branch_name

        except Exception as e:
            logger.error(f"Failed to create branch: {e}")
            # Continue without branch

    def _create_commit(self, fix: FixProposal) -> None:
        """Create git commit for fix.

        Args:
            fix: Fix proposal
        """
        if not self.repo:
            return

        try:
            # Stage all changed files
            for change in fix.changes:
                file_path = str(change.file_path)
                self.repo.index.add([file_path])

            # Create commit message
            commit_msg = fix.commit_message or self._generate_commit_message(fix)

            # Commit
            self.repo.index.commit(commit_msg)
            logger.info(f"Created commit for fix {fix.id}")

        except Exception as e:
            logger.error(f"Failed to create commit: {e}")

    def _create_batch_commit(self, fixes: List[FixProposal]) -> None:
        """Create single commit for multiple fixes.

        Args:
            fixes: List of applied fixes
        """
        if not self.repo or not fixes:
            return

        try:
            # Stage all changed files
            for fix in fixes:
                for change in fix.changes:
                    file_path = str(change.file_path)
                    self.repo.index.add([file_path])

            # Generate batch commit message
            commit_msg = self._generate_batch_commit_message(fixes)

            # Commit
            self.repo.index.commit(commit_msg)
            logger.info(f"Created batch commit for {len(fixes)} fixes")

        except Exception as e:
            logger.error(f"Failed to create batch commit: {e}")

    def _generate_commit_message(self, fix: FixProposal) -> str:
        """Generate commit message for fix.

        Args:
            fix: Fix proposal

        Returns:
            Commit message string
        """
        # Use fix's commit message if available
        if fix.commit_message:
            return fix.commit_message

        # Generate from fix details
        msg = f"fix: {fix.title}\n\n"
        msg += f"{fix.description}\n\n"
        msg += f"Fix Type: {fix.fix_type.value}\n"
        msg += f"Confidence: {fix.confidence.value}\n"
        msg += f"Files: {', '.join(f.file_path.name for f in fix.changes)}\n\n"
        msg += "🤖 Generated with Repotoire Auto-Fix\n"

        return msg

    def _generate_batch_commit_message(self, fixes: List[FixProposal]) -> str:
        """Generate commit message for batch of fixes.

        Args:
            fixes: List of fixes

        Returns:
            Batch commit message
        """
        msg = f"fix: apply {len(fixes)} auto-fixes\n\n"

        for fix in fixes:
            msg += f"- {fix.title}\n"

        msg += f"\n🤖 Generated with Repotoire Auto-Fix ({len(fixes)} fixes)\n"

        return msg

    def run_tests(self, test_command: Optional[str] = None) -> Tuple[bool, str]:
        """Run tests after applying fixes.

        Args:
            test_command: Test command to run (default: pytest)

        Returns:
            Tuple of (success, output)
        """
        command = test_command or "pytest"

        try:
            result = subprocess.run(
                command.split(),
                cwd=self.repository_path,
                capture_output=True,
                text=True,
                timeout=300,  # 5 minute timeout
            )

            success = result.returncode == 0
            output = result.stdout + result.stderr

            if success:
                logger.info("Tests passed after applying fixes")
            else:
                logger.warning("Tests failed after applying fixes")

            return success, output

        except subprocess.TimeoutExpired:
            return False, "Test execution timed out after 5 minutes"
        except FileNotFoundError:
            return False, f"Test command not found: {command}"
        except Exception as e:
            return False, f"Error running tests: {str(e)}"

    def rollback(self) -> bool:
        """Rollback all changes (reset to HEAD).

        Returns:
            True if successful, False otherwise
        """
        if not self.repo:
            logger.warning("No git repository, cannot rollback")
            return False

        try:
            # Reset to HEAD
            self.repo.head.reset(index=True, working_tree=True)
            logger.info("Rolled back all changes")
            return True

        except Exception as e:
            logger.error(f"Failed to rollback: {e}")
            return False
