"""Test RAG-enhanced schema generation.

This script demonstrates how the SchemaGenerator uses RAG to enhance
tool descriptions and parameter documentation.
"""

import json
import os
from repotoire.graph import FalkorDBClient
from repotoire.mcp import PatternDetector, SchemaGenerator

# Check if we need OpenAI API key
try:
    from repotoire.ai.retrieval import GraphRAGRetriever
    HAS_RAG = True
except ImportError:
    HAS_RAG = False
    print("⚠️  RAG dependencies not available. Install with: pip install openai")

def main():
    # Connect to FalkorDB
    password = os.getenv("FALKORDB_PASSWORD", "falkor-password")
    client = FalkorDBClient(uri="bolt://localhost:7688", password=password)

    # Create detector
    detector = PatternDetector(client)

    print("=" * 80)
    print("Schema Generation: Baseline vs RAG-Enhanced")
    print("=" * 80)

    # Test 1: Baseline schema generation (no RAG)
    print("\n1️⃣  BASELINE: Schema generation without RAG")
    print("-" * 80)

    generator_baseline = SchemaGenerator()
    routes = detector.detect_fastapi_routes()

    if routes:
        route = routes[0]
        schema = generator_baseline.generate_tool_schema(route)
        print(f"\n📍 Route: {route.http_method.value} {route.path}")
        print(f"   Function: {route.function_name}")
        print(f"   Description source: {'docstring' if route.docstring else 'generated from name'}")
        print(f"\n   Schema:")
        print(json.dumps(schema, indent=2))

    # Test 2: RAG-enhanced schema generation
    if HAS_RAG and os.getenv("OPENAI_API_KEY"):
        print("\n\n2️⃣  RAG-ENHANCED: Schema generation with knowledge graph context")
        print("-" * 80)

        # Check if embeddings exist
        result = client.execute_query(
            "MATCH (e) WHERE e.embedding IS NOT NULL RETURN count(e) as count"
        )
        embedding_count = result[0]["count"] if result else 0

        if embedding_count > 0:
            print(f"✅ Found {embedding_count} entities with embeddings")

            # Create embedder and RAG retriever
            from repotoire.ai.embeddings import CodeEmbedder
            embedder = CodeEmbedder()
            retriever = GraphRAGRetriever(graph_client=client, embedder=embedder)

            # Create RAG-enhanced generator
            generator_rag = SchemaGenerator(rag_retriever=retriever)

            # Test with a function that might not have great docstring
            functions = detector.detect_public_functions(min_params=2, max_params=3)

            if functions:
                # Find a function without docstring to test RAG enhancement
                func_no_doc = None
                func_with_doc = None

                for func in functions:
                    if not func.docstring:
                        func_no_doc = func
                    elif func.docstring:
                        func_with_doc = func

                    if func_no_doc and func_with_doc:
                        break

                if func_with_doc:
                    print(f"\n🔍 Testing RAG enhancement on: {func_with_doc.function_name}")
                    print(f"   Has docstring: Yes")

                    # Generate baseline schema
                    schema_baseline = generator_baseline.generate_tool_schema(func_with_doc)

                    # Generate RAG-enhanced schema
                    schema_rag = generator_rag.generate_tool_schema(func_with_doc)

                    print(f"\n   Baseline description:")
                    print(f"   {schema_baseline['description']}")

                    print(f"\n   RAG-enhanced description:")
                    print(f"   {schema_rag['description']}")

                    # Compare parameter descriptions
                    if func_with_doc.parameters:
                        param = func_with_doc.parameters[0]
                        baseline_param = schema_baseline['inputSchema']['properties'].get(param.name, {})
                        rag_param = schema_rag['inputSchema']['properties'].get(param.name, {})

                        print(f"\n   Parameter '{param.name}' descriptions:")
                        print(f"   Baseline: {baseline_param.get('description', 'N/A')}")
                        print(f"   RAG:      {rag_param.get('description', 'N/A')}")
        else:
            print("⚠️  No embeddings found in database")
            print("   To generate embeddings, run:")
            print("   export OPENAI_API_KEY='sk-...'")
            print("   repotoire ingest /path/to/repo --generate-embeddings")

    elif not os.getenv("OPENAI_API_KEY"):
        print("\n\n2️⃣  RAG-ENHANCED: Skipped (no OPENAI_API_KEY)")
        print("-" * 80)
        print("⚠️  Set OPENAI_API_KEY to test RAG enhancement")

    else:
        print("\n\n2️⃣  RAG-ENHANCED: Skipped (dependencies not installed)")
        print("-" * 80)

    # Test 3: Example extraction
    print("\n\n3️⃣  EXAMPLE EXTRACTION: Functions with docstring examples")
    print("-" * 80)

    functions = detector.detect_public_functions()
    functions_with_examples = []

    for func in functions[:20]:  # Check first 20
        if func.docstring and ('Example:' in func.docstring or 'Examples:' in func.docstring):
            schema = generator_baseline.generate_tool_schema(func)
            if 'examples' in schema:
                functions_with_examples.append((func, schema))

    if functions_with_examples:
        print(f"✅ Found {len(functions_with_examples)} function(s) with examples")

        func, schema = functions_with_examples[0]
        print(f"\n📝 Function: {func.function_name}")
        print(f"   Examples extracted:")
        for i, example in enumerate(schema['examples'], 1):
            print(f"   {i}. {example['code']}")
    else:
        print("ℹ️  No functions with docstring examples found in sample")

    # Test 4: Complex type handling
    print("\n\n4️⃣  COMPLEX TYPE HANDLING: Union, Optional, Literal")
    print("-" * 80)

    # Find functions with complex types
    complex_type_examples = []
    for func in functions[:50]:  # Check first 50
        for param in func.parameters:
            if param.type_hint and any(t in param.type_hint for t in ['Union', 'Optional', 'Literal']):
                complex_type_examples.append((func, param))
                if len(complex_type_examples) >= 3:
                    break
        if len(complex_type_examples) >= 3:
            break

    if complex_type_examples:
        print(f"✅ Found {len(complex_type_examples)} parameter(s) with complex types")

        for func, param in complex_type_examples[:3]:
            schema = generator_baseline.generate_tool_schema(func)
            param_schema = schema['inputSchema']['properties'].get(param.name, {})
            print(f"\n   Function: {func.function_name}")
            print(f"   Parameter: {param.name}")
            print(f"   Python type: {param.type_hint}")
            print(f"   JSON Schema type: {param_schema.get('type', 'N/A')}")
    else:
        print("ℹ️  No parameters with complex types found in sample")

    print("\n" + "=" * 80)
    print("✅ Schema generation test complete!")
    print("=" * 80)

    client.close()

if __name__ == "__main__":
    main()
