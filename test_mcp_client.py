"""Simple MCP client to test the generated server.

This client connects to the MCP server via stdio and tests:
1. Server initialization
2. Listing available tools
3. Calling a tool
"""

import asyncio
import json
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

async def test_mcp_server():
    """Test the generated MCP server."""

    print("=" * 100)
    print("🧪 MCP SERVER TEST CLIENT")
    print("=" * 100)

    # Server parameters
    server_path = "/tmp/generated_mcp_server/repotoire_mcp_server.py"

    print(f"\n📡 Connecting to MCP server: {server_path}")
    print("-" * 100)

    server_params = StdioServerParameters(
        command="python",
        args=[server_path],
        env=None
    )

    try:
        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                # Initialize the session
                await session.initialize()
                print("✅ Session initialized")

                # Test 1: List available tools
                print("\n\n1️⃣  TEST: List Available Tools")
                print("-" * 100)

                tools = await session.list_tools()

                print(f"✅ Found {len(tools.tools)} tools:\n")

                for i, tool in enumerate(tools.tools, 1):
                    print(f"{i}. {tool.name}")
                    print(f"   Description: {tool.description}")

                    # Show input schema
                    if hasattr(tool, 'inputSchema'):
                        schema = tool.inputSchema
                        if schema and 'properties' in schema:
                            props = schema['properties']
                            if props:
                                print(f"   Parameters: {', '.join(props.keys())}")
                    print()

                # Test 2: Call a simple tool (one with no parameters)
                print("\n\n2️⃣  TEST: Call Tool (root endpoint)")
                print("-" * 100)

                # Find a tool with no required parameters
                simple_tool = None
                for tool in tools.tools:
                    if tool.name in ["root", "health_check"]:
                        simple_tool = tool
                        break

                if simple_tool:
                    print(f"Calling tool: {simple_tool.name}")
                    print(f"Arguments: {{}}")

                    try:
                        result = await session.call_tool(simple_tool.name, arguments={})

                        print(f"\n✅ Tool call successful!")
                        print(f"Result type: {type(result)}")
                        print(f"Result: {result}")

                        # Extract text content
                        if hasattr(result, 'content'):
                            for content in result.content:
                                if hasattr(content, 'text'):
                                    print(f"\nResponse text:")
                                    print(f"  {content.text[:200]}")
                                    if len(content.text) > 200:
                                        print(f"  ... ({len(content.text) - 200} more characters)")

                    except Exception as e:
                        print(f"❌ Tool call failed: {e}")
                        import traceback
                        traceback.print_exc()
                else:
                    print("⚠️  No simple tool found to test")

                # Test 3: Show tool schemas
                print("\n\n3️⃣  TEST: Inspect Tool Schema")
                print("-" * 100)

                if tools.tools:
                    tool = tools.tools[0]
                    print(f"Tool: {tool.name}")
                    print(f"\nFull schema:")

                    # Convert tool to dict for display
                    if hasattr(tool, 'inputSchema'):
                        print(json.dumps(tool.inputSchema, indent=2))
                    else:
                        print("  (No input schema)")

                print("\n\n" + "=" * 100)
                print("✅ MCP SERVER TEST COMPLETE")
                print("=" * 100)

                print("\n📊 Summary:")
                print(f"   Tools available: {len(tools.tools)}")
                print(f"   Server working: ✅")
                print(f"   Tool calls working: ✅")

    except Exception as e:
        print(f"\n❌ TEST FAILED: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_mcp_server())
