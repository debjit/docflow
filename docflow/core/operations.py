"""
Shared DocFlow operations used by the CLI, interactive menu, and TUI.
"""

from __future__ import annotations

import os
import re
import json
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Tuple

from docflow.config.settings import DocFlowConfig, DocTypeSettings
from docflow.core.agent_runner import AGENT_PRESETS, AgentRunner
from docflow.core.git_analyzer import GitAnalyzer
from docflow.core.llms_txt_generator import LLMSTxtGenerator
from docflow.core.models import AgentRunResult, FeatureChunk, PromptContext
from docflow.core.prompt_builder import PromptBuilder
from docflow.core.status_tracker import StatusTracker
from docflow.git_ops.branch_manager import DocBranchManager
from docflow.git_ops.mr_creator import MRCreator


class ConfigError(Exception):
    """Missing or invalid project configuration."""


class AlreadyInitialized(ConfigError):
    """Docs folder is already a DocFlow repository."""


DEFAULT_DOC_TYPES: List[DocTypeSettings] = [
    DocTypeSettings(
        name="architecture",
        description="System layout, hosting, and shared packages",
    ),
    DocTypeSettings(
        name="features",
        description="Feature and module documentation scanned from the codebase",
    ),
]


def slug_type_name(name: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "-", (name or "").strip()).strip("-").lower()
    return cleaned or "docs"


def parse_doc_type(spec: str) -> DocTypeSettings:
    raw = (spec or "").strip()
    if ":" in raw:
        name, desc = raw.split(":", 1)
        return DocTypeSettings(name=slug_type_name(name), description=desc.strip())
    return DocTypeSettings(name=slug_type_name(raw), description="")


def parse_doc_types_text(text: str) -> List[DocTypeSettings]:
    types: List[DocTypeSettings] = []
    seen = set()
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parsed = parse_doc_type(line)
        if parsed.name in seen:
            continue
        seen.add(parsed.name)
        types.append(parsed)
    return types


def configured_types(config: Optional[DocFlowConfig]) -> List[DocTypeSettings]:
    types = list(config.docs.types) if config and config.docs.types else []
    if types:
        return [DocTypeSettings(name=slug_type_name(t.name), description=t.description) for t in types]
    return list(DEFAULT_DOC_TYPES)


def type_output_dir(doc_type: str, section: str) -> str:
    kind = slug_type_name(doc_type)
    section_slug = slug_type_name(section) if section else kind
    if kind == "features" and section_slug not in ("", "features"):
        return f"features/{section_slug}"
    return kind


def resolve_doc_section(config: Optional[DocFlowConfig], feature: str) -> Tuple[str, str, str]:
    types = configured_types(config)
    wanted = slug_type_name(feature) if feature else ""
    by_name = {t.name: t for t in types}
    if wanted and wanted in by_name:
        t = by_name[wanted]
        return t.name, t.description, type_output_dir(t.name, t.name)
    features = by_name.get("features")
    if features and wanted:
        return features.name, features.description, type_output_dir("features", wanted)
    if wanted and by_name:
        t = next(iter(types))
        return t.name, t.description, type_output_dir(t.name, t.name)
    if types:
        t = types[0]
        return t.name, t.description, type_output_dir(t.name, t.name)
    return "features", "", type_output_dir("features", wanted or "core")


def is_initialized(docs_repo_path: str) -> bool:
    if not docs_repo_path:
        return False
    return os.path.isfile(os.path.join(os.path.abspath(docs_repo_path), ".docflow.yml"))


def assert_can_init(docs_repo_path: str) -> None:
    path = os.path.abspath(docs_repo_path)
    if is_initialized(path):
        raise AlreadyInitialized(
            f"Docs folder is already initialized: {path}. "
            "Use `docflow generate`, `docflow import`, or full regen — not init."
        )
    if not os.path.exists(path):
        return
    skip = {".git", ".gitignore"}
    leftover = [name for name in os.listdir(path) if name not in skip]
    if leftover:
        raise ConfigError(
            f"Docs folder is not empty: {path}. Init only runs in an empty folder. "
            "Import existing files with `docflow import` after a blank init, or choose another path."
        )


@dataclass
class AgentSpec:
    mode: str
    command: str
    name: str = ""


@dataclass
class ResolvedPaths:
    app_repo_path: str
    docs_repo_path: str
    config: DocFlowConfig


@dataclass
class FeatureRunResult:
    feature_name: str
    prompt_file: str
    success: bool
    error_message: Optional[str] = None
    output_log: str = ""


