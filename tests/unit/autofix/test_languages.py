"""Comprehensive tests for multi-language auto-fix handlers."""

import pytest
from unittest.mock import patch, MagicMock
import subprocess

from repotoire.autofix.languages import (
    LanguageHandler,
    PythonHandler,
    TypeScriptHandler,
    JavaHandler,
    GoHandler,
    get_handler,
    get_handler_for_language,
    supported_extensions,
    clear_handler_cache,
)


@pytest.fixture(autouse=True)
def clear_cache():
    """Clear handler cache before each test."""
    clear_handler_cache()
    yield
    clear_handler_cache()


class TestPythonHandler:
    """Tests for PythonHandler."""

    @pytest.fixture
    def handler(self):
        return PythonHandler()

    def test_language_name(self, handler):
        """Test language name property."""
        assert handler.language_name == "Python"

    def test_file_extensions(self, handler):
        """Test supported file extensions."""
        assert ".py" in handler.file_extensions
        assert ".pyi" in handler.file_extensions
        assert ".pyw" in handler.file_extensions

    def test_validate_syntax_valid_code(self, handler):
        """Test validation of valid Python code."""
        code = "def foo():\n    return 42"
        assert handler.validate_syntax(code) is True

    def test_validate_syntax_invalid_code(self, handler):
        """Test validation of invalid Python code."""
        code = "def foo(:\n    return"
        assert handler.validate_syntax(code) is False

    def test_validate_syntax_with_indentation(self, handler):
        """Test validation handles indented code snippets."""
        code = """
            def foo():
                return 42
        """
        assert handler.validate_syntax(code) is True

    def test_validate_syntax_empty_string(self, handler):
        """Test validation of empty string."""
        assert handler.validate_syntax("") is True  # Empty is valid Python

    def test_validate_syntax_multiline(self, handler):
        """Test validation of complex multiline code."""
        code = """
class MyClass:
    def __init__(self, value: int):
        self.value = value

    def process(self) -> str:
        return str(self.value)
"""
        assert handler.validate_syntax(code) is True

    def test_extract_imports_simple(self, handler):
        """Test extraction of simple imports."""
        code = """
import os
import sys
"""
        imports = handler.extract_imports(code)
        assert "import os" in imports
        assert "import sys" in imports

    def test_extract_imports_from_imports(self, handler):
        """Test extraction of from imports."""
        code = """
from pathlib import Path
from typing import Optional, List
"""
        imports = handler.extract_imports(code)
        assert "from pathlib import Path" in imports
        assert "from typing import Optional" in imports
        assert "from typing import List" in imports

    def test_extract_imports_with_syntax_error(self, handler):
        """Test that import extraction handles syntax errors gracefully."""
        code = "this is not valid {python}"
        imports = handler.extract_imports(code)
        assert imports == []

    def test_extract_imports_relative_imports(self, handler):
        """Test extraction of relative imports."""
        code = """
from . import module
from ..package import something
"""
        imports = handler.extract_imports(code)
        # Python AST uses empty string or the module name for relative imports
        assert "from  import module" in imports  # relative imports (. becomes "")
        assert "from package import something" in imports  # ..package becomes "package"

    def test_get_system_prompt(self, handler):
        """Test system prompt contains expected content."""
        prompt = handler.get_system_prompt()
        assert "Python" in prompt
        assert "PEP" in prompt
        assert "refactor" in prompt.lower()

    def test_get_fix_template_security(self, handler):
        """Test security fix template."""
        template = handler.get_fix_template("security")
        assert "SQL" in template or "security" in template.lower()

    def test_get_fix_template_refactor(self, handler):
        """Test refactor fix template."""
        template = handler.get_fix_template("refactor")
        assert "Extract" in template or "refactor" in template.lower()

    def test_get_fix_template_unknown(self, handler):
        """Test unknown fix type falls back to refactor."""
        template = handler.get_fix_template("unknown_type")
        refactor_template = handler.get_fix_template("refactor")
        assert template == refactor_template

    def test_code_block_marker(self, handler):
        """Test code block marker."""
        assert handler.get_code_block_marker() == "python"


