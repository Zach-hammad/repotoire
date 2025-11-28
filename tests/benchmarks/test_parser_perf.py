"""Parser performance benchmarks."""

import pytest
from pathlib import Path

from repotoire.parsers.python_parser import PythonParser


class TestParserPerformance:
    """Benchmark parser performance."""

    def test_parse_single_file(self, benchmark, sample_python_files):
        """Benchmark parsing a single Python file."""
        parser = PythonParser()
        file_path = sample_python_files / "src" / "utils" / "helpers.py"

        def parse_file():
            return parser.parse(file_path)

        result = benchmark(parse_file)
        assert result is not None

    def test_parse_multiple_files(self, benchmark, sample_python_files):
        """Benchmark parsing multiple Python files."""
        parser = PythonParser()
        files = list(sample_python_files.rglob("*.py"))

        def parse_all():
            results = []
            for f in files:
                results.append(parser.parse(f))
            return results

        results = benchmark(parse_all)
        assert len(results) == len(files)

    def test_extract_entities(self, benchmark, sample_python_files):
        """Benchmark entity extraction from parsed AST."""
        parser = PythonParser()
        file_path = sample_python_files / "src" / "utils" / "helpers.py"
        module = parser.parse(file_path)

        def extract():
            return parser.extract_entities(module, file_path)

        entities = benchmark(extract)
        assert len(entities) > 0

    def test_extract_relationships(self, benchmark, sample_python_files):
        """Benchmark relationship extraction from parsed AST."""
        parser = PythonParser()
        file_path = sample_python_files / "src" / "utils" / "helpers.py"
        module = parser.parse(file_path)
        entities = parser.extract_entities(module, file_path)

        def extract():
            return parser.extract_relationships(module, file_path, entities)

        relationships = benchmark(extract)
        assert relationships is not None
