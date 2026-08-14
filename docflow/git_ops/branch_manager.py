"""
Git branch management for documentation repository updates.
"""

import os
from datetime import datetime, timezone
from git import Repo


class DocBranchManager:
    """Manages branches, commits, and pushes in the documentation repository."""

    def __init__(self, docs_repo_path: str):
        self.docs_repo_path = os.path.abspath(docs_repo_path)
        if not os.path.isdir(self.docs_repo_path):
            raise ValueError(f"Docs repository path does not exist: {self.docs_repo_path}")
        try:
            self.repo = Repo(self.docs_repo_path)
        except Exception:
            # Initialize git repo if not present
            self.repo = Repo.init(self.docs_repo_path)

    def prepare_update_branch(self, feature_name: str, prefix: str = "docs/update") -> str:
        """Creates and checks out a new branch for doc updates."""
        # Ensure repo has an initial commit
        try:
            _ = self.repo.head.commit
        except ValueError:
            # Empty repo - create baseline initial commit
            self.repo.git.add(A=True)
            self.repo.index.commit("docs: initialize repository baseline")

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        branch_name = f"{prefix}-{feature_name}-{timestamp}"

        # Create and checkout new branch
        new_branch = self.repo.create_head(branch_name)
        new_branch.checkout()
        return branch_name

    def commit_and_push(self, commit_message: str, remote_name: str = "origin") -> str:
        """Stages all changes in docs repo, commits, and pushes to remote if configured."""
        self.repo.git.add(A=True)
        if not self.repo.is_dirty(untracked_files=True):
            return "No changes to commit."

        commit = self.repo.index.commit(commit_message)
        current_branch = self.repo.active_branch.name

        try:
            if remote_name in self.repo.remotes:
                self.repo.git.push("--set-upstream", remote_name, current_branch)
        except Exception as e:
            print(f"[Warning] Git push failed (remote may not be configured): {e}")

        return commit.hexsha