class TestTypeScriptHandler:
    """Tests for TypeScriptHandler."""

    @pytest.fixture
    def handler(self):
        return TypeScriptHandler()

    def test_language_name(self, handler):
        """Test language name property."""
        assert handler.language_name == "TypeScript"

    def test_file_extensions(self, handler):
        """Test supported file extensions."""
        extensions = handler.file_extensions
        assert ".ts" in extensions
        assert ".tsx" in extensions
        assert ".js" in extensions
        assert ".jsx" in extensions
        assert ".mjs" in extensions
        assert ".cjs" in extensions

    def test_validate_syntax_no_tools(self, handler):
        """Test validation returns True when no tools are available."""
        handler._esbuild_path = None
        handler._tsc_path = None
        code = "const x: number = 42;"
        assert handler.validate_syntax(code) is True

    @patch("subprocess.run")
    def test_validate_syntax_with_esbuild(self, mock_run, handler):
        """Test validation using esbuild."""
        handler._esbuild_path = "/usr/bin/esbuild"
        handler._tsc_path = None
        mock_run.return_value = MagicMock(returncode=0)

        code = "const x: number = 42;"
        assert handler.validate_syntax(code) is True
        mock_run.assert_called_once()

    @patch("subprocess.run")
    def test_validate_syntax_esbuild_error(self, mock_run, handler):
        """Test validation failure with esbuild."""
        handler._esbuild_path = "/usr/bin/esbuild"
        handler._tsc_path = None
        mock_run.return_value = MagicMock(returncode=1)

        code = "const x: number = ;"  # Invalid
        assert handler.validate_syntax(code) is False

    @patch("subprocess.run")
    def test_validate_syntax_fallback_to_tsc(self, mock_run, handler):
        """Test fallback to tsc when esbuild fails."""
        handler._esbuild_path = "/usr/bin/esbuild"
        handler._tsc_path = "/usr/bin/tsc"

        # First call (esbuild) raises exception, second (tsc) succeeds
        mock_run.side_effect = [
            Exception("esbuild failed"),
            MagicMock(returncode=0),
        ]

        code = "const x: number = 42;"
        assert handler.validate_syntax(code) is True
        assert mock_run.call_count == 2

    def test_extract_imports_es6(self, handler):
        """Test extraction of ES6 imports."""
        code = """
import React from 'react';
import { useState, useEffect } from 'react';
import * as lodash from 'lodash';
"""
        imports = handler.extract_imports(code)
        assert any("'react'" in imp for imp in imports)
        assert any("'lodash'" in imp for imp in imports)

    def test_extract_imports_type_imports(self, handler):
        """Test extraction of type imports."""
        code = """
import type { User } from './types';
import { Component } from 'react';
"""
        imports = handler.extract_imports(code)
        assert any("'./types'" in imp for imp in imports)
        assert any("'react'" in imp for imp in imports)

    def test_extract_imports_require(self, handler):
        """Test extraction of CommonJS require."""
        code = """
const fs = require('fs');
const { join } = require('path');
"""
        imports = handler.extract_imports(code)
        assert any("'fs'" in imp for imp in imports)
        assert any("'path'" in imp for imp in imports)

    def test_get_system_prompt(self, handler):
        """Test system prompt content."""
        prompt = handler.get_system_prompt()
        assert "TypeScript" in prompt
        assert "ESLint" in prompt or "strict" in prompt.lower()

    def test_get_fix_template_security(self, handler):
        """Test security fix template."""
        template = handler.get_fix_template("security")
        assert "sanitize" in template.lower() or "DOMPurify" in template

    def test_code_block_marker(self, handler):
        """Test code block marker."""
        assert handler.get_code_block_marker() == "typescript"


