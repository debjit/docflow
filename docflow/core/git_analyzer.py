"""
Git repository analyzer for extracting change manifests and feature chunks.
"""

import fnmatch
import os
from pathlib import Path
from typing import Callable, List, Dict, Optional, Set
from git import Repo
from unidiff import PatchSet

from docflow.core.models import ChangeManifest, FileChange, BranchInfo, FeatureChunk

# File extensions mapped to language names
LANGUAGE_MAP = {
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
    ".jsx": "react",
    ".tsx": "react-typescript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".kt": "kotlin",
    ".cpp": "cpp",
    ".c": "c",
    ".h": "c",
    ".hpp": "cpp",
    ".sh": "bash",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".json": "json",
    ".md": "markdown",
    ".sql": "sql",
    ".html": "html",
    ".css": "css",
    ".php": "php",
    ".vue": "vue",
}

DEFAULT_IGNORE = {
    ".git", ".github", ".gitlab", ".gitignore", ".gitattributes",
    ".gitlab-ci.yml", "gitlab-ci.yml",
    "node_modules", "dist", "build", "__pycache__",
    ".venv", "venv", ".idea", ".vscode", "*.lock", "package-lock.json",
    ".pytest_cache", "test-docs-repo", "docs-repo", "*.egg-info", "docflow.egg-info",
    ".docflow",
}

_WRAPPER_DIRS = {"src", "lib", "app", "pkg"}


def posix_rel(rel_path: str) -> str:
    """Normalize a relative path without stripping leading dots from names like .github."""
    posix = Path(str(rel_path).replace("\\", "/")).as_posix()
    while posix.startswith("./"):
        posix = posix[2:]
    return posix.lstrip("/")


def path_is_ignored(rel_path: str, ignore_patterns: Set[str]) -> bool:
    """True if a relative path matches any ignore glob (files or directories)."""
    posix = posix_rel(rel_path)
    if not posix or posix == ".":
        return False
    parts = Path(posix).parts
    basename = parts[-1] if parts else posix
    for raw in ignore_patterns:
        pattern = (raw or "").strip()
        if not pattern:
            continue
        dir_pat = pattern.rstrip("/")
        if "/" in dir_pat:
            if posix == dir_pat or posix.startswith(f"{dir_pat}/"):
                return True
        if any(fnmatch.fnmatch(part, dir_pat) for part in parts):
            return True
        if fnmatch.fnmatch(posix, pattern) or fnmatch.fnmatch(posix, dir_pat):
            return True
        if fnmatch.fnmatch(basename, pattern) or fnmatch.fnmatch(basename, dir_pat):
            return True
    return False


def feature_bucket_for_path(path: str, skip_as_feature: Optional[Set[str]] = None) -> Optional[str]:
    """Map a source path to a feature/section name (same rules as scan_features)."""
    posix = posix_rel(path)
    parts = Path(posix).parts
    dir_parts = parts[:-1] if len(parts) > 1 else ()
    if not dir_parts:
        return "core"
    first = dir_parts[0]
    if first.startswith("."):
        return "config"
    if first in _WRAPPER_DIRS:
        name = dir_parts[1] if len(dir_parts) > 1 else "core"
    else:
        name = first
    if name.startswith("."):
        return "config"
    skip = skip_as_feature or set()
    if name in skip or first in skip:
        return None
    return name


