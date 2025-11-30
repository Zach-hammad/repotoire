"""Tests for fix templates functionality."""

import tempfile
from pathlib import Path

import pytest
import yaml

from repotoire.autofix.templates import (
    DEFAULT_TEMPLATE_DIRS,
    FixTemplate,
    PatternType,
    TemplateEvidence,
    TemplateFile,
    TemplateLoadError,
    TemplateMatch,
    TemplateRegistry,
    get_registry,
    reset_registry,
)


class TestPatternType:
    """Tests for PatternType enum."""

    def test_pattern_types_exist(self):
        """All expected pattern types exist."""
        assert PatternType.REGEX.value == "regex"
        assert PatternType.LITERAL.value == "literal"
        assert PatternType.AST.value == "ast"


class TestFixTemplate:
    """Tests for FixTemplate model."""

    def test_minimal_template(self):
        """Create template with minimal required fields."""
        template = FixTemplate(
            name="test-template",
            pattern="old_code",
            replacement="new_code",
        )

        assert template.name == "test-template"
        assert template.pattern == "old_code"
        assert template.replacement == "new_code"
        assert template.pattern_type == PatternType.REGEX  # default
        assert template.confidence == "HIGH"  # default
        assert template.priority == 50  # default
        assert template.languages == ["python"]  # default

    def test_full_template(self):
        """Create template with all fields."""
        template = FixTemplate(
            name="full-template",
            description="A complete template",
            pattern=r"print\((.+?)\)",
            pattern_type=PatternType.REGEX,
            replacement="logger.info($1)",
            confidence="MEDIUM",
            fix_type="refactor",
            languages=["python", "typescript"],
            evidence=TemplateEvidence(
                documentation_refs=["PEP 8"],
                best_practices=["Use logging instead of print"],
            ),
            file_pattern="**/src/*.py",
            priority=80,
        )

        assert template.name == "full-template"
        assert template.description == "A complete template"
        assert template.pattern_type == PatternType.REGEX
        assert template.confidence == "MEDIUM"
        assert template.fix_type == "refactor"
        assert "python" in template.languages
        assert "typescript" in template.languages
        assert template.file_pattern == "**/src/*.py"
        assert template.priority == 80

    def test_confidence_validation(self):
        """Confidence must be HIGH, MEDIUM, or LOW."""
        # Valid values
        for conf in ["HIGH", "MEDIUM", "LOW", "high", "medium", "low"]:
            template = FixTemplate(
                name="test", pattern="x", replacement="y", confidence=conf
            )
            assert template.confidence in {"HIGH", "MEDIUM", "LOW"}

        # Invalid value
        with pytest.raises(ValueError, match="confidence must be one of"):
            FixTemplate(name="test", pattern="x", replacement="y", confidence="INVALID")

    def test_empty_pattern_rejected(self):
        """Empty pattern is rejected."""
        with pytest.raises(ValueError, match="pattern cannot be empty"):
            FixTemplate(name="test", pattern="", replacement="y")

        with pytest.raises(ValueError, match="pattern cannot be empty"):
            FixTemplate(name="test", pattern="   ", replacement="y")

    def test_priority_bounds(self):
        """Priority must be between 0 and 100."""
        # Valid bounds
        FixTemplate(name="test", pattern="x", replacement="y", priority=0)
        FixTemplate(name="test", pattern="x", replacement="y", priority=100)

        # Invalid bounds
        with pytest.raises(ValueError):
            FixTemplate(name="test", pattern="x", replacement="y", priority=-1)

        with pytest.raises(ValueError):
            FixTemplate(name="test", pattern="x", replacement="y", priority=101)