class TestJavaHandler:
    """Tests for JavaHandler."""

    @pytest.fixture
    def handler(self):
        return JavaHandler()

    def test_language_name(self, handler):
        """Test language name property."""
        assert handler.language_name == "Java"

    def test_file_extensions(self, handler):
        """Test supported file extensions."""
        assert handler.file_extensions == [".java"]

    def test_validate_structure_balanced_braces(self, handler):
        """Test structural validation with balanced braces."""
        code = """
public class Test {
    public void method() {
        if (true) {
            System.out.println("Hello");
        }
    }
}
"""
        assert handler._validate_structure(code) is True

    def test_validate_structure_unbalanced_braces(self, handler):
        """Test structural validation with unbalanced braces."""
        code = """
public class Test {
    public void method() {
        if (true) {
            System.out.println("Hello");
        }
    }
"""  # Missing closing brace
        assert handler._validate_structure(code) is False

    def test_validate_structure_unbalanced_parens(self, handler):
        """Test structural validation with unbalanced parentheses."""
        code = """
public class Test {
    public void method((String x) {
    }
}
"""  # Extra opening paren
        assert handler._validate_structure(code) is False

    def test_validate_syntax_no_javac(self, handler):
        """Test validation uses fallback when javac unavailable."""
        handler._javac_path = None
        code = """
public class Test {
    public void method() {}
}
"""
        assert handler.validate_syntax(code) is True

    @patch("subprocess.run")
    def test_validate_syntax_with_javac(self, mock_run, handler):
        """Test validation using javac."""
        handler._javac_path = "/usr/bin/javac"
        mock_run.return_value = MagicMock(returncode=0)

        code = """
public class Test {
    public void method() {}
}
"""
        assert handler.validate_syntax(code) is True

    def test_extract_imports_standard(self, handler):
        """Test extraction of standard imports."""
        code = """
package com.example;

import java.util.List;
import java.util.ArrayList;
import static java.lang.Math.PI;
"""
        imports = handler.extract_imports(code)
        assert "import java.util.List" in imports
        assert "import java.util.ArrayList" in imports
        assert "import static java.lang.Math.PI" in imports

    def test_extract_imports_wildcard(self, handler):
        """Test extraction of wildcard imports."""
        code = """
import java.util.*;
import static org.junit.Assert.*;
"""
        imports = handler.extract_imports(code)
        assert "import java.util.*" in imports
        assert "import static org.junit.Assert.*" in imports

    def test_get_system_prompt(self, handler):
        """Test system prompt content."""
        prompt = handler.get_system_prompt()
        assert "Java" in prompt
        assert "SOLID" in prompt or "Effective Java" in prompt

    def test_get_fix_template_security(self, handler):
        """Test security fix template."""
        template = handler.get_fix_template("security")
        assert "PreparedStatement" in template or "SQL" in template

    def test_code_block_marker(self, handler):
        """Test code block marker."""
        assert handler.get_code_block_marker() == "java"


class TestGoHandler:
    """Tests for GoHandler."""

    @pytest.fixture
    def handler(self):
        return GoHandler()

    def test_language_name(self, handler):
        """Test language name property."""
        assert handler.language_name == "Go"

    def test_file_extensions(self, handler):
        """Test supported file extensions."""
        assert handler.file_extensions == [".go"]

    def test_validate_syntax_no_gofmt(self, handler):
        """Test validation returns True when gofmt unavailable."""
        handler._gofmt_path = None
        handler._go_path = None
        code = "package main\n\nfunc main() {}"
        assert handler.validate_syntax(code) is True

    @patch("subprocess.run")
    def test_validate_syntax_with_gofmt(self, mock_run, handler):
        """Test validation using gofmt."""
        handler._gofmt_path = "/usr/bin/gofmt"
        handler._go_path = None
        mock_run.return_value = MagicMock(returncode=0)

        code = "package main\n\nfunc main() {}"
        assert handler.validate_syntax(code) is True
        mock_run.assert_called_once()

    @patch("subprocess.run")
    def test_validate_syntax_gofmt_error(self, mock_run, handler):
        """Test validation failure with gofmt."""
        handler._gofmt_path = "/usr/bin/gofmt"
        handler._go_path = None
        mock_run.return_value = MagicMock(returncode=1)

        code = "package main\n\nfunc main( {}"  # Invalid
        assert handler.validate_syntax(code) is False

    @patch("subprocess.run")
    def test_validate_syntax_timeout(self, mock_run, handler):
        """Test validation returns True on timeout."""
        handler._gofmt_path = "/usr/bin/gofmt"
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="gofmt", timeout=10)

        code = "package main\n\nfunc main() {}"
        assert handler.validate_syntax(code) is True

    def test_extract_imports_single(self, handler):
        """Test extraction of single imports."""
        code = """
package main

import "fmt"
import "strings"
"""
        imports = handler.extract_imports(code)
        assert 'import "fmt"' in imports
        assert 'import "strings"' in imports

    def test_extract_imports_grouped(self, handler):
        """Test extraction of grouped imports."""
        code = """
package main

import (
    "fmt"
    "strings"
    myalias "github.com/pkg/errors"
)
"""
        imports = handler.extract_imports(code)
        assert any('"fmt"' in imp for imp in imports)
        assert any('"strings"' in imp for imp in imports)
        assert any('"github.com/pkg/errors"' in imp for imp in imports)

    def test_extract_imports_mixed(self, handler):
        """Test extraction with dot imports and blank imports."""
        code = """
package main

import (
    . "testing"
    _ "github.com/lib/pq"
    "os"
)
"""
        imports = handler.extract_imports(code)
        assert any('"testing"' in imp for imp in imports)
        assert any('"github.com/lib/pq"' in imp for imp in imports)
        assert any('"os"' in imp for imp in imports)

    def test_get_system_prompt(self, handler):
        """Test system prompt content."""
        prompt = handler.get_system_prompt()
        assert "Go" in prompt
        assert "Effective Go" in prompt or "gofmt" in prompt.lower()

    def test_get_fix_template_security(self, handler):
        """Test security fix template."""
        template = handler.get_fix_template("security")
        assert "crypto/rand" in template or "prepared" in template.lower()

    def test_code_block_marker(self, handler):
        """Test code block marker."""
        assert handler.get_code_block_marker() == "go"


