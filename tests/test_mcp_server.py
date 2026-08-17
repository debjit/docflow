"""
Tests for MCPServer implementation.
"""

import os
import json
import tempfile
import pytest
from docflow.mcp.server import create_mcp_server


@pytest.mark.asyncio
async def test_mcp_server_creation_and_tools():
    with tempfile.TemporaryDirectory() as docs_dir:
        # Create a sample feature doc
        auth_dir = os.path.join(docs_dir, "features", "authentication")
        os.makedirs(auth_dir, exist_ok=True)
        with open(os.path.join(auth_dir, "index.md"), "w") as f:
            f.write("# Authentication\n\nOAuth2 token verification.")
        with open(os.path.join(auth_dir, "context.json"), "w") as f:
            json.dump({"feature": "authentication", "status": "stable"}, f)

        server = create_mcp_server(docs_dir)

        assert server.name == "DocFlow Server"

        # List tools
        tools = await server.list_tools()
        tool_names = [t.name for t in tools]
        assert "search_docs" in tool_names
        assert "get_feature" in tool_names
        assert "list_features" in tool_names

        # Call search_docs tool
        search_res = await server.call_tool("search_docs", {"query": "OAuth2"})
        assert search_res.is_error is False
        assert "features/authentication/index.md" in str(search_res.content)

        # Call get_feature tool
        feat_res = await server.call_tool("get_feature", {"feature_name": "authentication"})
        assert feat_res.is_error is False
        assert "OAuth2 token verification" in str(feat_res.content)

        list_res = await server.call_tool("list_features", {})
        assert "authentication" in str(list_res.content)


@pytest.mark.asyncio
async def test_mcp_lists_all_doc_types_and_rejects_traversal():
    with tempfile.TemporaryDirectory() as docs_dir:
        arch = os.path.join(docs_dir, "architecture")
        os.makedirs(arch, exist_ok=True)
        with open(os.path.join(arch, "index.md"), "w") as f:
            f.write("# Architecture\n")
        feat = os.path.join(docs_dir, "features", "auth")
        os.makedirs(feat, exist_ok=True)
        with open(os.path.join(feat, "index.md"), "w") as f:
            f.write("# Auth\n")
        prompts = os.path.join(docs_dir, "prompts", "pending")
        os.makedirs(prompts, exist_ok=True)
        with open(os.path.join(prompts, "secret.md"), "w") as f:
            f.write("PROMPT_SECRET_TOKEN\n")

        server = create_mcp_server(docs_dir)
        listed = await server.call_tool("list_features", {})
        body = str(listed.content)
        assert "architecture" in body
        assert "auth" in body

        arch_res = await server.call_tool("get_feature", {"feature_name": "architecture"})
        assert "Architecture" in str(arch_res.content)

        traverse = await server.call_tool("get_feature", {"feature_name": "../prompts"})
        assert "not found" in str(traverse.content).lower() or "error" in str(traverse.content).lower()

        search = await server.call_tool("search_docs", {"query": "PROMPT_SECRET_TOKEN"})
        assert "PROMPT_SECRET_TOKEN" not in str(search.content)
