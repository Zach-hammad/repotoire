"""Unit tests for AutoFixEngine."""

import pytest
from pathlib import Path
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from datetime import datetime, timezone

from repotoire.autofix.engine import AutoFixEngine
from repotoire.autofix.models import (
    FixType,
    FixConfidence,
    FixContext,
    FixProposal,
    CodeChange,
)
from repotoire.models import Finding, Severity


@pytest.fixture
def mock_neo4j_client():
    """Create a mock Neo4j client."""
    client = Mock()
    client.close = Mock()
    return client


@pytest.fixture
def mock_rag_retriever():
    """Create a mock RAG retriever."""
    retriever = AsyncMock()
    retriever.search = AsyncMock(return_value=Mock(entities=[]))
    return retriever


@pytest.fixture
def auto_fix_engine(mock_neo4j_client):
    """Create AutoFixEngine with mocked dependencies."""
    with patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test-key"}):
        with patch("repotoire.autofix.engine.CodeEmbedder"):
            with patch("repotoire.autofix.engine.GraphRAGRetriever"):
                with patch("repotoire.autofix.engine.OpenAI"):
                    engine = AutoFixEngine(mock_neo4j_client)
                    return engine


@pytest.fixture
def sample_finding():
    """Create a sample finding for testing."""
    return Finding(
        id="test-finding-1",
        title="Function too complex",
        description="calculate_score has cyclomatic complexity of 15",
        severity=Severity.MEDIUM,
        affected_files=["src/module.py"],
        affected_nodes=["src.module.calculate_score"],
        line_start=42,
        detector="radon",
    )


class TestAutoFixEngineInit:
    """Test AutoFixEngine initialization."""

    def test_init_with_env_api_key(self, mock_neo4j_client):
        """Test initialization with API key from environment."""
        with patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test-key"}):
            with patch("repotoire.autofix.engine.CodeEmbedder"):
                with patch("repotoire.autofix.engine.GraphRAGRetriever"):
                    with patch("repotoire.autofix.engine.OpenAI"):
                        engine = AutoFixEngine(mock_neo4j_client)
                        assert engine.neo4j_client == mock_neo4j_client
                        assert engine.model == "gpt-4o"

    def test_init_with_explicit_api_key(self, mock_neo4j_client):
        """Test initialization with explicit API key."""
        with patch("repotoire.autofix.engine.CodeEmbedder"):
            with patch("repotoire.autofix.engine.GraphRAGRetriever"):
                with patch("repotoire.autofix.engine.OpenAI"):
                    engine = AutoFixEngine(
                        mock_neo4j_client, openai_api_key="sk-explicit-key"
                    )
                    assert engine.neo4j_client == mock_neo4j_client

    def test_init_without_api_key_raises_error(self, mock_neo4j_client):
        """Test that missing API key raises ValueError."""
        with patch.dict("os.environ", {}, clear=True):
            with pytest.raises(ValueError) as exc_info:
                AutoFixEngine(mock_neo4j_client)
            assert "OPENAI_API_KEY" in str(exc_info.value)

    def test_init_with_custom_model(self, mock_neo4j_client):
        """Test initialization with custom model."""
        with patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test-key"}):
            with patch("repotoire.autofix.engine.CodeEmbedder"):
                with patch("repotoire.autofix.engine.GraphRAGRetriever"):
                    with patch("repotoire.autofix.engine.OpenAI"):
                        engine = AutoFixEngine(mock_neo4j_client, model="gpt-4-turbo")
                        assert engine.model == "gpt-4-turbo"