class TestGetHandler:
    """Tests for the get_handler factory function."""

    def test_get_python_handler(self):
        """Test getting handler for Python files."""
        handler = get_handler("src/module.py")
        assert isinstance(handler, PythonHandler)

    def test_get_typescript_handler_ts(self):
        """Test getting handler for .ts files."""
        handler = get_handler("components/Button.ts")
        assert isinstance(handler, TypeScriptHandler)

    def test_get_typescript_handler_tsx(self):
        """Test getting handler for .tsx files."""
        handler = get_handler("components/Button.tsx")
        assert isinstance(handler, TypeScriptHandler)

    def test_get_typescript_handler_js(self):
        """Test getting handler for .js files."""
        handler = get_handler("utils/helper.js")
        assert isinstance(handler, TypeScriptHandler)

    def test_get_typescript_handler_jsx(self):
        """Test getting handler for .jsx files."""
        handler = get_handler("components/App.jsx")
        assert isinstance(handler, TypeScriptHandler)

    def test_get_java_handler(self):
        """Test getting handler for Java files."""
        handler = get_handler("Main.java")
        assert isinstance(handler, JavaHandler)

    def test_get_go_handler(self):
        """Test getting handler for Go files."""
        handler = get_handler("server/main.go")
        assert isinstance(handler, GoHandler)

    def test_get_handler_default_fallback(self):
        """Test default fallback to Python for unknown extensions."""
        handler = get_handler("unknown.xyz")
        assert isinstance(handler, PythonHandler)

    def test_get_handler_case_insensitive(self):
        """Test extension matching is case insensitive."""
        handler = get_handler("TEST.PY")
        assert isinstance(handler, PythonHandler)

    def test_get_handler_caches_handlers(self):
        """Test that handlers are cached."""
        handler1 = get_handler("file1.py")
        handler2 = get_handler("file2.py")
        assert handler1 is handler2

    def test_get_handler_full_path(self):
        """Test handler selection with full path."""
        handler = get_handler("/home/user/project/src/module.py")
        assert isinstance(handler, PythonHandler)


class TestGetHandlerForLanguage:
    """Tests for the get_handler_for_language function."""

    def test_get_python_by_name(self):
        """Test getting Python handler by name."""
        handler = get_handler_for_language("python")
        assert isinstance(handler, PythonHandler)

    def test_get_typescript_by_name(self):
        """Test getting TypeScript handler by name."""
        handler = get_handler_for_language("typescript")
        assert isinstance(handler, TypeScriptHandler)

    def test_get_javascript_by_name(self):
        """Test getting JavaScript handler (maps to TypeScript)."""
        handler = get_handler_for_language("javascript")
        assert isinstance(handler, TypeScriptHandler)

    def test_get_java_by_name(self):
        """Test getting Java handler by name."""
        handler = get_handler_for_language("java")
        assert isinstance(handler, JavaHandler)

    def test_get_go_by_name(self):
        """Test getting Go handler by name."""
        handler = get_handler_for_language("go")
        assert isinstance(handler, GoHandler)

    def test_get_golang_by_name(self):
        """Test getting Go handler with 'golang' name."""
        handler = get_handler_for_language("golang")
        assert isinstance(handler, GoHandler)

    def test_case_insensitive(self):
        """Test language name matching is case insensitive."""
        handler = get_handler_for_language("PYTHON")
        assert isinstance(handler, PythonHandler)

    def test_unknown_language_fallback(self):
        """Test fallback to Python for unknown languages."""
        handler = get_handler_for_language("rust")
        assert isinstance(handler, PythonHandler)


