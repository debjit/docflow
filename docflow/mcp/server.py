"""
MCP server implementation for serving documentation to AI agents using mcp.server.MCPServer.
"""

import os
import json
from typing import Optional, Dict, Any, List
from mcp.server import MCPServer


def create_mcp_server(docs_repo_path: Optional[str] = None) -> MCPServer:
    """Creates and configures an MCPServer for the documentation repository."""

    target_docs = os.path.abspath(docs_repo_path or "./docs-repo")
    mcp = MCPServer(name="DocFlow Server", version="0.1.0")

    # --- TOOLS ---

    @mcp.tool()
    def search_docs(query: str) -> List[Dict[str, str]]:
        """
        Performs full-text keyword search across all documentation in the repository.
        """
        results = []
        query_lower = query.lower()

        for root, _, files in os.walk(target_docs):
            for file in files:
                if file.endswith((".md", ".json")) and not file.startswith("."):
                    file_path = os.path.join(root, file)
                    rel_path = os.path.relpath(file_path, target_docs)
                    try:
                        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                            content = f.read()
                            if query_lower in content.lower():
                                idx = content.lower().find(query_lower)
                                start = max(0, idx - 100)
                                end = min(len(content), idx + 200)
                                snippet = content[start:end].strip()
                                results.append({
                                    "file": rel_path,
                                    "snippet": snippet
                                })
                    except Exception:
                        pass
        return results[:10]

    @mcp.tool()
    def get_feature(feature_name: str) -> Dict[str, Any]:
        """
        Retrieves human documentation (index.md) and machine-readable data (context.json) for a specific feature.
        """
        feat_dir = os.path.join(target_docs, "features", feature_name)
        if not os.path.exists(feat_dir):
            return {"error": f"Feature '{feature_name}' not found in documentation repository."}

        index_md = ""
        context_json = {}

        index_path = os.path.join(feat_dir, "index.md")
        if os.path.exists(index_path):
            with open(index_path, "r", encoding="utf-8") as f:
                index_md = f.read()

        context_path = os.path.join(feat_dir, "context.json")
        if os.path.exists(context_path):
            with open(context_path, "r", encoding="utf-8") as f:
                try:
                    context_json = json.load(f)
                except Exception:
                    pass

        return {
            "feature": feature_name,
            "index_md": index_md,
            "context_json": context_json,
        }

    @mcp.tool()
    def list_features() -> List[Dict[str, str]]:
        """
        Lists all features currently documented in the repository.
        """
        feats_dir = os.path.join(target_docs, "features")
        features = []
        if os.path.exists(feats_dir):
            for entry in sorted(os.listdir(feats_dir)):
                fpath = os.path.join(feats_dir, entry)
                if os.path.isdir(fpath):
                    features.append({
                        "name": entry,
                        "path": f"features/{entry}/index.md"
                    })
        return features

    @mcp.tool()
    def get_wip() -> Dict[str, Any]:
        """
        Retrieves current work-in-progress active branches and tasks.
        """
        wip_json_path = os.path.join(target_docs, "status", "wip.json")
        if os.path.exists(wip_json_path):
            with open(wip_json_path, "r", encoding="utf-8") as f:
                try:
                    return json.load(f)
                except Exception:
                    pass
        return {"active_branches": [], "message": "No active WIP status found."}

    @mcp.tool()
    def get_full_context() -> str:
        """
        Retrieves the complete concatenated llms-full.txt context for the repository.
        """
        llms_full_path = os.path.join(target_docs, "llms-full.txt")
        if os.path.exists(llms_full_path):
            with open(llms_full_path, "r", encoding="utf-8") as f:
                return f.read()
        return "No documentation context available."

    # --- RESOURCES ---

    @mcp.resource("docflow://llms.txt")
    def resource_llms_txt() -> str:
        path = os.path.join(target_docs, "llms.txt")
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
        return "No llms.txt found."

    @mcp.resource("docflow://llms-full.txt")
    def resource_llms_full_txt() -> str:
        return get_full_context()

    @mcp.resource("docflow://features/{feature_name}/context")
    def resource_feature_context(feature_name: str) -> str:
        path = os.path.join(target_docs, "features", feature_name, "context.json")
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
        return "{}"

    @mcp.resource("docflow://status/wip")
    def resource_wip() -> str:
        path = os.path.join(target_docs, "status", "wip.md")
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
        return "No active WIP documentation."

    return mcp