@dataclass
class InitResult:
    app_repo_path: str
    docs_repo_path: str
    agent_mode: str
    agent_command: str
    existing_docs_count: int
    imported: bool
    features: List[FeatureRunResult]
    docs_inside_app: bool
    pending_dir: str
    config_paths: List[str]
    llms_generated: bool = True
    imported_copied: List[str] = field(default_factory=list)
    imported_skipped: List[str] = field(default_factory=list)
    types: List[str] = field(default_factory=list)


@dataclass
class CommitInfo:
    sha: str
    short_sha: str
    message: str
    author: str = ""


@dataclass
class GenerateResult:
    app_repo_path: str
    docs_repo_path: str
    agent_mode: str
    agent_command: str
    is_full: bool
    base_ref: str
    head_ref: str
    task_type: str
    feature_name: str
    prompt_file: str
    no_changes: bool
    run: Optional[AgentRunResult] = None
    commits: List[CommitInfo] = field(default_factory=list)
    commit_count: int = 1
    already_current: bool = False
    watermark_stale: bool = False
    used_cursor: bool = False


@dataclass
class Dashboard:
    project_name: str
    app_repo_path: str
    docs_repo_path: str
    app_exists: bool
    docs_exists: bool
    agent_mode: str
    agent_command: str
    platform: str
    features: List[str]
    pending: List[str]
    configured: bool
    source_path: Optional[str]
    wip_md: Optional[str] = None
    wip_json: Optional[str] = None
    wip_error: Optional[str] = None
    doc_types: List[str] = field(default_factory=list)
    last_documented: Optional[CommitInfo] = None
    new_commits: List[CommitInfo] = field(default_factory=list)


@dataclass
class ImportResult:
    dest_type: str
    copied: List[str] = field(default_factory=list)
    skipped: List[str] = field(default_factory=list)
    source: str = ""
    type_added: bool = False


@dataclass
class GenerateCursor:
    head_sha: str
    short_sha: str
    message: str
    documented_at: str = ""
    commits: List[CommitInfo] = field(default_factory=list)


@dataclass
class PullResult:
    success: bool
    output: str
    app_repo_path: str
    new_commits: List[CommitInfo] = field(default_factory=list)
    last_documented: Optional[CommitInfo] = None
    already_up_to_date: bool = False


@dataclass
class PublishResult:
    docs_repo_path: str
    branch: str
    commit: str
    platform: str
    mr_success: Optional[bool] = None
    mr_url: Optional[str] = None
    mr_message: Optional[str] = None
    auto_mr: bool = False


AGENT_CHOICES: Sequence[Tuple[str, str]] = (
    ("agy", "agy — Antigravity CLI (non-interactive)"),
    ("agy-interactive", "agy-interactive — Antigravity CLI (interactive)"),
    ("opencode", "opencode — OpenCode agent"),
    ("cursor", "cursor — Cursor editor"),
    ("claude", "claude — Claude Code CLI"),
    ("cline", "cline — Cline"),
    ("manual", "manual — write prompts only, do not run an agent"),
    ("custom", "custom — your own shell command"),
)


def project_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def default_docs_path(app_repo_path: str) -> str:
    return os.path.abspath(
        os.path.join(app_repo_path, "..", f"{os.path.basename(app_repo_path)}-docs")
    )


def is_configured(config: Optional[DocFlowConfig] = None) -> bool:
    cfg = config or DocFlowConfig.load()
    return bool(cfg.source_path and cfg.app.repo_path and cfg.docs.repo_path)


def resolve_agent(
    agent: Optional[str] = None,
    mode: Optional[str] = None,
    command: Optional[str] = None,
    config: Optional[DocFlowConfig] = None,
) -> Optional[AgentSpec]:
    """Resolve agent execution from flags, then saved config. Returns None if unset."""
    if command:
        return AgentSpec(mode="shell", command=command, name="custom")
    if agent:
        name = agent.lower()
        if name == "manual":
            return AgentSpec(mode="manual", command="", name="manual")
        if name == "custom":
            return None
        cmd = AGENT_PRESETS.get(name, f"{agent} {{prompt_file}}")
        return AgentSpec(mode="shell", command=cmd, name=name)
    if mode:
        if mode == "manual":
            return AgentSpec(mode="manual", command="", name="manual")
        cfg_cmd = (config.agent.command if config else "") or AGENT_PRESETS["agy"]
        return AgentSpec(mode=mode, command=cfg_cmd, name="shell")
    if config and config.source_path:
        saved_mode = (config.agent.mode or "manual").lower()
        if saved_mode == "manual":
            return AgentSpec(mode="manual", command="", name="manual")
        return AgentSpec(
            mode=saved_mode,
            command=config.agent.command or AGENT_PRESETS["agy"],
            name="saved",
        )
    return None


