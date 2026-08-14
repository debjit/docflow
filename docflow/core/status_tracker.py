"""
Status tracker for Work-In-Progress (WIP) branches and active tasks.
"""

import os
import json
from datetime import datetime, timezone
from typing import List, Dict, Any
from git import Repo


class StatusTracker:
    """Tracks active feature branches, WIP tasks, and updates status/wip documentation."""

    def __init__(self, app_repo_path: str, docs_repo_path: str):
        self.app_repo_path = os.path.abspath(app_repo_path)
        self.docs_repo_path = os.path.abspath(docs_repo_path)
        self.app_repo = Repo(self.app_repo_path)

    def scan_wip(self) -> Dict[str, Any]:
        """Scans active local and remote branches in the application repository."""
        branches_data = []

        for branch in self.app_repo.branches:
            if branch.name in ("main", "master", "develop", "HEAD"):
                continue

            last_commit = branch.commit
            commit_time = datetime.fromtimestamp(last_commit.committed_date, tz=timezone.utc).isoformat()

            # Determine feature association from branch name (e.g., feature/auth -> auth)
            parts = branch.name.split("/")
            feature_tag = parts[1] if len(parts) > 1 else parts[0]

            branches_data.append({
                "branch": branch.name,
                "feature": feature_tag,
                "last_commit_sha": last_commit.hexsha[:8],
                "last_commit_message": last_commit.message.strip(),
                "author": str(last_commit.author),
                "last_updated": commit_time,
                "status": "in-progress"
            })

        return {
            "last_scanned": datetime.now(timezone.utc).isoformat(),
            "active_count": len(branches_data),
            "active_branches": branches_data,
        }

    def write_wip_docs(self) -> Dict[str, str]:
        """Generates and writes status/wip.md and status/wip.json to the docs repo."""
        wip_data = self.scan_wip()
        status_dir = os.path.join(self.docs_repo_path, "status")
        os.makedirs(status_dir, exist_ok=True)

        json_path = os.path.join(status_dir, "wip.json")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(wip_data, f, indent=2)

        md_lines = [
            "# Work In Progress (WIP)",
            "",
            f"> Last updated: {wip_data['last_scanned']}",
            "",
            "## Active Branches & Ongoing Work",
            "",
            "| Branch | Feature | Last Commit | Author | Last Updated |",
            "|--------|---------|-------------|--------|--------------|"
        ]

        for b in wip_data["active_branches"]:
            md_lines.append(
                f"| `{b['branch']}` | **{b['feature']}** | {b['last_commit_message']} (`{b['last_commit_sha']}`) | {b['author']} | {b['last_updated']} |"
            )

        if not wip_data["active_branches"]:
            md_lines.append("| *None* | - | No active feature branches detected | - | - |")

        md_path = os.path.join(status_dir, "wip.md")
        with open(md_path, "w", encoding="utf-8") as f:
            f.write("\n".join(md_lines) + "\n")

        return {"json_path": json_path, "md_path": md_path}
