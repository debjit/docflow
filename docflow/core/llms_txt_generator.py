"""
Generator for llms.txt and llms-full.txt files adhering to the standard specification.
"""

import os
from typing import Dict, List


SKIP_DIRS = {
    ".git",
    ".venv",
    "venv",
    "prompts",
    "status",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
}


class LLMSTxtGenerator:
    """Generates llms.txt and llms-full.txt files in the documentation repository."""

    def __init__(self, docs_repo_path: str):
        self.docs_repo_path = os.path.abspath(docs_repo_path)

    def _collect_sections(self) -> List[Dict[str, str]]:
        items: List[Dict[str, str]] = []
        root = self.docs_repo_path
        if not os.path.isdir(root):
            return items

        def add_dir(path: str, name: str, rel_path: str, doc_type: str) -> None:
            index_path = os.path.join(path, "index.md")
            if not os.path.isdir(path) or not os.path.exists(index_path):
                return
            items.append({
                "name": name,
                "path": rel_path,
                "description": f"Documentation for {doc_type} '{name}'.",
                "type": doc_type,
            })

        for entry in sorted(os.listdir(root)):
            if entry.startswith(".") or entry in SKIP_DIRS:
                continue
            fpath = os.path.join(root, entry)
            if not os.path.isdir(fpath):
                continue
            if entry == "features":
                for child in sorted(os.listdir(fpath)):
                    child_path = os.path.join(fpath, child)
                    add_dir(child_path, child, f"features/{child}/index.md", "features")
                continue
            add_dir(fpath, entry, f"{entry}/index.md", entry)
        return items

    def generate(self, project_name: str = "Project") -> Dict[str, str]:
        """Generates llms.txt and llms-full.txt at the root of the docs repository."""
        sections = self._collect_sections()
        concatenated_md: List[str] = [f"# {project_name} — Complete Documentation", ""]

        for item in sections:
            index_path = os.path.join(self.docs_repo_path, item["path"])
            if os.path.exists(index_path):
                with open(index_path, "r", encoding="utf-8") as f:
                    content = f.read()
                label = "FEATURE" if item["type"] == "features" else item["type"].upper()
                concatenated_md.append(f"<!-- BEGIN {label}: {item['name']} -->")
                concatenated_md.append(content)
                concatenated_md.append(f"<!-- END {label}: {item['name']} -->\n")

        llms_txt_lines = [
            f"# {project_name}",
            "",
            f"> Machine-readable index of documentation for {project_name}.",
            "",
        ]

        grouped: Dict[str, List[Dict[str, str]]] = {}
        for item in sections:
            grouped.setdefault(item["type"], []).append(item)

        type_order = list(grouped.keys())
        if "features" in type_order:
            type_order = ["features"] + [k for k in type_order if k != "features"]

        for doc_type in type_order:
            heading = "Features" if doc_type == "features" else doc_type.replace("-", " ").title()
            llms_txt_lines.append(f"## {heading}")
            for item in grouped[doc_type]:
                llms_txt_lines.append(
                    f"- [{item['name'].capitalize()}]({item['path']}): {item['description']}"
                )
            llms_txt_lines.append("")

        if not sections:
            llms_txt_lines.extend([
                "## Features",
                "- [Documentation](index.md): Main documentation.",
                "",
            ])

        llms_txt_lines.extend([
            "## Status",
            "- [Work in Progress](status/wip.md): Active development branches and tasks.",
            "",
            "## Conventions",
            "- [Conventions](CONVENTIONS.md): Documentation format standards.",
        ])

        llms_txt_path = os.path.join(self.docs_repo_path, "llms.txt")
        with open(llms_txt_path, "w", encoding="utf-8") as f:
            f.write("\n".join(llms_txt_lines) + "\n")

        llms_full_txt_path = os.path.join(self.docs_repo_path, "llms-full.txt")
        with open(llms_full_txt_path, "w", encoding="utf-8") as f:
            f.write("\n".join(concatenated_md) + "\n")

        return {"llms_txt": llms_txt_path, "llms_full_txt": llms_full_txt_path}