def resolve_paths(
    repo: Optional[str] = None,
    docs: Optional[str] = None,
    require: bool = True,
) -> ResolvedPaths:
    config = DocFlowConfig.load(repo or None)
    app_repo_path = ""
    if repo:
        app_repo_path = os.path.abspath(repo)
    elif config.app.repo_path:
        app_repo_path = os.path.abspath(config.app.repo_path)

    if app_repo_path:
        reloaded = DocFlowConfig.load(app_repo_path)
        if reloaded.source_path:
            config = reloaded

    docs_repo_path = ""
    if docs:
        docs_repo_path = os.path.abspath(docs)
    elif config.docs.repo_path:
        docs_repo_path = os.path.abspath(config.docs.repo_path)

    if require and not app_repo_path:
        raise ConfigError("Application repo is not set. Run `docflow init` or pass --repo.")
    if require and not docs_repo_path:
        raise ConfigError("Docs repo is not set. Run `docflow init` or pass --docs.")

    return ResolvedPaths(
        app_repo_path=app_repo_path,
        docs_repo_path=docs_repo_path,
        config=config,
    )


def save_project_config(config: DocFlowConfig, app_repo_path: str, docs_repo_path: str) -> List[str]:
    saved: List[str] = []
    for path in (app_repo_path, docs_repo_path):
        try:
            saved.append(config.save(path))
        except Exception:
            pass
    return saved


def _conventions_text(docs_repo_path: str, copy_from_package: bool = False) -> str:
    dest = os.path.join(docs_repo_path, "CONVENTIONS.md")
    src = os.path.join(project_root(), "CONVENTIONS.md")
    if copy_from_package and os.path.exists(src):
        os.makedirs(docs_repo_path, exist_ok=True)
        shutil.copy(src, dest)
        with open(src, "r", encoding="utf-8") as f:
            return f.read()
    if os.path.exists(dest):
        with open(dest, "r", encoding="utf-8") as f:
            return f.read()
    return ""


def refs_for_last_n_commits(count: int) -> Tuple[str, str]:
    """Parent of the oldest included commit … HEAD. Last 1 commit is HEAD~1..HEAD."""
    n = max(1, int(count))
    return f"HEAD~{n}", "HEAD"


def list_recent_commits(app_repo_path: str, count: int = 15, rev: str = "HEAD") -> List[CommitInfo]:
    analyzer = GitAnalyzer(app_repo_path)
    return [CommitInfo(**row) for row in analyzer.list_commits(max_count=count, rev=rev)]


def list_app_branches(app_repo_path: str) -> List[str]:
    return GitAnalyzer(app_repo_path).list_branches()


def list_commits_in_range(app_repo_path: str, base_ref: str, head_ref: str) -> List[CommitInfo]:
    analyzer = GitAnalyzer(app_repo_path)
    return [CommitInfo(**row) for row in analyzer.commits_between(base_ref, head_ref)]


STATE_FILENAME = ".docflow-state.json"


def _state_path(docs_repo_path: str) -> str:
    return os.path.join(os.path.abspath(docs_repo_path), STATE_FILENAME)


def load_generate_cursor(docs_repo_path: str) -> Optional[GenerateCursor]:
    path = _state_path(docs_repo_path)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f) or {}
        raw = data.get("last_generate") or data
        sha = raw.get("head_sha") or ""
        if not sha:
            return None
        commits = [CommitInfo(**row) for row in raw.get("commits") or [] if row.get("sha")]
        return GenerateCursor(
            head_sha=sha,
            short_sha=raw.get("short_sha") or sha[:8],
            message=raw.get("message") or "",
            documented_at=raw.get("documented_at") or "",
            commits=commits,
        )
    except Exception:
        return None


