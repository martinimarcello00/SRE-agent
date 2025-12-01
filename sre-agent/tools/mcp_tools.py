"""MCP tools setup and configuration."""
import asyncio
from langchain_mcp_adapters.client import MultiServerMCPClient
from config import MCP_CONFIG, TOOLS_ALLOWED


async def get_mcp_tools(mcp_client: MultiServerMCPClient) -> list:
    """Get and filter MCP tools based on allowed list.
    
    Args:
        mcp_client: Initialized MCP client
        
    Returns:
        List of filtered tool objects
    """
    mcp_tools = await mcp_client.get_tools()
    
    tools = []
    for tool in mcp_tools:
        if tool.name in TOOLS_ALLOWED:
            tools.append(tool)
    
    return tools


_mcp_client = MultiServerMCPClient(MCP_CONFIG)
TOOLS = asyncio.run(get_mcp_tools(_mcp_client))