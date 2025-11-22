"""Debug RAG retrieval to understand what it's finding."""

import os
from repotoire.graph import Neo4jClient
from repotoire.ai.embeddings import CodeEmbedder
from repotoire.ai.retrieval import GraphRAGRetriever
from repotoire.mcp import PatternDetector

# Connect to Neo4j
password = os.getenv("REPOTOIRE_NEO4J_PASSWORD", "falkor-password")
client = Neo4jClient(uri="bolt://localhost:7688", password=password)

# Create embedder and retriever
embedder = CodeEmbedder()
retriever = GraphRAGRetriever(neo4j_client=client, embedder=embedder)

# Detect a function to test with
detector = PatternDetector(client)
functions = detector.detect_public_functions(min_params=1, max_params=3)

if not functions:
    print("No functions found")
    exit(1)

# Get a function with a decent docstring
test_func = None
for func in functions[:20]:
    if func.docstring and len(func.docstring) > 50:
        test_func = func
        break

if not test_func:
    print("No suitable function found")
    exit(1)

print("=" * 80)
print(f"Testing RAG Retrieval for: {test_func.function_name}")
print("=" * 80)

print(f"\nOriginal docstring:")
print(f"  {test_func.docstring[:200]}...")

print(f"\nParameters:")
for param in test_func.parameters:
    print(f"  - {param.name}: {param.type_hint}")

# Test different query styles
queries = [
    f"What does {test_func.function_name} do? Explain its purpose.",
    f"{test_func.function_name} function purpose",
    f"function {test_func.function_name}",
    f"{test_func.qualified_name}",
]

for i, query in enumerate(queries, 1):
    print(f"\n{'='*80}")
    print(f"Query {i}: {query}")
    print("=" * 80)

    results = retriever.retrieve(
        query=query,
        top_k=3,
        entity_types=["Function"],
        include_related=True
    )

    if not results:
        print("  ❌ No results found")
        continue

    print(f"  ✅ Found {len(results)} results:\n")

    for j, result in enumerate(results, 1):
        print(f"  Result {j}:")
        print(f"    Name: {result.qualified_name}")
        print(f"    Type: {result.entity_type}")
        print(f"    Similarity: {result.similarity_score:.3f}")

        if result.docstring:
            doc_preview = result.docstring[:150].replace('\n', ' ')
            print(f"    Docstring: {doc_preview}...")
        else:
            print(f"    Docstring: None")

        if result.code:
            code_preview = result.code[:100].replace('\n', ' ')
            print(f"    Code: {code_preview}...")

        print()

# Test parameter-specific query
if test_func.parameters:
    param = test_func.parameters[0]
    print(f"\n{'='*80}")
    print(f"Parameter Query: {param.name} in {test_func.function_name}")
    print("=" * 80)

    param_queries = [
        f"What is {param.name} parameter in {test_func.function_name} function? How is it used?",
        f"{test_func.function_name} {param.name} parameter",
        f"parameter {param.name}",
    ]

    for query in param_queries:
        print(f"\n  Query: {query}")
        results = retriever.retrieve(
            query=query,
            top_k=3,
            entity_types=["Function"],
            include_related=True
        )

        if results:
            print(f"  ✅ Found {len(results)} results")
            for j, result in enumerate(results[:1], 1):  # Just show top result
                print(f"    Top result: {result.qualified_name} (similarity: {result.similarity_score:.3f})")
        else:
            print("  ❌ No results")

client.close()

print("\n" + "=" * 80)
print("RAG Debug Complete")
print("=" * 80)