class TestTemplateRegistry:
    """Tests for TemplateRegistry."""

    @pytest.fixture
    def registry(self):
        """Create fresh registry for each test."""
        return TemplateRegistry()

    @pytest.fixture
    def sample_yaml(self, tmp_path):
        """Create sample YAML template file."""
        yaml_content = {
            "templates": [
                {
                    "name": "test-template-1",
                    "pattern": "== None",
                    "replacement": "is None",
                    "confidence": "HIGH",
                    "priority": 60,
                },
                {
                    "name": "test-template-2",
                    "pattern": "print\\((.+?)\\)",
                    "pattern_type": "regex",
                    "replacement": "logger.info($1)",
                    "confidence": "MEDIUM",
                    "priority": 40,
                },
            ]
        }

        yaml_file = tmp_path / "test-templates.yaml"
        with open(yaml_file, "w") as f:
            yaml.dump(yaml_content, f)

        return yaml_file

    def test_load_from_file(self, registry, sample_yaml):
        """Load templates from YAML file."""
        count = registry.load_from_file(sample_yaml)

        assert count == 2
        assert len(registry.templates) == 2
        assert sample_yaml in registry.loaded_files

    def test_priority_sorting(self, registry, sample_yaml):
        """Templates are sorted by priority (descending)."""
        registry.load_from_file(sample_yaml)

        templates = registry.templates
        assert templates[0].name == "test-template-1"  # priority 60
        assert templates[1].name == "test-template-2"  # priority 40

    def test_load_from_directory(self, registry, tmp_path):
        """Load all YAML files from directory."""
        # Create multiple YAML files
        for i, ext in enumerate(["yaml", "yml"]):
            yaml_content = {
                "templates": [
                    {
                        "name": f"template-{i}",
                        "pattern": f"pattern{i}",
                        "replacement": f"replacement{i}",
                    }
                ]
            }
            yaml_file = tmp_path / f"templates{i}.{ext}"
            with open(yaml_file, "w") as f:
                yaml.dump(yaml_content, f)

        count = registry.load_from_directory(tmp_path)

        assert count == 2
        assert len(registry.templates) == 2

    def test_load_nonexistent_directory(self, registry, tmp_path):
        """Loading from nonexistent directory returns 0."""
        nonexistent = tmp_path / "does-not-exist"
        count = registry.load_from_directory(nonexistent)

        assert count == 0
        assert len(registry.templates) == 0

    def test_load_invalid_yaml(self, registry, tmp_path):
        """Invalid YAML raises TemplateLoadError."""
        invalid_file = tmp_path / "invalid.yaml"
        invalid_file.write_text("not: valid: yaml: {{")

        with pytest.raises(TemplateLoadError, match="YAML parse error"):
            registry.load_from_file(invalid_file)

    def test_load_invalid_regex(self, registry, tmp_path):
        """Invalid regex in template raises TemplateLoadError."""
        yaml_content = {
            "templates": [
                {
                    "name": "bad-regex",
                    "pattern": "[invalid(regex",  # Missing closing bracket
                    "pattern_type": "regex",
                    "replacement": "x",
                }
            ]
        }

        yaml_file = tmp_path / "bad-regex.yaml"
        with open(yaml_file, "w") as f:
            yaml.dump(yaml_content, f)

        with pytest.raises(TemplateLoadError, match="Invalid regex"):
            registry.load_from_file(yaml_file)

    def test_load_missing_required_field(self, registry, tmp_path):
        """Missing required field raises TemplateLoadError."""
        yaml_content = {
            "templates": [
                {
                    "name": "missing-pattern",
                    # Missing 'pattern' field
                    "replacement": "x",
                }
            ]
        }

        yaml_file = tmp_path / "missing-field.yaml"
        with open(yaml_file, "w") as f:
            yaml.dump(yaml_content, f)

        with pytest.raises(TemplateLoadError, match="Validation errors"):
            registry.load_from_file(yaml_file)

    def test_clear(self, registry, sample_yaml):
        """Clear removes all templates."""
        registry.load_from_file(sample_yaml)
        assert len(registry.templates) > 0

        registry.clear()

        assert len(registry.templates) == 0
        assert len(registry.loaded_files) == 0