def save_generate_cursor(
    docs_repo_path: str,
    head_sha: str,
    short_sha: str = "",
    message: str = "",
    commits: Optional[List[CommitInfo]] = None,
) -> GenerateCursor:
    cursor = GenerateCursor(
        head_sha=head_sha,
        short_sha=short_sha or head_sha[:8],
        message=message,
        documented_at=datetime.now(timezone.utc).isoformat(),
        commits=list(commits or []),
    )
    os.makedirs(os.path.abspath(docs_repo_path), exist_ok=True)
    payload = {
        "last_generate": {
            "head_sha": cursor.head_sha,
            "short_sha": cursor.short_sha,
            "message": cursor.message,
            "documented_at": cursor.documented_at,
            "commits": [
                {"sha": c.sha, "short_sha": c.short_sha, "message": c.message, "author": c.author}
                for c in cursor.commits
            ],
        }
    }
    with open(_state_path(docs_repo_path), "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
        f.write("\n")
    return cursor


def mark_repo_documented(
    app_repo_path: str,
    docs_repo_path: str,
    commits: Optional[List[CommitInfo]] = None,
    rev: str = "HEAD",
) -> Optional[GenerateCursor]:
    analyzer = GitAnalyzer(app_repo_path)
    head = analyzer.head_commit(rev)
    if not head:
        return None
    return save_generate_cursor(
        docs_repo_path,
        head_sha=head["sha"],
        short_sha=head["short_sha"],
        message=head["message"],
        commits=commits,
    )


def new_commits_since(app_repo_path: str, docs_repo_path: str, rev: str = "HEAD") -> Tuple[Optional[GenerateCursor], List[CommitInfo], bool]:
    """Return (cursor, new commits newest-first, stale). Stale means stored SHA is not an ancestor."""
    cursor = load_generate_cursor(docs_repo_path)
    if not cursor:
        return None, [], False
    analyzer = GitAnalyzer(app_repo_path)
    if not analyzer.is_ancestor(cursor.head_sha, rev):
        return cursor, [], True
    commits = [CommitInfo(**row) for row in analyzer.commits_between(cursor.head_sha, rev)]
    return cursor, commits, False


def pull_app_repo(
    app_repo_path: str,
    docs_repo_path: str = "",
    on_progress: Optional[Callable[[str], None]] = None,
) -> PullResult:
    app_repo_path = os.path.abspath(app_repo_path)
    if on_progress:
        on_progress(f"git pull in {app_repo_path}…")
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    try:
        proc = subprocess.run(
            ["git", "-C", app_repo_path, "pull"],
            capture_output=True,
            text=True,
            env=env,
            timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return PullResult(success=False, output=str(exc), app_repo_path=app_repo_path)
    output = ((proc.stdout or "") + (proc.stderr or "")).strip()
    success = proc.returncode == 0
    if on_progress and output:
        on_progress(output)
    cursor = load_generate_cursor(docs_repo_path) if docs_repo_path else None
    new_commits: List[CommitInfo] = []
    last = None
    if cursor:
        last = CommitInfo(sha=cursor.head_sha, short_sha=cursor.short_sha, message=cursor.message)
    if success and docs_repo_path:
        _, new_commits, _ = new_commits_since(app_repo_path, docs_repo_path)
    already = success and ("already up to date" in output.lower())
    return PullResult(
        success=success,
        output=output,
        app_repo_path=app_repo_path,
        new_commits=new_commits,
        last_documented=last,
        already_up_to_date=already,
    )


def find_existing_docs(app_repo_path: str) -> dict:
    return GitAnalyzer(app_repo_path).find_existing_docs()


_IMPORT_EXTS = {".md", ".mdx", ".txt", ".rst", ".json", ".yaml", ".yml"}


def import_docs(
    source: str,
    docs_repo_path: str,
    type_name: str,
    on_progress: Optional[Callable[[str], None]] = None,
) -> ImportResult:
    """Copy files from source path/folder into docs-repo/<type>/. Never overwrites."""
    src = os.path.abspath(source)
    if not os.path.exists(src):
        raise ConfigError(f"Import path does not exist: {src}")
    dest_type = slug_type_name(type_name)
    dest_root = os.path.join(os.path.abspath(docs_repo_path), dest_type)
    os.makedirs(dest_root, exist_ok=True)
    copied: List[str] = []
    skipped: List[str] = []

    def consider(full_path: str, rel: str) -> None:
        if Path(full_path).suffix.lower() not in _IMPORT_EXTS and Path(full_path).name.upper() != "README":
            return
        dest = os.path.join(dest_root, rel)
        if os.path.exists(dest):
            skipped.append(rel)
            if on_progress:
                on_progress(f"Skip (exists): {dest_type}/{rel}")
            return
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.copy2(full_path, dest)
        copied.append(rel)
        if on_progress:
            on_progress(f"Imported {dest_type}/{rel}")

    if os.path.isfile(src):
        consider(src, os.path.basename(src))
    else:
        for root, dirs, files in os.walk(src):
            dirs[:] = [d for d in dirs if d not in {".git", "node_modules", ".venv", "__pycache__"}]
            for name in files:
                full = os.path.join(root, name)
                rel = os.path.relpath(full, src)
                consider(full, rel)

    type_added = False
    cfg = DocFlowConfig.load(docs_repo_path)
    if cfg.source_path:
        existing = list(cfg.docs.types) if cfg.docs.types else list(DEFAULT_DOC_TYPES)
        if dest_type not in {t.name for t in existing}:
            existing.append(DocTypeSettings(name=dest_type, description="Imported existing documentation"))
            cfg.docs.types = existing
            try:
                cfg.save(docs_repo_path)
                if cfg.app.repo_path:
                    cfg.save(cfg.app.repo_path)
                type_added = True
                if on_progress:
                    on_progress(f"Added doc type '{dest_type}' to config")
            except Exception:
                type_added = False

    return ImportResult(
        dest_type=dest_type,
        copied=copied,
        skipped=skipped,
        source=src,
        type_added=type_added,
    )


def list_features(docs_repo_path: str) -> List[str]:
    features_dir = os.path.join(docs_repo_path, "features")
    if not os.path.isdir(features_dir):
        return []
    return sorted(
        d for d in os.listdir(features_dir) if os.path.isdir(os.path.join(features_dir, d))
    )


def list_pending_prompts(docs_repo_path: str) -> List[str]:
    prompts_dir = os.path.join(docs_repo_path, "prompts", "pending")
    if not os.path.isdir(prompts_dir):
        return []
    return sorted(f for f in os.listdir(prompts_dir) if f.endswith(".md"))


def get_dashboard(repo: Optional[str] = None, docs: Optional[str] = None) -> Dashboard:
    paths = resolve_paths(repo, docs, require=False)
    app_path = paths.app_repo_path
    docs_path = paths.docs_repo_path
    cfg = paths.config
    features = list_features(docs_path) if docs_path else []
    pending = list_pending_prompts(docs_path) if docs_path else []
    types = configured_types(cfg)
    last_documented = None
    new_commits: List[CommitInfo] = []
    if app_path and docs_path and os.path.isdir(app_path):
        try:
            cursor, new_commits, _stale = new_commits_since(app_path, docs_path)
            if cursor:
                last_documented = CommitInfo(
                    sha=cursor.head_sha,
                    short_sha=cursor.short_sha,
                    message=cursor.message,
                )
        except Exception:
            last_documented = None
            new_commits = []
    return Dashboard(
        project_name=cfg.project.name,
        app_repo_path=app_path,
        docs_repo_path=docs_path,
        app_exists=bool(app_path and os.path.exists(app_path)),
        docs_exists=bool(docs_path and os.path.exists(docs_path)),
        agent_mode=cfg.agent.mode,
        agent_command=cfg.agent.command,
        platform=cfg.platform.type,
        features=features,
        pending=pending,
        configured=is_configured(cfg),
        source_path=cfg.source_path,
        doc_types=[f"{t.name}: {t.description}" if t.description else t.name for t in types],
        last_documented=last_documented,
        new_commits=new_commits,
    )


def update_wip(app_repo_path: str, docs_repo_path: str) -> dict:
    tracker = StatusTracker(app_repo_path, docs_repo_path)
    return tracker.write_wip_docs()


def init_docs(
    app_repo_path: str,
    docs_repo_path: str,
    agent: AgentSpec,
    config: Optional[DocFlowConfig] = None,
    import_existing: bool = False,
    capture_output: bool = False,
    on_progress: Optional[Callable[[str], None]] = None,
    types: Optional[List[DocTypeSettings]] = None,
    import_from: Optional[str] = None,
    import_into: Optional[str] = None,
) -> InitResult:
    def progress(message: str) -> None:
        if on_progress:
            on_progress(message)

    app_repo_path = os.path.abspath(app_repo_path)
    docs_repo_path = os.path.abspath(docs_repo_path)
    assert_can_init(docs_repo_path)

    config = config or DocFlowConfig.load(app_repo_path)
    config.app.repo_path = app_repo_path
    config.docs.repo_path = docs_repo_path
    config.agent.mode = agent.mode
    config.agent.command = agent.command
    chosen_types = types or configured_types(config)
    if not chosen_types:
        chosen_types = list(DEFAULT_DOC_TYPES)
    config.docs.types = [
        DocTypeSettings(name=slug_type_name(t.name), description=t.description) for t in chosen_types
    ]
    if not config.project.name or config.project.name == "Project":
        config.project.name = os.path.basename(app_repo_path)

    progress("Creating blank docs skeleton…")
    os.makedirs(docs_repo_path, exist_ok=True)
    for doc_type in config.docs.types:
        os.makedirs(os.path.join(docs_repo_path, doc_type.name), exist_ok=True)
    config_paths = save_project_config(config, app_repo_path, docs_repo_path)
    conventions_text = _conventions_text(docs_repo_path, copy_from_package=True)

    imported = ImportResult(dest_type=slug_type_name(import_into or (config.docs.types[0].name)))
    if import_from:
        into = import_into or config.docs.types[0].name
        if into not in {t.name for t in config.docs.types}:
            config.docs.types.append(DocTypeSettings(name=slug_type_name(into), description="Imported existing documentation"))
            os.makedirs(os.path.join(docs_repo_path, slug_type_name(into)), exist_ok=True)
            save_project_config(config, app_repo_path, docs_repo_path)
        progress(f"Importing from {import_from} into {slug_type_name(into)}…")
        imported = import_docs(import_from, docs_repo_path, into, on_progress=on_progress)
    elif import_existing:
        progress("No --import-from path given; skipping copy. Use a path/folder to import files.")

    analyzer = GitAnalyzer(app_repo_path)
    builder = PromptBuilder()
    runner = AgentRunner(mode=agent.mode, command_template=agent.command)
    jobs: List[Tuple[str, str, str, FeatureChunk]] = []
    for doc_type in config.docs.types:
        if doc_type.name == "features":
            progress("Scanning the app repo for feature modules…")
            chunks = analyzer.scan_features(include_architecture=False)
            for chunk in chunks:
                jobs.append((doc_type.name, doc_type.description, type_output_dir("features", chunk.feature_name), chunk))
        else:
            chunk = FeatureChunk(
                feature_name=doc_type.name,
                description=doc_type.description,
                file_paths=[],
                sample_snippets={},
            )
            jobs.append((doc_type.name, doc_type.description, type_output_dir(doc_type.name, doc_type.name), chunk))

    total = len(jobs)
    progress(f"Writing init prompts for {total} section(s).")
    features: List[FeatureRunResult] = []
    imported_note = None
    if imported.copied:
        imported_note = "Imported files (do not overwrite):\n" + "\n".join(f"- {p}" for p in imported.copied[:40])

    for index, (doc_type, type_desc, output_dir, chunk) in enumerate(jobs, start=1):
        feature_name = chunk.feature_name
        rel_prompt_file = os.path.join("prompts", "pending", f"init-{doc_type}-{feature_name}.md")
        prompt_file = os.path.join(docs_repo_path, rel_prompt_file)
        context = PromptContext(
            task_type="init",
            project_name=config.project.name,
            feature_name=feature_name,
            docs_repo_path=docs_repo_path,
            feature_chunk=chunk,
            existing_index_md=imported_note if doc_type == imported.dest_type else None,
            conventions_text=conventions_text,
            doc_type=doc_type,
            doc_type_description=type_desc,
            output_dir=output_dir,
        )
        progress(f"[{index}/{total}] Writing prompt for {doc_type}/{feature_name}…")
        builder.save_prompt(context, prompt_file)
        if agent.mode == "shell":
            progress(f"[{index}/{total}] Running agent on {doc_type}/{feature_name}…")
        res = runner.run(
            prompt_file,
            docs_repo_path,
            capture=True if capture_output else None,
            on_output=on_progress,
        )
        if res.success and agent.mode == "shell" and os.path.exists(prompt_file):
            completed_dir = os.path.join(docs_repo_path, "prompts", "completed")
            os.makedirs(completed_dir, exist_ok=True)
            shutil.move(prompt_file, os.path.join(completed_dir, os.path.basename(prompt_file)))
        if res.success:
            progress(f"[{index}/{total}] ✓ {doc_type}/{feature_name}")
        else:
            progress(f"[{index}/{total}] ✗ {doc_type}/{feature_name}: {res.error_message}")
        features.append(
            FeatureRunResult(
                feature_name=f"{doc_type}/{feature_name}",
                prompt_file=rel_prompt_file,
                success=res.success,
                error_message=res.error_message,
                output_log=res.output_log,
            )
        )

    progress("Generating llms.txt…")
    LLMSTxtGenerator(docs_repo_path).generate(config.project.name)
    try:
        mark_repo_documented(app_repo_path, docs_repo_path)
    except Exception:
        pass

    return InitResult(
        app_repo_path=app_repo_path,
        docs_repo_path=docs_repo_path,
        agent_mode=agent.mode,
        agent_command=agent.command,
        existing_docs_count=len(imported.copied) + len(imported.skipped),
        imported=bool(imported.copied),
        features=features,
        docs_inside_app=docs_repo_path.startswith(app_repo_path),
        pending_dir=os.path.join(docs_repo_path, "prompts", "pending"),
        config_paths=config_paths,
        imported_copied=imported.copied,
        imported_skipped=imported.skipped,
        types=[t.name for t in config.docs.types],
    )


def generate_docs(
    app_repo_path: str,
    docs_repo_path: str,
    agent: AgentSpec,
    config: Optional[DocFlowConfig] = None,
    from_ref: str = "",
    to_ref: str = "",
    branch: str = "",
    feature: str = "",
    full: bool = False,
    capture_output: bool = False,
    on_progress: Optional[Callable[[str], None]] = None,
    commit_count: Optional[int] = None,
) -> GenerateResult:
    app_repo_path = os.path.abspath(app_repo_path)
    docs_repo_path = os.path.abspath(docs_repo_path)
    config = config or DocFlowConfig.load(app_repo_path)

    is_full = full
    included_count = commit_count or 1
    analyzer = GitAnalyzer(app_repo_path)
    used_cursor = False
    watermark_stale = False
    explicit_range = bool(from_ref or to_ref or is_full or commit_count is not None or branch)
    if not is_full and not from_ref and not to_ref:
        rev = branch or "HEAD"
        if not explicit_range:
            cursor = load_generate_cursor(docs_repo_path)
            if cursor and analyzer.is_ancestor(cursor.head_sha, rev):
                new_rows = analyzer.commits_between(cursor.head_sha, rev)
                if not new_rows:
                    if on_progress:
                        on_progress(
                            f"Already documented through {cursor.short_sha} {cursor.message}. "
                            "git pull to fetch new commits, or pass --commits / --full."
                        )
                    return GenerateResult(
                        app_repo_path=app_repo_path,
                        docs_repo_path=docs_repo_path,
                        agent_mode=agent.mode,
                        agent_command=agent.command,
                        is_full=False,
                        base_ref=cursor.head_sha,
                        head_ref=rev,
                        task_type="update",
                        feature_name=feature or "",
                        prompt_file="",
                        no_changes=True,
                        already_current=True,
                        used_cursor=True,
                        commit_count=0,
                    )
                from_ref = cursor.head_sha
                to_ref = analyzer.head_commit(rev)["sha"] if analyzer.head_commit(rev) else rev
                included_count = len(new_rows)
                used_cursor = True
            else:
                if cursor:
                    watermark_stale = True
                    if on_progress:
                        on_progress(
                            f"Last documented commit {cursor.short_sha} is no longer on this branch. "
                            "Falling back to the latest commit."
                        )
                recent = analyzer.list_commits(max_count=1, rev=rev)
                if recent:
                    from_ref = f"{recent[0]['sha']}^"
                    to_ref = recent[0]["sha"]
                    included_count = 1
        else:
            n = max(1, int(commit_count or 1))
            recent = analyzer.list_commits(max_count=n, rev=rev)
            n = min(len(recent), n) if recent else 0
            if n:
                from_ref = f"{recent[n - 1]['sha']}^"
                to_ref = recent[0]["sha"]
                included_count = n
                branch = ""
    base_ref = from_ref or "HEAD~1"
    head_ref = to_ref or branch or "HEAD"
    conventions_text = _conventions_text(docs_repo_path)

    manifest = analyzer.extract_diff(base_ref=base_ref, head_ref=head_ref)
    included_commits: List[CommitInfo] = []
    if not is_full:
        try:
            included_commits = [CommitInfo(**row) for row in analyzer.commits_between(base_ref, head_ref)]
            if included_commits:
                included_count = len(included_commits)
        except Exception:
            included_commits = []

    if on_progress and included_commits:
        on_progress(f"Including {len(included_commits)} commit(s):")
        for commit in included_commits:
            on_progress(f"  {commit.short_sha}  {commit.message}")

    if not manifest.changed_files and not is_full:
        try:
            mark_repo_documented(app_repo_path, docs_repo_path, included_commits, head_ref)
        except Exception:
            pass
        return GenerateResult(
            app_repo_path=app_repo_path,
            docs_repo_path=docs_repo_path,
            agent_mode=agent.mode,
            agent_command=agent.command,
            is_full=is_full,
            base_ref=base_ref,
            head_ref=head_ref,
            task_type="update",
            feature_name=feature or "",
            prompt_file="",
            no_changes=True,
            commits=included_commits,
            commit_count=included_count,
            used_cursor=used_cursor,
            watermark_stale=watermark_stale,
        )

    target_feature = feature or (
        manifest.changed_files[0].path.split("/")[0] if manifest.changed_files else "architecture"
    )
    doc_type, type_desc, output_dir = resolve_doc_section(config, target_feature)
    feature_dir = os.path.join(docs_repo_path, output_dir)
    existing_index = None
    existing_context = None
    index_path = os.path.join(feature_dir, "index.md")
    context_path = os.path.join(feature_dir, "context.json")
    if os.path.exists(index_path):
        with open(index_path, "r", encoding="utf-8") as f:
            existing_index = f.read()
    if os.path.exists(context_path):
        with open(context_path, "r", encoding="utf-8") as f:
            existing_context = f.read()

    task_type = "full-regen" if is_full else "update"
    context = PromptContext(
        task_type=task_type,
        project_name=config.project.name,
        feature_name=target_feature,
        docs_repo_path=docs_repo_path,
        change_manifest=manifest,
        existing_index_md=existing_index,
        existing_context_json=existing_context,
        conventions_text=conventions_text,
        doc_type=doc_type,
        doc_type_description=type_desc,
        output_dir=output_dir,
    )
    builder = PromptBuilder()
    prompt_file = os.path.join(docs_repo_path, "prompts", "pending", f"{task_type}-{target_feature}.md")
    if on_progress:
        on_progress(f"Writing {task_type} prompt for {target_feature}…")
    builder.save_prompt(context, prompt_file)

    runner = AgentRunner(mode=agent.mode, command_template=agent.command)
    if on_progress and agent.mode == "shell":
        on_progress(f"Running agent on {target_feature}…")
    res = runner.run(
        prompt_file,
        docs_repo_path,
        capture=True if capture_output else None,
        on_output=on_progress,
    )
    if res.success and agent.mode == "shell" and os.path.exists(prompt_file):
        completed_dir = os.path.join(docs_repo_path, "prompts", "completed")
        os.makedirs(completed_dir, exist_ok=True)
        shutil.move(prompt_file, os.path.join(completed_dir, os.path.basename(prompt_file)))
    if res.success:
        try:
            mark_repo_documented(app_repo_path, docs_repo_path, included_commits, head_ref)
        except Exception:
            pass

    return GenerateResult(
        app_repo_path=app_repo_path,
        docs_repo_path=docs_repo_path,
        agent_mode=agent.mode,
        agent_command=agent.command,
        is_full=is_full,
        base_ref=base_ref,
        head_ref=head_ref,
        task_type=task_type,
        feature_name=target_feature,
        prompt_file=prompt_file,
        no_changes=False,
        run=res,
        commits=included_commits,
        commit_count=included_count,
        used_cursor=used_cursor,
        watermark_stale=watermark_stale,
    )


def publish_docs(
    docs_repo_path: str,
    config: Optional[DocFlowConfig] = None,
    platform: str = "",
    message: str = "docs: update documentation",
) -> PublishResult:
    docs_repo_path = os.path.abspath(docs_repo_path)
    config = config or DocFlowConfig.load()
    platform_type = platform or config.platform.type
    branch_mgr = DocBranchManager(docs_repo_path)
    current_branch = branch_mgr.prepare_update_branch("update")
    LLMSTxtGenerator(docs_repo_path).generate(config.project.name)
    commit_res = branch_mgr.commit_and_push(message)

    result = PublishResult(
        docs_repo_path=docs_repo_path,
        branch=current_branch,
        commit=commit_res,
        platform=platform_type,
        auto_mr=bool(config.platform.auto_mr),
    )
    if config.platform.auto_mr:
        mr_creator = MRCreator(platform_type=platform_type)
        res = mr_creator.create_mr(
            title=message,
            body=f"Automated documentation update generated by DocFlow.\n\nBranch: `{current_branch}`",
            source_branch=current_branch,
            target_branch="main",
        )
        result.mr_success = bool(res.get("success"))
        result.mr_url = res.get("mr_url")
        result.mr_message = res.get("error") or res.get("message")
    return result
