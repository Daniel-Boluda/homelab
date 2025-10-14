"""
Test client for Plant Risk MCP Server (OAuth-protected).

This script:
  1. Authenticates using OAuth.
  2. Lists available tools.
  3. Calls 'health', 'get_user_info', and 'list_plants' endpoints.
  4. Prints compact responses for inspection.

Run with:
    python client_test_mcp.py
"""

import asyncio
from fastmcp.client import Client

SERVER_URL = "https://mcpservers.dbcloud.org/mcp"


async def main():
    try:
        async with Client(SERVER_URL, auth="oauth") as client:
            # --- 1. Ping test ---
            print("🔍 Pinging server...")
            assert await client.ping()
            print("✅ Successfully authenticated and connected!\n")

            # --- 2. List available tools ---
            print("🔧 Listing available tools...")
            tools = await client.list_tools()
            print(f"Found {len(tools)} tools:")
            for t in tools:
                print(f"   - {t.name}")

            # --- 3. Call health endpoint ---
            if any(t.name == "health" for t in tools):
                print("\n🩺 Checking server health...")
                res = await client.call_tool("health")
                print("Health response:", res)

            # --- 4. Get authenticated user info ---
            if any(t.name == "get_user_info" for t in tools):
                print("\n👤 Getting user info...")
                user_info = await client.call_tool("get_user_info")
                print("User info:", user_info)

            # --- 5. Call list_plants endpoint ---
            if any(t.name == "list_plants" for t in tools):
                print("\n🌱 Fetching list of plants...")
                result = await client.call_tool("list_plants")
                plants = result.get("plants", [])
                print(f"✅ Received {len(plants)} plants:")
                for p in plants[:5]:  # print up to 5 plants
                    print(f"   - {p.get('id')} | {p.get('name')} | code={p.get('acs_code')}")
            else:
                print("\n⚠️ No 'list_plants' tool found on server.")

            print("\n🎉 MCP test completed successfully!")

    except Exception as e:
        print(f"❌ MCP test failed: {e}")
        raise


if __name__ == "__main__":
    asyncio.run(main())