class TestDetermineFixType:
    """Test fix type determination."""

    def test_determine_security_fix(self, auto_fix_engine):
        """Test identification of security fixes."""
        finding = Finding(
            id="test-sec-1",
            title="SQL injection vulnerability",
            description="Unsafe SQL query construction",
            severity=Severity.CRITICAL,
            affected_files=["db.py"],
            affected_nodes=["db.execute_query"],
            line_start=10,
            detector="bandit",
        )
        fix_type = auto_fix_engine._determine_fix_type(finding)
        assert fix_type == FixType.SECURITY

    def test_determine_complexity_fix(self, auto_fix_engine):
        """Test identification of complexity fixes."""
        finding = Finding(
            id="test-complex-1",
            title="Function too complex",
            description="Cyclomatic complexity is 20",
            severity=Severity.MEDIUM,
            affected_files=["module.py"],
            affected_nodes=["module.process"],
            line_start=42,
            detector="radon",
        )
        fix_type = auto_fix_engine._determine_fix_type(finding)
        assert fix_type == FixType.SIMPLIFY

    def test_determine_dead_code_fix(self, auto_fix_engine):
        """Test identification of dead code removal."""
        finding = Finding(
            id="test-dead-1",
            title="Unused function detected",
            description="Function is never called",
            severity=Severity.LOW,
            affected_files=["utils.py"],
            affected_nodes=["utils.unused_helper"],
            line_start=100,
            detector="vulture",
        )
        fix_type = auto_fix_engine._determine_fix_type(finding)
        assert fix_type == FixType.REMOVE

    def test_determine_documentation_fix(self, auto_fix_engine):
        """Test identification of documentation fixes."""
        finding = Finding(
            id="test-doc-1",
            title="Missing docstring",
            description="Function lacks documentation",
            severity=Severity.LOW,
            affected_files=["api.py"],
            affected_nodes=["api.get_data"],
            line_start=25,
            detector="pylint",
        )
        fix_type = auto_fix_engine._determine_fix_type(finding)
        assert fix_type == FixType.DOCUMENTATION

    def test_determine_type_hint_fix(self, auto_fix_engine):
        """Test identification of type hint fixes."""
        finding = Finding(
            id="test-type-1",
            title="Missing type hints",
            description="Function parameters lack type hint annotations",
            severity=Severity.LOW,
            affected_files=["models.py"],
            affected_nodes=["models.UserModel.validate"],
            line_start=15,
            detector="mypy",
        )
        fix_type = auto_fix_engine._determine_fix_type(finding)
        assert fix_type == FixType.TYPE_HINT

    def test_determine_extract_method_fix(self, auto_fix_engine):
        """Test identification of method extraction needs."""
        finding = Finding(
            id="test-extract-1",
            title="Long function detected",
            description="Function has too many lines",
            severity=Severity.MEDIUM,
            affected_files=["service.py"],
            affected_nodes=["service.DataService.process_all"],
            line_start=50,
            detector="pylint",
        )
        fix_type = auto_fix_engine._determine_fix_type(finding)
        assert fix_type == FixType.EXTRACT

    def test_determine_default_refactor(self, auto_fix_engine):
        """Test default to refactor for unknown issues."""
        finding = Finding(
            id="test-refactor-1",
            title="Code smell detected",
            description="Generic code quality issue",
            severity=Severity.MEDIUM,
            affected_files=["app.py"],
            affected_nodes=["app.main"],
            line_start=30,
            detector="custom",
        )
        fix_type = auto_fix_engine._determine_fix_type(finding)
        assert fix_type == FixType.REFACTOR


class TestExtractImports:
    """Test import extraction from Python code.

    Note: Import extraction is now handled by language handlers.
    These tests verify the PythonHandler is correctly integrated.
    """

    def test_extract_simple_imports(self):
        """Test extraction of simple import statements."""
        from repotoire.autofix.languages import PythonHandler

        handler = PythonHandler()
        code = """
import os
import sys
from pathlib import Path
"""
        imports = handler.extract_imports(code)
        assert "import os" in imports
        assert "import sys" in imports
        assert "from pathlib import Path" in imports

    def test_extract_multiple_from_imports(self):
        """Test extraction of from imports with multiple names."""
        from repotoire.autofix.languages import PythonHandler

        handler = PythonHandler()
        code = """
from typing import Optional, List, Dict
"""
        imports = handler.extract_imports(code)
        assert "from typing import Optional" in imports
        assert "from typing import List" in imports
        assert "from typing import Dict" in imports

    def test_extract_imports_with_aliases(self):
        """Test extraction of imports with aliases."""
        from repotoire.autofix.languages import PythonHandler

        handler = PythonHandler()
        code = """
import numpy as np
from datetime import datetime as dt
"""
        imports = handler.extract_imports(code)
        assert "import numpy" in imports
        assert "from datetime import datetime" in imports

    def test_extract_imports_handles_syntax_errors(self):
        """Test that syntax errors don't crash import extraction."""
        from repotoire.autofix.languages import PythonHandler

        handler = PythonHandler()
        code = "this is not valid python code {"
        imports = handler.extract_imports(code)
        assert imports == []


