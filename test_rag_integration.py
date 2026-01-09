"""Test RAG integration in SchemaGenerator.

Tests:
1. Graceful degradation when no embeddings available
2. RAG code path with mocked retriever
3. Side-by-side comparison of baseline vs RAG-enhanced
"""

import json
import os
from unittest.mock import Mock, MagicMock
from repotoire.graph import FalkorDBClient
from repotoire.mcp import PatternDetector, SchemaGenerator, DetectedPattern, FunctionPattern
from repotoire.mcp.models import Parameter

def test_graceful_degradation():
    """Test that SchemaGenerator works fine without RAG."""
    print("=" * 80)
    print("TEST 1: Graceful Degradation (No RAG)")
    print("=" * 80)

    # Create generator without RAG
    generator = SchemaGenerator()

    # Create a test pattern
    pattern = FunctionPattern(
        function_name="calculate_score",
        module_name="scoring",
        qualified_name="scoring.calculate_score",
        parameters=[
            Parameter(name="data", type_hint="Dict[str, Any]", required=True),
            Parameter(name="threshold", type_hint="float", required=False, default_value="0.5")
        ],
        return_type="float",
        docstring="""Calculate quality score for data.

        Args:
            data: Input data dictionary
            threshold: Minimum threshold for passing score

        Returns:
            Quality score between 0 and 1
        """
    )

    schema = generator.generate_tool_schema(pattern)

    print("\n✅ Schema generated without RAG:")
    print(json.dumps(schema, indent=2))

    # Verify structure
    assert "name" in schema
    assert "description" in schema
    assert "inputSchema" in schema
    assert schema["name"] == "calculate_score"

    print("\n✅ All assertions passed - graceful degradation works!")


def test_rag_code_path():
    """Test RAG code path with mocked retriever."""
    print("\n" + "=" * 80)
    print("TEST 2: RAG Code Path (Mocked Retriever)")
    print("=" * 80)

    # Create mock retriever
    mock_retriever = Mock()

    # Create a realistic mock result
    mock_result = Mock()
    mock_result.entity_type = "Function"
    mock_result.qualified_name = "scoring.calculate_score"
    mock_result.docstring = """Calculate quality score.

    Args:
        data: Dictionary containing metrics and measurements to evaluate
        threshold: Cutoff value below which the score is considered failing
    """
    mock_result.code = "def calculate_score(data, threshold=0.5): ..."

    # Configure retriever to return results
    mock_retriever.retrieve.return_value = [mock_result]

    # Create generator with mocked RAG
    generator = SchemaGenerator(rag_retriever=mock_retriever)

    # Create test pattern
    pattern = FunctionPattern(
        function_name="calculate_score",
        module_name="scoring",
        qualified_name="scoring.calculate_score",
        parameters=[
            Parameter(name="data", type_hint="Dict[str, Any]", required=True),
            Parameter(name="threshold", type_hint="float", required=False, default_value="0.5")
        ],
        return_type="float",
        docstring="Calculate quality score."  # Minimal docstring
    )

    schema = generator.generate_tool_schema(pattern)

    print("\n✅ Schema generated with mocked RAG:")
    print(json.dumps(schema, indent=2))

    # Verify RAG was called
    print(f"\n📊 RAG retriever called: {mock_retriever.retrieve.called}")
    print(f"   Call count: {mock_retriever.retrieve.call_count}")

    if mock_retriever.retrieve.called:
        print("   ✅ RAG integration is wired up correctly!")
        print(f"   Queries made:")
        for call in mock_retriever.retrieve.call_args_list:
            args, kwargs = call
            if 'query' in kwargs:
                print(f"     - {kwargs['query']}")

    # Check if parameters got enhanced descriptions
    param_schema = schema["inputSchema"]["properties"]
    print(f"\n📝 Parameter descriptions:")
    for param_name, param_def in param_schema.items():
        print(f"   {param_name}: {param_def.get('description', 'N/A')}")


