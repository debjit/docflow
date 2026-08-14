"""
Generator for llms.txt and llms-full.txt files adhering to the standard specification.
"""

import os
from pathlib import Path
from typing import Dict, List


class LLMSTxtGenerator:
    """Generates llms.txt and llms-full.txt files in the documentation repository."""

    def __init__(self, docs_repo_path: str):
        self.docs_repo_path = os.path.abspath(docs_repo_path)

    def generate(self, project_name: str = "Project") -> Dict[str, str]:
        """Generates llms.txt and llms-full.txt at the root of the docs repository."""
        features_dir = os.path.join(self.docs_repo_path, "features")

        feature_items: List[Dict[str, str]] = []
        concatenated_md: List[str] = [f"# {project_name} — Complete Documentation", ""]

        if os.path.exists(features_dir):
            for entry in sorted(os.listdir(features_dir)):
                feat_path = os.path.join(features_dir, entry)
                if os.path.isdir(feat_path):
                    index_path = os.path.join(feat_path, "index.md")
                    if os.path.exists(index_path):
                        description = f"Documentation for feature '{entry}'."
                        feature_items.append({
                            "name": entry,
                            "path": f"features/{entry}/index.md",
                            "description": description
                        })
                        with open(index_path, "r", encoding="utf-8") as f:
                            content = f.read()
                            concatenated_md.append(f"<!-- BEGIN FEATURE: {entry} -->")
                            concatenated_md.append(content)
                            concatenated_md.append(f"<!-- END FEATURE: {entry} -->\n")

        # Build llms.txt content
        llms_txt_lines = [
            f"# {project_name}",
            "",
            f"> Machine-readable index of documentation for {project_name}.",
            "",
            "## Features"
        ]

        for item in feature_items:
            llms_txt_lines.append(f"- [{item['name'].capitalize()}]({item['path']}): {item['description']}")

        if not feature_items:
            llms_txt_lines.append("- [Documentation](index.md): Main documentation.")

        llms_txt_lines.extend([
            "",
            "## Status",
            "- [Work in Progress](status/wip.md): Active development branches and tasks.",
            "",
            "## Conventions",
            "- [Conventions](CONVENTIONS.md): Documentation format standards."
        ])

        llms_txt_path = os.path.join(self.docs_repo_path, "llms.txt")
        with open(llms_txt_path, "w", encoding="utf-8") as f:
            f.write("\n".join(llms_txt_lines) + "\n")

        llms_full_txt_path = os.path.join(self.docs_repo_path, "llms-full.txt")
        with open(llms_full_txt_path, "w", encoding="utf-8") as f:
            f.write("\n".join(concatenated_md) + "\n")

        return {"llms_txt": llms_txt_path, "llms_full_txt": llms_full_txt_path}