class TestValidateFix:
    """Test fix validation."""

    def test_validate_syntactically_correct_fix(self, auto_fix_engine, sample_finding, tmp_path):
        """Test validation of syntactically correct fix."""
        # Create test file
        test_file = tmp_path / "src" / "module.py"
        test_file.parent.mkdir(parents=True)
        test_file.write_text("def old_function():\n    pass\n")

        # Create fix proposal
        fix_proposal = FixProposal(
            id="test-123",
            finding=sample_finding,
            fix_type=FixType.REFACTOR,
            confidence=FixConfidence.HIGH,
            changes=[
                CodeChange(
                    file_path=Path("src/module.py"),
                    original_code="def old_function():\n    pass",
                    fixed_code="def new_function():\n    return None",
                    start_line=1,
                    end_line=2,
                    description="Rename and improve function",
                )
            ],
            title="Test fix",
            description="Test description",
            rationale="Test rationale",
        )

        is_valid = auto_fix_engine._validate_fix(fix_proposal, tmp_path)
        assert is_valid is True

    def test_validate_syntax_error_in_fix(self, auto_fix_engine, sample_finding, tmp_path):
        """Test validation catches syntax errors."""
        # Create test file
        test_file = tmp_path / "src" / "module.py"
        test_file.parent.mkdir(parents=True)
        test_file.write_text("def old_function():\n    pass\n")

        # Create fix with syntax error
        fix_proposal = FixProposal(
            id="test-123",
            finding=sample_finding,
            fix_type=FixType.REFACTOR,
            confidence=FixConfidence.HIGH,
            changes=[
                CodeChange(
                    file_path=Path("src/module.py"),
                    original_code="def old_function():\n    pass",
                    fixed_code="def new_function(\n    # Missing closing paren",
                    start_line=1,
                    end_line=2,
                    description="Broken fix",
                )
            ],
            title="Test fix",
            description="Test description",
            rationale="Test rationale",
        )

        is_valid = auto_fix_engine._validate_fix(fix_proposal, tmp_path)
        assert is_valid is False

    def test_validate_original_code_not_found(self, auto_fix_engine, sample_finding, tmp_path):
        """Test validation fails if original code doesn't exist."""
        # Create test file with different content
        test_file = tmp_path / "src" / "module.py"
        test_file.parent.mkdir(parents=True)
        test_file.write_text("def different_function():\n    pass\n")

        # Create fix for non-existent code
        fix_proposal = FixProposal(
            id="test-123",
            finding=sample_finding,
            fix_type=FixType.REFACTOR,
            confidence=FixConfidence.HIGH,
            changes=[
                CodeChange(
                    file_path=Path("src/module.py"),
                    original_code="def nonexistent_function():\n    pass",
                    fixed_code="def new_function():\n    return None",
                    start_line=1,
                    end_line=2,
                    description="Fix for non-existent code",
                )
            ],
            title="Test fix",
            description="Test description",
            rationale="Test rationale",
        )

        is_valid = auto_fix_engine._validate_fix(fix_proposal, tmp_path)
        assert is_valid is False


class TestCalculateConfidence:
    """Test confidence calculation."""

    def test_high_confidence_with_good_context(self, auto_fix_engine, sample_finding):
        """Test high confidence with comprehensive context."""
        context = FixContext(
            finding=sample_finding,
            related_code=["code1", "code2", "code3"],
            file_content="full file content here",
        )
        fix_data = {
            "changes": [Mock()],  # Single change
            "rationale": "Very detailed rationale explaining the fix and why it works" * 3,
        }

        confidence = auto_fix_engine._calculate_confidence(fix_data, context)
        assert confidence == FixConfidence.HIGH

    def test_medium_confidence_with_moderate_context(self, auto_fix_engine, sample_finding):
        """Test medium confidence with moderate context."""
        context = FixContext(
            finding=sample_finding,
            related_code=["code1", "code2"],
            file_content="full file content",
        )
        fix_data = {
            "changes": [Mock()],
            "rationale": "Brief rationale",
        }

        confidence = auto_fix_engine._calculate_confidence(fix_data, context)
        assert confidence in [FixConfidence.MEDIUM, FixConfidence.HIGH]

    def test_low_confidence_for_critical_findings(self, auto_fix_engine):
        """Test that critical findings reduce confidence."""
        critical_finding = Finding(
            id="test-critical-1",
            title="Critical security issue",
            description="Hardcoded credentials detected",
            severity=Severity.CRITICAL,
            affected_files=["secure.py"],
            affected_nodes=["secure.authenticate"],
            line_start=10,
            detector="bandit",
        )
        context = FixContext(
            finding=critical_finding,
            related_code=["code1", "code2", "code3"],
            file_content="content",
        )
        fix_data = {
            "changes": [Mock()],
            "rationale": "Short",
        }

        confidence = auto_fix_engine._calculate_confidence(fix_data, context)
        # Critical severity reduces score by 0.2
        assert confidence in [FixConfidence.LOW, FixConfidence.MEDIUM]

    def test_low_confidence_with_minimal_context(self, auto_fix_engine, sample_finding):
        """Test low confidence with minimal context."""
        context = FixContext(
            finding=sample_finding,
            related_code=[],
            file_content=None,
        )
        fix_data = {
            "changes": [Mock(), Mock()],  # Multiple changes
            "rationale": "",
        }

        confidence = auto_fix_engine._calculate_confidence(fix_data, context)
        assert confidence == FixConfidence.LOW