def test_baseline_vs_rag_comparison():
    """Compare baseline vs RAG-enhanced schemas side-by-side."""
    print("\n" + "=" * 80)
    print("TEST 3: Baseline vs RAG-Enhanced Comparison")
    print("=" * 80)

    # Create pattern with minimal docstring
    pattern = FunctionPattern(
        function_name="process_file",
        module_name="pipeline",
        qualified_name="pipeline.process_file",
        parameters=[
            Parameter(name="file_path", type_hint="str", required=True),
            Parameter(name="encoding", type_hint="str", required=False, default_value="'utf-8'")
        ],
        return_type="Dict[str, Any]",
        docstring="Process a file."  # Very minimal
    )

    # Baseline generator
    baseline_gen = SchemaGenerator()
    baseline_schema = baseline_gen.generate_tool_schema(pattern)

    # RAG-enhanced generator with mock
    mock_retriever = Mock()
    mock_result = Mock()
    mock_result.entity_type = "Function"
    mock_result.docstring = """Process and analyze a file.

    Args:
        file_path: Absolute path to the file to process
        encoding: Character encoding for reading the file (default: utf-8)

    Returns:
        Dictionary with processing results and metadata
    """
    mock_retriever.retrieve.return_value = [mock_result]

    rag_gen = SchemaGenerator(rag_retriever=mock_retriever)
    rag_schema = rag_gen.generate_tool_schema(pattern)

    # Compare
    print("\n📊 BASELINE Schema:")
    print("-" * 40)
    print(f"Description: {baseline_schema['description']}")
    print(f"Parameters:")
    for name, prop in baseline_schema['inputSchema']['properties'].items():
        print(f"  {name}: {prop.get('description', 'N/A')}")

    print("\n📊 RAG-ENHANCED Schema:")
    print("-" * 40)
    print(f"Description: {rag_schema['description']}")
    print(f"Parameters:")
    for name, prop in rag_schema['inputSchema']['properties'].items():
        print(f"  {name}: {prop.get('description', 'N/A')}")

    print("\n🔍 Analysis:")
    if baseline_schema['description'] == rag_schema['description']:
        print("   ⚠️  Descriptions are identical (RAG may not have enhanced it)")
    else:
        print("   ✅ Descriptions differ (RAG provided enhancement)")

    # Check parameter descriptions
    baseline_params = baseline_schema['inputSchema']['properties']
    rag_params = rag_schema['inputSchema']['properties']

    for param in ['file_path', 'encoding']:
        baseline_desc = baseline_params[param].get('description', '')
        rag_desc = rag_params[param].get('description', '')

        if baseline_desc != rag_desc:
            print(f"   ✅ Parameter '{param}' enhanced by RAG")
        else:
            print(f"   ℹ️  Parameter '{param}' unchanged")


def test_with_real_detector():
    """Test with real patterns from database."""
    print("\n" + "=" * 80)
    print("TEST 4: Real Patterns from Database")
    print("=" * 80)

    password = os.getenv("FALKORDB_PASSWORD", "falkor-password")
    client = FalkorDBClient(uri="bolt://localhost:7688", password=password)
    detector = PatternDetector(client)

    # Get a real function
    functions = detector.detect_public_functions(min_params=2, max_params=3)

    if not functions:
        print("⚠️  No functions found in database")
        client.close()
        return

    func = functions[0]
    print(f"\n📝 Testing with real function: {func.function_name}")
    print(f"   Module: {func.module_name}")
    print(f"   Parameters: {len(func.parameters)}")
    print(f"   Has docstring: {'Yes' if func.docstring else 'No'}")

    # Test baseline
    baseline_gen = SchemaGenerator()
    baseline_schema = baseline_gen.generate_tool_schema(func)

    print("\n✅ Generated schema:")
    print(json.dumps(baseline_schema, indent=2)[:500] + "...")

    client.close()


if __name__ == "__main__":
    test_graceful_degradation()
    test_rag_code_path()
    test_baseline_vs_rag_comparison()
    test_with_real_detector()

    print("\n" + "=" * 80)
    print("✅ All RAG integration tests complete!")
    print("=" * 80)