class TestSupportedExtensions:
    """Tests for the supported_extensions function."""

    def test_returns_list(self):
        """Test that function returns a list."""
        extensions = supported_extensions()
        assert isinstance(extensions, list)

    def test_contains_python(self):
        """Test that Python extensions are included."""
        extensions = supported_extensions()
        assert ".py" in extensions

    def test_contains_typescript(self):
        """Test that TypeScript extensions are included."""
        extensions = supported_extensions()
        assert ".ts" in extensions
        assert ".tsx" in extensions

    def test_contains_java(self):
        """Test that Java extension is included."""
        extensions = supported_extensions()
        assert ".java" in extensions

    def test_contains_go(self):
        """Test that Go extension is included."""
        extensions = supported_extensions()
        assert ".go" in extensions


class TestLanguageHandlerInterface:
    """Tests to ensure all handlers implement the interface correctly."""

    @pytest.mark.parametrize(
        "handler_class",
        [PythonHandler, TypeScriptHandler, JavaHandler, GoHandler],
    )
    def test_implements_abstract_methods(self, handler_class):
        """Test that all handlers implement required abstract methods."""
        handler = handler_class()

        # Test all abstract methods are implemented
        assert isinstance(handler.language_name, str)
        assert isinstance(handler.file_extensions, list)
        assert callable(handler.validate_syntax)
        assert callable(handler.extract_imports)
        assert callable(handler.get_system_prompt)
        assert callable(handler.get_fix_template)

    @pytest.mark.parametrize(
        "handler_class",
        [PythonHandler, TypeScriptHandler, JavaHandler, GoHandler],
    )
    def test_methods_return_correct_types(self, handler_class):
        """Test that methods return correct types."""
        handler = handler_class()

        assert isinstance(handler.validate_syntax("code"), bool)
        assert isinstance(handler.extract_imports("code"), list)
        assert isinstance(handler.get_system_prompt(), str)
        assert isinstance(handler.get_fix_template("refactor"), str)
        assert isinstance(handler.get_code_block_marker(), str)

    @pytest.mark.parametrize(
        "handler_class",
        [PythonHandler, TypeScriptHandler, JavaHandler, GoHandler],
    )
    def test_fix_templates_cover_all_types(self, handler_class):
        """Test that handlers have templates for all fix types."""
        handler = handler_class()
        fix_types = [
            "security",
            "simplify",
            "refactor",
            "extract",
            "remove",
            "documentation",
            "type_hint",
        ]

        for fix_type in fix_types:
            template = handler.get_fix_template(fix_type)
            assert len(template) > 0, f"Empty template for {fix_type}"


class TestEdgeCases:
    """Tests for edge cases and error handling."""

    def test_python_validate_syntax_with_unicode(self):
        """Test Python validation with unicode content."""
        handler = PythonHandler()
        code = 'x = "héllo wörld"'
        assert handler.validate_syntax(code) is True

    def test_typescript_extract_imports_with_comments(self):
        """Test TypeScript import extraction with commented imports."""
        handler = TypeScriptHandler()
        code = """
// import React from 'react';
import Vue from 'vue';
/* import Angular from '@angular/core'; */
"""
        imports = handler.extract_imports(code)
        # Should extract both the commented and uncommented imports
        # (regex doesn't distinguish comments)
        assert any("'vue'" in imp for imp in imports)

    def test_java_validate_empty_class(self):
        """Test Java validation with empty class."""
        handler = JavaHandler()
        handler._javac_path = None  # Force fallback
        code = "class Empty {}"
        assert handler.validate_syntax(code) is True

    def test_go_extract_imports_empty(self):
        """Test Go import extraction with no imports."""
        handler = GoHandler()
        code = """
package main

func main() {
    println("Hello")
}
"""
        imports = handler.extract_imports(code)
        assert imports == []

    def test_handler_with_no_extension(self):
        """Test handler selection for file with no extension."""
        handler = get_handler("Makefile")
        assert isinstance(handler, PythonHandler)  # Default fallback

    def test_handler_with_multiple_dots(self):
        """Test handler selection for file with multiple dots."""
        handler = get_handler("component.test.tsx")
        assert isinstance(handler, TypeScriptHandler)
