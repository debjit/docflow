"""
MCP server implementation for serving documentation to AI agents using mcp.server.MCPServer.
"""

import os
import json
import re
from typing import Optional, Dict, Any, List, Tuple
from mcp.server import MCPServer

from docflow.core.workspace import SPLIT_DOC_TYPES

SKIP_SEARCH_DIRS = {
    ".git",
    ".venv",
    "venv",
    "prompts",
    ".docflow",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
}

_SECTION_NAME = re.compile(r"^[A-Za-z0-9._-]+$")


def _is_within(root: str, candidate: str) -> bool:
    root_abs = os.path.abspath(root)
    cand_abs = os.path.abspath(candidate)
    return cand_abs == root_abs or cand_abs.startswith(root_abs + os.sep)


def _safe_section_name(name: str) -> Optional[str]:
    cleaned = (name or "").strip().replace("\\", "/")
    if not cleaned or cleaned in (".", "..") or "/" in cleaned:
        return None
    if not _SECTION_NAME.match(cleaned):
        return None
    return cleaned


def _read_section(feat_dir: str, name: str) -> Dict[str, Any]:
    index_md = ""
    context_json: Any = {}
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
        "feature": name,
        "index_md": index_md,
        "context_json": context_json,
    }


def resolve_doc_dir(target_docs: str, feature_name: str) -> Optional[Tuple[str, str]]:
    """Return (absolute dir, repo-relative path) for a documented section."""
    name = _safe_section_name(feature_name)
    if not name:
        return None
    candidates = [
        (os.path.join(target_docs, name), name),
        (os.path.join(target_docs, "features", name), f"features/{name}"),
    ]
    for type_name in sorted(SPLIT_DOC_TYPES):
        candidates.append(
            (os.path.join(target_docs, type_name, name), f"{type_name}/{name}")
        )
    for feat_dir, rel in candidates:
        if not _is_within(target_docs, feat_dir) or not os.path.isdir(feat_dir):
            continue
        if os.path.isfile(os.path.join(feat_dir, "index.md")) or os.path.isfile(
            os.path.join(feat_dir, "context.json")
        ):
            return feat_dir, rel
    return None


def list_documented_sections(target_docs: str) -> List[Dict[str, str]]:
    sections: List[Dict[str, str]] = []
    if not os.path.isdir(target_docs):
        return sections

    def add_dir(path: str, name: str, rel_path: str, doc_type: str) -> None:
        if not os.path.isdir(path):
            return
        if not (
            os.path.isfile(os.path.join(path, "index.md"))
            or os.path.isfile(os.path.join(path, "context.json"))
        ):
            return
        sections.append({"name": name, "path": rel_path, "type": doc_type})

    for entry in sorted(os.listdir(target_docs)):
        if entry.startswith(".") or entry in SKIP_SEARCH_DIRS or entry == "status":
            continue
        fpath = os.path.join(target_docs, entry)
        if not os.path.isdir(fpath):
            continue
        if entry in SPLIT_DOC_TYPES:
            for child in sorted(os.listdir(fpath)):
                if child.startswith("."):
                    continue
                child_path = os.path.join(fpath, child)
                add_dir(child_path, child, f"{entry}/{child}/index.md", entry)
            continue
        add_dir(fpath, entry, f"{entry}/index.md", entry)
    return sections


def create_mcp_server(docs_repo_path: Optional[str] = None) -> MCPServer:
    """Creates and configures an MCPServer for the documentation repository."""

    target_docs = os.path.abspath(docs_repo_path or "./docs-repo")
    mcp = MCPServer(name="DocFlow Server", version="0.1.0")

    @mcp.tool()
    def search_docs(query: str) -> List[Dict[str, str]]:
        """
        Performs full-text keyword search across published documentation.
        """
        results = []
        query_lower = query.lower()

        for root, dirs, files in os.walk(target_docs):
            dirs[:] = [d for d in dirs if d not in SKIP_SEARCH_DIRS and not d.startswith(".")]
            rel_root = os.path.relpath(root, target_docs)
            if rel_root.split(os.sep)[0] in SKIP_SEARCH_DIRS:
                continue
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
        Retrieves human documentation (index.md) and machine-readable data (context.json)
        for a feature or other configured doc type.
        """
        resolved = resolve_doc_dir(target_docs, feature_name)
        if not resolved:
            return {"error": f"Feature '{feature_name}' not found in documentation repository."}
        feat_dir, rel = resolved
        payload = _read_section(feat_dir, feature_name)
        payload["path"] = rel
        return payload

    @mcp.tool()
    def list_features() -> List[Dict[str, str]]:
        """
        Lists documented sections: features/* plus top-level types such as architecture/.
        """
        return list_documented_sections(target_docs)

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
        resolved = resolve_doc_dir(target_docs, feature_name)
        if not resolved:
            return "{}"
        feat_dir, _ = resolved
        path = os.path.join(feat_dir, "context.json")
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
