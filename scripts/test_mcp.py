from __future__ import annotations

import asyncio

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main() -> None:
    params = StdioServerParameters(
        command=r"C:\Development\Game_Agent\.venv\Scripts\python.exe",
        args=[r"C:\Development\Game_Agent\ldplayer_mcp\server.py"],
    )

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = await session.list_tools()
            print("tools:", [tool.name for tool in tools.tools])

            result = await session.call_tool(
                "observe_screen",
                {"preview_scale": 0.25, "include_base64": False},
            )
            print("observe_screen:", result.content)


if __name__ == "__main__":
    asyncio.run(main())
