"""
Pydantic data models for DocFlow.
"""

from datetime import datetime, timezone
from typing import Literal, Optional, List, Dict, Any
from pydantic import BaseModel, Field


class FileChange(BaseModel):
    """Represents a single changed file in a git diff."""
    path: str
    old_path: Optional[str] = None
    change_type: Literal["added", "modified", "deleted", "renamed"]
    language: Optional[str] = None
    diff_summary: str = ""
    full_diff: str = ""
    added_lines: int = 0
    removed_lines: int = 0


class BranchInfo(BaseModel):
    """Metadata about git branches involved in a merge or commit."""
    source_branch: str
    target_branch: str = "main"
    author_name: Optional[str] = None
    author_email: Optional[str] = None
    commit_sha: Optional[str] = None


class ChangeManifest(BaseModel):
    """Manifest of all code changes extracted from a git commit range or merge."""
    repo_path: str
    base_ref: str
    head_ref: str
    merge_description: str = ""
    changed_files: List[FileChange] = Field(default_factory=list)
    commit_messages: List[str] = Field(default_factory=list)
    branch_info: Optional[BranchInfo] = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def total_additions(self) -> int:
        return sum(f.added_lines for f in self.changed_files)

    @property
    def total_deletions(self) -> int:
        return sum(f.removed_lines for f in self.changed_files)


class FeatureChunk(BaseModel):
    """Represents a chunk of a repository corresponding to a logical feature/module for init."""
    feature_name: str
    description: str = ""
    file_paths: List[str] = Field(default_factory=list)
    sample_snippets: Dict[str, str] = Field(default_factory=dict)


class PromptContext(BaseModel):
    """Data payload passed into prompt templates."""
    task_type: Literal["init", "update", "full-regen", "stack-survey"]
    project_name: str
    feature_name: str
    app_repo_path: Optional[str] = None
    docs_repo_path: Optional[str] = None
    change_manifest: Optional[ChangeManifest] = None
    feature_chunk: Optional[FeatureChunk] = None
    existing_index_md: Optional[str] = None
    existing_context_json: Optional[str] = None
    conventions_text: str = ""
    extra_instructions: Optional[str] = None
    doc_type: str = "features"
    doc_type_description: str = ""
    output_dir: str = ""
    available_sections: List[Dict[str, str]] = Field(default_factory=list)


class AgentRunResult(BaseModel):
    """Result of an agent execution."""
    success: bool
    mode: Literal["shell", "manual"]
    prompt_file_path: str
    output_log: str = ""
    error_message: Optional[str] = None