class GitAnalyzer:
    """Analyzes git repositories to generate change manifests and feature chunkings."""

    def __init__(self, repo_path: str):
        self.repo_path = os.path.abspath(repo_path)
        if not os.path.isdir(self.repo_path):
            raise ValueError(f"Repository path does not exist: {self.repo_path}")
        try:
            self.repo = Repo(self.repo_path)
        except Exception as e:
            raise ValueError(f"Failed to open git repository at {self.repo_path}: {e}")

    def _detect_language(self, path: str) -> Optional[str]:
        ext = Path(path).suffix.lower()
        return LANGUAGE_MAP.get(ext)

    def extract_diff(
        self,
        base_ref: str = "HEAD~1",
        head_ref: str = "HEAD",
        full_diff_threshold: int = 200,
        ignore_patterns: Optional[Set[str]] = None,
    ) -> ChangeManifest:
        """
        Extracts a ChangeManifest representing differences between base_ref and head_ref.
        """
        ignore = ignore_patterns or DEFAULT_IGNORE
        try:
            if base_ref == head_ref:
                raw_diff = self.repo.git.diff("4b825dc642cb6eb9a060e54bf8d69288fbee4904", head_ref)
            else:
                raw_diff = self.repo.git.diff(base_ref, head_ref)
        except Exception:
            # Fallback to initial commit or empty tree diff if base_ref (e.g. HEAD~1) is invalid
            try:
                # 4b825dc642cb6eb9a060e54bf8d69288fbee4904 is Git's magic empty tree SHA
                raw_diff = self.repo.git.diff("4b825dc642cb6eb9a060e54bf8d69288fbee4904", head_ref)
            except Exception:
                raw_diff = ""
        patch = PatchSet(raw_diff)

        file_changes: List[FileChange] = []
        for patched_file in patch:
            path = patched_file.path
            if path_is_ignored(path, ignore):
                continue

            change_type = "modified"
            if patched_file.is_added_file:
                change_type = "added"
            elif patched_file.is_removed_file:
                change_type = "deleted"
            elif patched_file.is_rename:
                change_type = "renamed"

            added_lines = patched_file.added
            removed_lines = patched_file.removed
            full_diff_str = str(patched_file)

            # Condense diff if it exceeds threshold
            if (added_lines + removed_lines) > full_diff_threshold:
                # Create a condensed diff summary (hunk headers + signatures)
                summary_lines = []
                for hunk in patched_file:
                    summary_lines.append(f"@@ -{hunk.source_start},{hunk.source_length} +{hunk.target_start},{hunk.target_length} @@")
                    for line in hunk:
                        # Include additions and signatures
                        if line.is_added or line.is_removed:
                            val = str(line.value).strip()
                            if val.startswith("def ") or val.startswith("class ") or val.startswith("export ") or val.startswith("func "):
                                prefix = "+" if line.is_added else "-"
                                summary_lines.append(f"{prefix} {val}")
                diff_summary = "\n".join(summary_lines[:50])
            else:
                diff_summary = full_diff_str

            file_changes.append(
                FileChange(
                    path=path,
                    old_path=patched_file.source_file if patched_file.is_rename else None,
                    change_type=change_type,
                    language=self._detect_language(path),
                    diff_summary=diff_summary,
                    full_diff=full_diff_str,
                    added_lines=added_lines,
                    removed_lines=removed_lines,
                )
            )

        # Collect commit messages
        commit_messages = []
        try:
            commits = list(self.repo.iter_commits(f"{base_ref}..{head_ref}"))
            commit_messages = [c.message.strip() for c in commits]
        except Exception:
            commit_messages = [f"Changes from {base_ref} to {head_ref}"]

        # Extract branch info if available
        branch_info = BranchInfo(
            source_branch=head_ref,
            target_branch=base_ref,
        )

        return ChangeManifest(
            repo_path=self.repo_path,
            base_ref=base_ref,
            head_ref=head_ref,
            merge_description="\n".join(commit_messages[:5]),
            changed_files=file_changes,
            commit_messages=commit_messages,
            branch_info=branch_info,
        )

    def list_commits(self, max_count: int = 15, rev: str = "HEAD") -> List[Dict[str, str]]:
        """Recent commits on rev (HEAD or a branch), newest first."""
        commits = []
        try:
            for commit in self.repo.iter_commits(rev or "HEAD", max_count=max_count):
                commits.append({
                    "sha": commit.hexsha,
                    "short_sha": commit.hexsha[:8],
                    "message": commit.message.strip().splitlines()[0],
                    "author": str(commit.author),
                })
        except Exception:
            return []
        return commits

    def list_branches(self) -> List[str]:
        names = [head.name for head in self.repo.heads]
        seen = {name.lower() for name in names}
        try:
            for remote in self.repo.remotes:
                for ref in remote.refs:
                    try:
                        short = ref.remote_head
                    except Exception:
                        continue
                    if not short or short == "HEAD":
                        continue
                    if short.lower() in seen:
                        continue
                    names.append(short)
                    seen.add(short.lower())
        except Exception:
            pass
        try:
            current = self.repo.active_branch.name
            names.sort(key=lambda name: (name != current, name.lower()))
        except Exception:
            names.sort(key=str.lower)
        return names

    def list_tree_paths(self, rev: str = "HEAD") -> List[str]:
        """File paths in the commit tree for rev, without checking out."""
        try:
            commit = self.repo.commit(rev or "HEAD")
        except Exception:
            return []
        paths: List[str] = []
        try:
            for item in commit.tree.traverse():
                if getattr(item, "type", None) == "blob":
                    paths.append(posix_rel(item.path))
        except Exception:
            return []
        return paths

    def is_ancestor(self, maybe_ancestor: str, rev: str = "HEAD") -> bool:
        if not maybe_ancestor:
            return False
        try:
            return bool(self.repo.is_ancestor(maybe_ancestor, rev or "HEAD"))
        except Exception:
            return False

    def head_commit(self, rev: str = "HEAD") -> Optional[Dict[str, str]]:
        rows = self.list_commits(max_count=1, rev=rev)
        return rows[0] if rows else None

    def commits_between(self, base_ref: str, head_ref: str) -> List[Dict[str, str]]:
        """Commits reachable from head_ref but not base_ref (newest first)."""
        commits = []
        try:
            iterable = self.repo.iter_commits(f"{base_ref}..{head_ref}")
            for commit in iterable:
                commits.append({
                    "sha": commit.hexsha,
                    "short_sha": commit.hexsha[:8],
                    "message": commit.message.strip().splitlines()[0],
                    "author": str(commit.author),
                })
        except Exception:
            return self.list_commits(max_count=1)
        return commits

    def scan_features(
        self,
        ignore_patterns: Optional[Set[str]] = None,
        include_architecture: bool = True,
        skip_as_feature: Optional[Set[str]] = None,
        architecture_seed_paths: Optional[List[str]] = None,
        on_progress: Optional[Callable[[str], None]] = None,
        progress_every: int = 50,
    ) -> List[FeatureChunk]:
        """
        Scans the repository structure and groups source files into logical feature chunks for init.
        """
        ignore = ignore_patterns or DEFAULT_IGNORE
        skip = skip_as_feature or set()
        feature_map: Dict[str, List[str]] = {}
        scanned = 0
        every = max(1, int(progress_every) if progress_every else 50)

        for root, dirs, files in os.walk(self.repo_path):
            rel_root = os.path.relpath(root, self.repo_path)
            dirs[:] = [
                d for d in dirs
                if not path_is_ignored(os.path.normpath(os.path.join(rel_root, d)), ignore)
            ]

            for file in files:
                rel_file_path = os.path.normpath(os.path.join(rel_root, file))
                if path_is_ignored(rel_file_path, ignore):
                    continue

                scanned += 1
                if on_progress and scanned % every == 0:
                    on_progress(f"Scanning app repo… {scanned} files")

                feature_name = feature_bucket_for_path(rel_file_path, skip_as_feature=skip)
                if not feature_name:
                    continue
                feature_map.setdefault(feature_name, []).append(rel_file_path)

        if on_progress and scanned and scanned % every != 0:
            on_progress(f"Scanning app repo… {scanned} files")

        # Convert feature map to FeatureChunk models
        chunks = []

        # 1. First: Infrastructure & Architecture chunk
        infra_files = [f for f in feature_map.get("core", []) if any(k in f.lower() for k in ("docker", "compose", "k8s", "deploy", "config", "env", "helm", "terraform", "server", "main", "app"))]
        if not infra_files:
            infra_files = feature_map.get("core", [])[:5]

        if include_architecture:
            seed_paths = list(architecture_seed_paths or [])
            if not seed_paths:
                seed_paths = infra_files or ["pyproject.toml", "Dockerfile", "docker-compose.yml"]
            chunks.append(
                FeatureChunk(
                    feature_name="architecture",
                    description="System architecture, hosting environment (Dev/Staging/Prod), frameworks, and core dependencies.",
                    file_paths=seed_paths,
                    sample_snippets={}
                )
            )

        for feature, files in feature_map.items():
            if feature == "architecture":
                continue
            chunks.append(
                FeatureChunk(
                    feature_name=feature,
                    description=f"Feature module for '{feature}' containing {len(files)} file(s).",
                    file_paths=files,
                    sample_snippets=self._snippets_for(files),
                )
            )

        return chunks

    def _snippets_for(self, files: List[str], limit: int = 5, rev: Optional[str] = None) -> Dict[str, str]:
        snippets: Dict[str, str] = {}
        for fpath in files[:limit]:
            text = ""
            if rev:
                try:
                    text = self.repo.git.show(f"{rev}:{fpath}")
                except Exception:
                    text = ""
            if not text:
                full_p = os.path.join(self.repo_path, fpath)
                try:
                    with open(full_p, "r", encoding="utf-8", errors="ignore") as f:
                        text = "".join(f.readline() for _ in range(20))
                except Exception:
                    text = ""
            if text:
                lines = text.splitlines()[:20]
                snippets[fpath] = "\n".join(lines) + ("\n" if lines else "")
        return snippets

    def chunk_from_entry(
        self,
        raw: str,
        ignore_patterns: Optional[Set[str]] = None,
        skip_as_feature: Optional[Set[str]] = None,
    ) -> FeatureChunk:
        """Build a feature chunk from a user-supplied module name or relative path."""
        ignore = ignore_patterns or DEFAULT_IGNORE
        skip = skip_as_feature or set()
        rel = posix_rel(raw.strip())
        full = os.path.join(self.repo_path, rel)
        files: List[str] = []
        if os.path.isdir(full):
            for root, dirs, fnames in os.walk(full):
                rel_root = os.path.relpath(root, self.repo_path)
                dirs[:] = [
                    d for d in dirs
                    if not path_is_ignored(os.path.normpath(os.path.join(rel_root, d)), ignore)
                ]
                for fname in fnames:
                    rel_file = os.path.normpath(os.path.join(rel_root, fname))
                    if path_is_ignored(rel_file, ignore):
                        continue
                    files.append(rel_file.replace("\\", "/"))
            name = feature_bucket_for_path(os.path.join(rel, "_"), skip_as_feature=skip)
            if not name:
                name = Path(rel).name or "extra"
        elif os.path.isfile(full):
            files = [rel]
            name = feature_bucket_for_path(rel, skip_as_feature=skip) or Path(rel).stem
        else:
            name = rel.replace("\\", "/").strip("/").split("/")[-1] or "extra"
        name = name or "extra"
        return FeatureChunk(
            feature_name=name,
            description=f"Feature module for '{name}' containing {len(files)} file(s).",
            file_paths=files,
            sample_snippets=self._snippets_for(files),
        )

    def find_existing_docs(self) -> Dict[str, str]:
        """Finds pre-existing documentation files in the repository (e.g. README.md, docs/, etc.)."""
        existing_docs = {}
        for root, _, files in os.walk(self.repo_path):
            rel_root = os.path.relpath(root, self.repo_path)
            if rel_root.startswith(".git") or rel_root.startswith(".venv") or "node_modules" in rel_root:
                continue
            for file in files:
                if file.endswith(".md") and not file.startswith("."):
                    rel_path = os.path.normpath(os.path.join(rel_root, file))
                    full_p = os.path.join(self.repo_path, rel_path)
                    try:
                        with open(full_p, "r", encoding="utf-8", errors="ignore") as f:
                            existing_docs[rel_path] = f.read()
                    except Exception:
                        pass
        return existing_docs