class TestTemplateMatching:
    """Tests for template pattern matching."""

    @pytest.fixture
    def registry(self):
        """Create registry with test templates."""
        registry = TemplateRegistry()
        return registry

    def test_literal_match(self, registry):
        """Match using literal pattern."""
        template = FixTemplate(
            name="literal-test",
            pattern="from collections import Mapping",
            pattern_type=PatternType.LITERAL,
            replacement="from collections.abc import Mapping",
        )
        registry._templates.append(template)

        code = 'from collections import Mapping\n\nclass MyMapping(Mapping): pass'
        match = registry.match(code, "test.py", "python")

        assert match is not None
        assert match.template.name == "literal-test"
        assert match.original_code == "from collections import Mapping"
        assert match.fixed_code == "from collections.abc import Mapping"

    def test_regex_match_simple(self, registry):
        """Match using simple regex pattern."""
        template = FixTemplate(
            name="none-comparison",
            pattern=r"==\s*None",
            pattern_type=PatternType.REGEX,
            replacement="is None",
        )
        registry._templates.append(template)

        code = 'if x == None:\n    pass'
        match = registry.match(code, "test.py", "python")

        assert match is not None
        assert match.original_code == "== None"
        assert match.fixed_code == "is None"

    def test_regex_match_with_capture_groups(self, registry):
        """Match with capture group substitution."""
        template = FixTemplate(
            name="print-to-logger",
            pattern=r'print\((.+?)\)',
            pattern_type=PatternType.REGEX,
            replacement="logger.info($1)",
        )
        registry._templates.append(template)

        code = 'print("Hello world")'
        match = registry.match(code, "test.py", "python")

        assert match is not None
        assert match.original_code == 'print("Hello world")'
        assert match.fixed_code == 'logger.info("Hello world")'
        assert match.capture_groups == {"1": '"Hello world"'}

    def test_regex_multiple_capture_groups(self, registry):
        """Match with multiple capture groups."""
        template = FixTemplate(
            name="swap-args",
            pattern=r'swap\((\w+),\s*(\w+)\)',
            pattern_type=PatternType.REGEX,
            replacement="swap($2, $1)",
        )
        registry._templates.append(template)

        code = "result = swap(a, b)"
        match = registry.match(code, "test.py", "python")

        assert match is not None
        assert match.fixed_code == "swap(b, a)"
        assert match.capture_groups == {"1": "a", "2": "b"}

    def test_capture_group_brace_syntax(self, registry):
        """Match with ${N} capture group syntax."""
        template = FixTemplate(
            name="brace-syntax",
            pattern=r'old_func\((\w+)\)',
            pattern_type=PatternType.REGEX,
            replacement="new_func(${1}_modified)",
        )
        registry._templates.append(template)

        code = "old_func(value)"
        match = registry.match(code, "test.py", "python")

        assert match is not None
        assert match.fixed_code == "new_func(value_modified)"

    def test_language_filter(self, registry):
        """Templates are filtered by language."""
        template = FixTemplate(
            name="python-only",
            pattern="python_specific",
            pattern_type=PatternType.LITERAL,
            replacement="fixed",
            languages=["python"],
        )
        registry._templates.append(template)

        code = "python_specific code"

        # Should match for Python
        match = registry.match(code, "test.py", "python")
        assert match is not None

        # Should not match for TypeScript
        match = registry.match(code, "test.ts", "typescript")
        assert match is None

    def test_file_pattern_filter(self, registry):
        """Templates are filtered by file pattern."""
        template = FixTemplate(
            name="models-only",
            pattern="old_pattern",
            pattern_type=PatternType.LITERAL,
            replacement="new_pattern",
            file_pattern="**/models.py",
        )
        registry._templates.append(template)

        code = "old_pattern here"

        # Should match models.py
        match = registry.match(code, "app/models.py", "python")
        assert match is not None

        # Should match nested models.py
        match = registry.match(code, "deep/nested/models.py", "python")
        assert match is not None

        # Should not match other files
        match = registry.match(code, "app/views.py", "python")
        assert match is None

    def test_no_match(self, registry):
        """No match returns None."""
        template = FixTemplate(
            name="no-match",
            pattern="pattern_not_in_code",
            pattern_type=PatternType.LITERAL,
            replacement="fixed",
        )
        registry._templates.append(template)

        code = "completely different code"
        match = registry.match(code, "test.py", "python")

        assert match is None

    def test_priority_order_matching(self, registry):
        """Higher priority templates are matched first."""
        # Add lower priority first
        low_priority = FixTemplate(
            name="low-priority",
            pattern="match_me",
            pattern_type=PatternType.LITERAL,
            replacement="low_replacement",
            priority=30,
        )
        high_priority = FixTemplate(
            name="high-priority",
            pattern="match_me",
            pattern_type=PatternType.LITERAL,
            replacement="high_replacement",
            priority=70,
        )

        registry._templates.extend([low_priority, high_priority])
        registry._sort_by_priority()

        code = "match_me"
        match = registry.match(code, "test.py", "python")

        assert match is not None
        assert match.template.name == "high-priority"
        assert match.fixed_code == "high_replacement"

    def test_match_all(self, registry):
        """match_all returns all matching templates."""
        template1 = FixTemplate(
            name="template1",
            pattern="target",
            pattern_type=PatternType.LITERAL,
            replacement="replacement1",
            priority=60,
        )
        template2 = FixTemplate(
            name="template2",
            pattern="target",
            pattern_type=PatternType.LITERAL,
            replacement="replacement2",
            priority=40,
        )

        registry._templates.extend([template1, template2])

        code = "target here"
        matches = registry.match_all(code, "test.py", "python")

        assert len(matches) == 2
        # Should be ordered by priority (descending)
        assert matches[0].template.name == "template1"
        assert matches[1].template.name == "template2"


class TestGlobalRegistry:
    """Tests for global registry management."""

    def teardown_method(self):
        """Reset global registry after each test."""
        reset_registry()

    def test_get_registry_singleton(self):
        """get_registry returns same instance on repeated calls."""
        registry1 = get_registry()
        registry2 = get_registry()

        assert registry1 is registry2

    def test_reset_registry(self):
        """reset_registry clears the singleton."""
        registry1 = get_registry()
        reset_registry()
        registry2 = get_registry()

        assert registry1 is not registry2

    def test_custom_template_dirs(self, tmp_path):
        """Custom template directories are loaded."""
        # Create custom directory with template
        custom_dir = tmp_path / "custom-templates"
        custom_dir.mkdir()

        yaml_content = {
            "templates": [
                {
                    "name": "custom-template",
                    "pattern": "custom_pattern",
                    "replacement": "custom_replacement",
                }
            ]
        }

        yaml_file = custom_dir / "custom.yaml"
        with open(yaml_file, "w") as f:
            yaml.dump(yaml_content, f)

        reset_registry()
        registry = get_registry(template_dirs=[custom_dir])

        assert len(registry.templates) == 1
        assert registry.templates[0].name == "custom-template"


