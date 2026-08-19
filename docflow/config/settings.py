"""
Settings model and loader for .docflow.yml and environment variable overrides.
"""

import os
from typing import Optional, List
import yaml
from pydantic import BaseModel, Field, ConfigDict


class ProjectSettings(BaseModel):
    name: str = "Project"
    description: str = ""


class AppSettings(BaseModel):
    repo_path: str = ""


class DocTypeSettings(BaseModel):
    name: str
    description: str = ""


class DocsSettings(BaseModel):
    repo_path: str = ""
    types: List[DocTypeSettings] = Field(default_factory=list)


class AgentSettings(BaseModel):
    mode: str = "manual"  # shell | manual
    command: str = 'agy --dangerously-skip-permissions --add-dir {docs_repo} -p "Follow every instruction in {prompt_file}."'


class PlatformSettings(BaseModel):
    type: str = "github"  # github | gitlab | bitbucket | generic
    auto_mr: bool = True
    notify_branch_owner: bool = True


class ExtraFeatureSettings(BaseModel):
    name: str
    paths: List[str] = Field(default_factory=list)


class GenerationSettings(BaseModel):
    skill_token_budget: int = 8000
    full_diff_threshold: int = 200
    concurrency: int = 4
    framework: str = "auto"  # auto | none | laravel
    ignore: List[str] = Field(default_factory=lambda: ["*.lock", "node_modules/", "dist/", "__pycache__/"])
    features: Optional[List[str]] = None
    extra_features: List[ExtraFeatureSettings] = Field(default_factory=list)


class DocFlowConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    project: ProjectSettings = Field(default_factory=ProjectSettings)
    app: AppSettings = Field(default_factory=AppSettings)
    docs: DocsSettings = Field(default_factory=DocsSettings)
    agent: AgentSettings = Field(default_factory=AgentSettings)
    platform: PlatformSettings = Field(default_factory=PlatformSettings)
    generation: GenerationSettings = Field(default_factory=GenerationSettings)
    source_path: Optional[str] = Field(default=None, exclude=True)

    def save(self, config_dir: str) -> str:
        """Saves current configuration to .docflow.yml in specified directory."""
        target_dir = os.path.abspath(config_dir)
        os.makedirs(target_dir, exist_ok=True)
        target_path = os.path.join(target_dir, ".docflow.yml")
        with open(target_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(self.model_dump(exclude={"source_path"}), f, sort_keys=False)
        return target_path

    @classmethod
    def load(
        cls,
        app_repo_path: Optional[str] = None,
        docs_repo_path: Optional[str] = None,
    ) -> "DocFlowConfig":
        config_data = {}
        candidate_paths = []

        def add_docs_config(directory: Optional[str]) -> None:
            if not directory:
                return
            cpath = os.path.join(os.path.abspath(directory), ".docflow.yml")
            if os.path.isfile(cpath):
                candidate_paths.append(cpath)

        add_docs_config(docs_repo_path)
        # First positional path may itself be a docs folder (has .docflow.yml).
        add_docs_config(app_repo_path)

        cwd_config = os.path.join(os.getcwd(), ".docflow.yml")
        if os.path.isfile(cwd_config):
            candidate_paths.append(cwd_config)

        seen = set()
        for cpath in candidate_paths:
            if cpath in seen:
                continue
            seen.add(cpath)
            try:
                with open(cpath, "r", encoding="utf-8") as f:
                    loaded = yaml.safe_load(f)
                    if isinstance(loaded, dict) and loaded:
                        config_data = loaded
                        config_data["source_path"] = cpath
                        break
            except Exception:
                pass

        # Environment variable overrides
        if env_app_path := os.getenv("DOCFLOW_APP_REPO"):
            config_data.setdefault("app", {})["repo_path"] = env_app_path

        if env_docs_path := os.getenv("DOCFLOW_DOCS_PATH"):
            config_data.setdefault("docs", {})["repo_path"] = env_docs_path

        if env_agent_mode := os.getenv("DOCFLOW_AGENT_MODE"):
            config_data.setdefault("agent", {})["mode"] = env_agent_mode

        if env_agent_cmd := os.getenv("DOCFLOW_AGENT_COMMAND"):
            config_data.setdefault("agent", {})["command"] = env_agent_cmd

        if env_platform := os.getenv("DOCFLOW_PLATFORM"):
            config_data.setdefault("platform", {})["type"] = env_platform

        return cls(**config_data)
