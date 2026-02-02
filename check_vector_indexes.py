#!/usr/bin/env python3
"""Check vector indexes in FalkorDB."""
import os
import sys

# Set up environment
os.environ.setdefault("FALKORDB_HOST", "localhost")
os.environ.setdefault("FALKORDB_PORT", "6379")

from repotoire.graph.factory import create_client

def main():
    print("Connecting to FalkorDB...")
    client = create_client(graph_name="org_repotoire")
    
    print("\n=== Checking Indexes ===")
    try:
        result = client.execute_query("CALL db.indexes()")
        print(f"Found {len(result)} indexes:")
        for row in result:
            print(f"  - {row}")
    except Exception as e:
        print(f"Error querying indexes: {e}")
    
    print("\n=== Checking Vector Indexes Specifically ===")
    for label in ["Function", "Class", "File", "Commit"]:
        try:
            # Try a simple vector query
            result = client.execute_query(f"""
                MATCH (n:{label}) 
                WHERE n.embedding IS NOT NULL 
                WITH n LIMIT 1 
                RETURN '{label}' as type, size(n.embedding) as dims
            """)
            if result:
                dims = result[0].get("dims", "unknown")
                print(f"  ✓ {label}: has embeddings with {dims} dimensions")
            else:
                print(f"  ✗ {label}: no embeddings found")
        except Exception as e:
            print(f"  ✗ {label}: error - {e}")
    
    print("\n=== Testing Vector Search ===")
    try:
        # Create a dummy embedding (just zeros for testing)
        test_embedding = [0.0] * 3072  # Qwen embedding size
        result = client.execute_query("""
            CALL db.idx.vector.queryNodes(
                'Function',
                'embedding',
                5,
                vecf32($embedding)
            ) YIELD node, score
            RETURN node.name as name, score
            LIMIT 5
        """, {"embedding": test_embedding})
        print(f"Vector search returned {len(result)} results:")
        for row in result:
            print(f"  - {row}")
    except Exception as e:
        print(f"Vector search error: {e}")
    
    client.close()
    print("\nDone!")

if __name__ == "__main__":
    main()
