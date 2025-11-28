"""Pytest configuration for benchmarks."""

import pytest
import tempfile
from pathlib import Path


@pytest.fixture
def sample_python_files():
    """Create a set of sample Python files for benchmarking."""
    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)

        # Create a realistic file structure
        (base / "src").mkdir()
        (base / "src" / "utils").mkdir()
        (base / "tests").mkdir()

        # Main module
        (base / "src" / "__init__.py").write_text('"""Main package."""')

        # Utility module with various constructs
        (base / "src" / "utils" / "__init__.py").write_text(
            '"""Utility functions."""\n'
            'from .helpers import calculate_sum, process_data\n'
        )

        (base / "src" / "utils" / "helpers.py").write_text('''
"""Helper functions."""

from typing import List, Optional
import json


class DataProcessor:
    """Process data efficiently."""

    def __init__(self, config: dict):
        self.config = config
        self._cache = {}

    def process(self, data: List[dict]) -> List[dict]:
        """Process a list of data items."""
        results = []
        for item in data:
            processed = self._process_item(item)
            results.append(processed)
        return results

    def _process_item(self, item: dict) -> dict:
        """Process a single item."""
        key = json.dumps(item, sort_keys=True)
        if key in self._cache:
            return self._cache[key]

        result = {k: v.upper() if isinstance(v, str) else v for k, v in item.items()}
        self._cache[key] = result
        return result


def calculate_sum(numbers: List[int]) -> int:
    """Calculate sum of numbers."""
    return sum(numbers)


def process_data(data: List[dict], processor: Optional[DataProcessor] = None) -> List[dict]:
    """Process data using optional processor."""
    if processor is None:
        processor = DataProcessor({})
    return processor.process(data)


class ConfigManager:
    """Manage configuration."""

    _instance = None

    @classmethod
    def get_instance(cls) -> "ConfigManager":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        self.settings = {}

    def load(self, path: str) -> None:
        with open(path) as f:
            self.settings = json.load(f)

    def get(self, key: str, default=None):
        return self.settings.get(key, default)
''')

        # Create multiple service modules
        for i in range(5):
            (base / "src" / f"service_{i}.py").write_text(f'''
"""Service module {i}."""

from .utils.helpers import DataProcessor, calculate_sum


class Service{i}:
    """Service implementation {i}."""

    def __init__(self):
        self.processor = DataProcessor({{"service": {i}}})

    def run(self, data):
        """Run the service."""
        processed = self.processor.process(data)
        total = calculate_sum([len(d) for d in processed])
        return {{"result": processed, "total": total}}
''')

        yield base


@pytest.fixture
def large_graph_data():
    """Generate data for large graph benchmarks."""
    nodes = []
    relationships = []

    # Create 1000 function nodes
    for i in range(1000):
        nodes.append({
            "qualifiedName": f"module.function_{i}",
            "name": f"function_{i}",
            "type": "Function",
            "complexity": i % 10 + 1,
        })

    # Create relationships (calls between functions)
    for i in range(1000):
        for j in range(min(5, 1000 - i - 1)):
            relationships.append({
                "source": f"module.function_{i}",
                "target": f"module.function_{i + j + 1}",
                "type": "CALLS",
            })

    return {"nodes": nodes, "relationships": relationships}