class TestTemplateMatch:
    """Tests for TemplateMatch model."""

    def test_template_match_creation(self):
        """Create TemplateMatch with all fields."""
        template = FixTemplate(
            name="test",
            pattern="old",
            replacement="new",
        )

        match = TemplateMatch(
            template=template,
            original_code="old_code",
            fixed_code="new_code",
            match_start=10,
            match_end=20,
            capture_groups={"1": "captured"},
        )

        assert match.template.name == "test"
        assert match.original_code == "old_code"
        assert match.fixed_code == "new_code"
        assert match.match_start == 10
        assert match.match_end == 20
        assert match.capture_groups["1"] == "captured"


class TestTemplateEvidence:
    """Tests for TemplateEvidence model."""

    def test_default_evidence(self):
        """Default evidence has empty lists."""
        evidence = TemplateEvidence()

        assert evidence.documentation_refs == []
        assert evidence.best_practices == []

    def test_populated_evidence(self):
        """Evidence with populated fields."""
        evidence = TemplateEvidence(
            documentation_refs=["PEP 8", "Google Style Guide"],
            best_practices=["Use is None for None comparisons"],
        )

        assert len(evidence.documentation_refs) == 2
        assert "PEP 8" in evidence.documentation_refs
        assert len(evidence.best_practices) == 1


class TestTemplateFile:
    """Tests for TemplateFile model."""

    def test_empty_template_file(self):
        """Empty template file has empty list."""
        tf = TemplateFile()
        assert tf.templates == []

    def test_template_file_with_templates(self):
        """Template file with templates."""
        templates = [
            FixTemplate(name="t1", pattern="p1", replacement="r1"),
            FixTemplate(name="t2", pattern="p2", replacement="r2"),
        ]

        tf = TemplateFile(templates=templates)

        assert len(tf.templates) == 2
        assert tf.templates[0].name == "t1"


class TestEdgeCases:
    """Tests for edge cases and error conditions."""

    @pytest.fixture
    def registry(self):
        return TemplateRegistry()

    def test_empty_code(self, registry):
        """Matching against empty code."""
        template = FixTemplate(
            name="test",
            pattern="anything",
            replacement="x",
        )
        registry._templates.append(template)

        match = registry.match("", "test.py", "python")
        assert match is None

    def test_multiline_regex(self, registry):
        """Regex with MULTILINE and DOTALL flags."""
        template = FixTemplate(
            name="multiline",
            pattern=r"def\s+old_func\([^)]*\):\s*pass",
            pattern_type=PatternType.REGEX,
            replacement="def new_func():\n    pass",
        )
        registry._templates.append(template)

        code = """
def old_func(
    arg1, arg2
): pass
"""
        match = registry.match(code, "test.py", "python")
        assert match is not None

    def test_special_regex_characters_in_literal(self, registry):
        """Literal patterns don't interpret regex special chars."""
        template = FixTemplate(
            name="literal-special",
            pattern="func()",  # () would be group in regex
            pattern_type=PatternType.LITERAL,
            replacement="func(arg)",
        )
        registry._templates.append(template)

        code = "result = func()"
        match = registry.match(code, "test.py", "python")

        assert match is not None
        assert match.original_code == "func()"

    def test_case_sensitive_language_match(self, registry):
        """Language matching is case-insensitive."""
        template = FixTemplate(
            name="test",
            pattern="code",
            replacement="fixed",
            languages=["Python", "TYPESCRIPT"],
        )
        registry._templates.append(template)

        code = "code here"

        # Case variations should all match
        assert registry.match(code, "test.py", "python") is not None
        assert registry.match(code, "test.py", "PYTHON") is not None
        assert registry.match(code, "test.ts", "typescript") is not None
        assert registry.match(code, "test.ts", "TypeScript") is not None

    def test_yaml_with_empty_templates_list(self, registry, tmp_path):
        """YAML file with empty templates list."""
        yaml_content = {"templates": []}

        yaml_file = tmp_path / "empty.yaml"
        with open(yaml_file, "w") as f:
            yaml.dump(yaml_content, f)

        count = registry.load_from_file(yaml_file)
        assert count == 0

    def test_empty_yaml_file(self, registry, tmp_path):
        """Completely empty YAML file."""
        yaml_file = tmp_path / "empty.yaml"
        yaml_file.write_text("")

        count = registry.load_from_file(yaml_file)
        assert count == 0
