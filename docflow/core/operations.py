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
import shlex
from typing import Callable, List, Optional, Sequence, Tuple

from docflow.config.settings import DocFlowConfig, DocTypeSettings, ExtraFeatureSettings
from docflow.core.agent_runner import AGENT_PRESETS, AgentRunner
from docflow.core.projects import (
    find_by_app,
    find_by_docs,
    last_usable_project,
    register_project,
)
from docflow.core.git_analyzer import GitAnalyzer, feature_bucket_for_path, posix_rel
from docflow.core.frameworks import (
    architecture_seed_paths,
    effective_ignore,
    load_stack_file,
    resolve_framework_name,
    skip_as_feature_dirs,
    stack_file_path,
    stack_guidance,
)
from docflow.core.inventory import (
    APP_KIND_ORDER,
    KIND_TO_SECTION,
    inventory_app_items,
    is_bootstrap_item,
    is_tooling_item,
    stack_items_from_payload,
)
from docflow.core.workspace import (
    SPLIT_DOC_TYPES,
    completed_prompts_dir,
    docs_root_from_config_path,
    find_conventions_path,
    is_docs_project,
    pending_prompt_path,
    pending_prompts_dir,
    prompt_search_dirs,
    rel_pending_prompt,
    write_conventions_path,
    write_state_path,
    find_state_path,
)
from docflow.core.job_runner import Job, RunControl, clamp_concurrency, default_concurrency, run_jobs
from docflow.core.llms_txt_generator import LLMSTxtGenerator
from docflow.core.models import AgentRunResult, FeatureChunk, PromptContext
from docflow.core.prompt_builder import PromptBuilder
from docflow.core.status_tracker import StatusTracker
from docflow.git_ops.branch_manager import DocBranchManager
from docflow.git_ops.mr_creator import MRCreator, git_origin_slug


class ConfigError(Exception):
    """Missing or invalid project configuration."""


class AlreadyInitialized(ConfigError):
    """Docs folder is already a DocFlow repository."""


class InitCancelled(ConfigError):
    """User cancelled init before any docs were written."""


NOISY_SECTION_NAMES = {
    "git", "github", "gitlab", "ci", "vendor", "node_modules",
    "storage", "bootstrap", "public", "tests", "test", "core",
}


DEFAULT_DOC_TYPES: List[DocTypeSettings] = [
    DocTypeSettings(
        name="architecture",
        description="System layout, hosting, and packages this app uses",
    ),
    DocTypeSettings(
        name="database",
        description="Schema and migrations",
    ),
    DocTypeSettings(
        name="models",
        description="Domain models",
    ),
    DocTypeSettings(
        name="functions",
        description="Application services, jobs, actions, and controllers",
    ),
    DocTypeSettings(
        name="routes",
        description="HTTP routes",
    ),
    DocTypeSettings(
        name="pages",
        description="UI pages and indexes (Inertia, Filament, views)",
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
    if kind in SPLIT_DOC_TYPES and section_slug not in ("", kind):
        return f"{kind}/{section_slug}"
    return kind


def resolve_doc_section(config: Optional[DocFlowConfig], feature: str) -> Tuple[str, str, str]:
    types = configured_types(config)
    wanted = slug_type_name(feature) if feature else ""
    by_name = {t.name: t for t in types}
    extras = list(config.generation.extra_features or []) if config else []
    for extra in extras:
        if extra.name == wanted:
            dtype = slug_type_name(extra.doc_type) if extra.doc_type else ""
            if dtype in by_name:
                t = by_name[dtype]
                return t.name, t.description, type_output_dir(t.name, wanted)
            mapped = KIND_TO_SECTION.get(dtype)
            if mapped and mapped in by_name:
                t = by_name[mapped]
                return t.name, t.description, type_output_dir(t.name, wanted)
    if wanted and wanted in by_name:
        t = by_name[wanted]
        return t.name, t.description, type_output_dir(t.name, t.name)
    features = by_name.get("features")
    if features and wanted:
        return features.name, features.description, type_output_dir("features", wanted)
    if wanted and by_name:
        t = next(iter(types))
        return t.name, t.description, type_output_dir(t.name, wanted)
    if types:
        t = types[0]
        return t.name, t.description, type_output_dir(t.name, t.name)
    return "architecture", "", type_output_dir("architecture", wanted or "architecture")


def generate_section_names(
    changed_files: Sequence,
    feature: str = "",
    skip_as_feature: Optional[set] = None,
    config: Optional[DocFlowConfig] = None,
) -> List[str]:
    """Unique feature/section buckets for a generate run, preserving first-seen order."""
    if feature:
        return [feature]
    names: List[str] = []
    seen = set()
    skip = skip_as_feature or set()
    for item in changed_files:
        path = item.path if hasattr(item, "path") else str(item)
        bucket = documented_name_for_path(path, config, skip)
        if not bucket or bucket in seen:
            continue
        seen.add(bucket)
        names.append(bucket)
    return names or ["architecture"]


def documented_name_for_path(
    path: str,
    config: Optional[DocFlowConfig] = None,
    skip_as_feature: Optional[set] = None,
) -> Optional[str]:
    """Map a changed file to a selected documentation unit, else a folder bucket."""
    posix = posix_rel(path)
    extras = []
    if config is not None:
        extras = list(config.generation.extra_features or [])
    for extra in extras:
        for raw in extra.paths or []:
            mapped = posix_rel(raw)
            if not mapped:
                continue
            if posix == mapped or posix.startswith(mapped.rstrip("/") + "/") or mapped.startswith(posix.rstrip("/") + "/"):
                return extra.name
    return feature_bucket_for_path(posix, skip_as_feature=skip_as_feature)


def allowed_feature_names(config: Optional[DocFlowConfig]) -> Optional[set]:
    if not config:
        return None
    extras = [item.name for item in config.generation.extra_features or []]
    stored = config.generation.features
    has_features = any(t.name == "features" for t in config.docs.types)
    if not has_features:
        return set(extras) if extras else None
    if stored is None and not extras:
        return None
    return set(stored or []) | set(extras)


@dataclass
class SectionCandidate:
    """One discovered application unit (class, page, resource) the user can include or skip."""

    doc_type: str
    name: str
    description: str = ""
    file_paths: List[str] = field(default_factory=list)
    included: bool = True
    extra: bool = False
    sample_snippets: dict = field(default_factory=dict)
    kind: str = ""
    title: str = ""

    @property
    def key(self) -> str:
        if self.doc_type == "features":
            return f"features/{self.name}"
        return self.doc_type

    @property
    def display_name(self) -> str:
        return self.title or self.name

    @property
    def label(self) -> str:
        kind = self.kind or self.doc_type
        return f"{self.display_name}  ({kind_item_label(kind)})"

    def to_chunk(self) -> FeatureChunk:
        desc = self.description or f"{self.kind or 'feature'}: {self.display_name}"
        return FeatureChunk(
            feature_name=self.name,
            description=desc,
            file_paths=self.file_paths,
            sample_snippets=self.sample_snippets,
        )


def suggested_section_included(name: str, has_architecture: bool = False, kind: str = "") -> bool:
    if is_tooling_item(name, kind) or is_bootstrap_item(name, kind):
        return False
    if name in NOISY_SECTION_NAMES:
        return False
    if has_architecture and name == "core":
        return False
    return True


def _candidate_from_item(
    item: dict,
    analyzer: GitAnalyzer,
    known_types: Optional[set] = None,
    fallback_type: str = "features",
    rev: str = "",
) -> Optional[SectionCandidate]:
    title = str(item.get("title") or item.get("id") or "").strip()
    kind = str(item.get("kind") or "module").strip()
    rel = str(item.get("path") or "").strip()
    name = slug_type_name(str(item.get("id") or title))
    if not name or is_tooling_item(title or name, kind, rel) or is_bootstrap_item(title or name, kind, rel):
        return None
    paths = [rel.replace("\\", "/")] if rel else []
    snippets = analyzer._snippets_for(paths[:1], rev=rev or None) if paths else {}
    included = bool(item.get("include", True))
    if not suggested_section_included(name, kind=kind):
        included = False
    doc_type = _section_for_item(item, known_types or set(), fallback_type)
    return SectionCandidate(
        doc_type=doc_type,
        name=name,
        title=title or name,
        kind=kind,
        description=f"{kind}: {title or name}",
        file_paths=paths,
        included=included,
        sample_snippets=snippets,
    )


def _section_for_item(item: dict, known_types: set, fallback_type: str) -> str:
    kind = str(item.get("kind") or "").strip().lower()
    section = slug_type_name(str(item.get("section") or ""))
    if kind in ("architecture", "overview") and "architecture" in known_types:
        return "architecture"
    if section in known_types:
        return section
    mapped = KIND_TO_SECTION.get(kind)
    if mapped and mapped in known_types:
        return mapped
    if "features" in known_types:
        return "features"
    return fallback_type or "architecture"


def discover_init_sections(
    analyzer: GitAnalyzer,
    types: Sequence[DocTypeSettings],
    ignore_patterns: set,
    skip_dirs: set,
    arch_seeds: Optional[List[str]] = None,
    on_progress: Optional[Callable[[str], None]] = None,
    stack_payload: Optional[dict] = None,
    rev: str = "",
) -> List[SectionCandidate]:
    """Return individual application units (not folders) for the init picker."""
    del skip_dirs
    known = [t.name for t in types]
    known_set = set(known)
    fallback = known[0] if known else "features"
    has_architecture = "architecture" in known_set
    if on_progress:
        on_progress("Finding application units to document…")
    agent_items = stack_items_from_payload(stack_payload, analyzer.repo_path)
    tree_paths = analyzer.list_tree_paths(rev) if rev else None
    raw_items = agent_items or inventory_app_items(
        analyzer.repo_path,
        ignore_patterns,
        paths=tree_paths,
    )
    candidates: List[SectionCandidate] = []
    seen = set()
    covered: set = set()
    for raw in raw_items:
        candidate = _candidate_from_item(raw, analyzer, known_set, fallback, rev=rev)
        if candidate is None or candidate.name in seen:
            continue
        if has_architecture and candidate.name == "core":
            candidate.included = False
        seen.add(candidate.name)
        covered.add(candidate.doc_type)
        candidates.append(candidate)
    prefix: List[SectionCandidate] = []
    for doc_type in types:
        if doc_type.name in covered or doc_type.name in SPLIT_DOC_TYPES:
            continue
        seed_paths = list(arch_seeds or []) if doc_type.name == "architecture" else []
        prefix.append(
            SectionCandidate(
                doc_type=doc_type.name,
                name=doc_type.name,
                title=doc_type.name.replace("-", " ").title(),
                kind="overview",
                description=doc_type.description,
                file_paths=seed_paths,
                included=True,
            )
        )
    return prefix + candidates


def apply_section_filters(
    candidates: List[SectionCandidate],
    include: Sequence[str] = (),
    exclude: Sequence[str] = (),
) -> List[SectionCandidate]:
    include_keys = {_normalize_section_filter(item) for item in include if str(item).strip()}
    exclude_keys = {_normalize_section_filter(item) for item in exclude if str(item).strip()}
    if include_keys:
        for candidate in candidates:
            candidate.included = _candidate_matches(candidate, include_keys)
    for candidate in candidates:
        if _candidate_matches(candidate, exclude_keys):
            candidate.included = False
    return candidates


def _normalize_section_filter(value: str) -> str:
    text = (value or "").strip().lower().replace("\\", "/")
    if text.startswith("features/"):
        text = text.split("/", 1)[1]
    return text


def _candidate_matches(candidate: SectionCandidate, keys: set) -> bool:
    if not keys:
        return False
    names = {
        candidate.key.lower(),
        candidate.name.lower(),
        candidate.doc_type.lower(),
        (candidate.title or "").lower(),
        f"features/{candidate.name}".lower(),
    }
    return bool(names & keys)


def extra_section_from_entry(
    analyzer: GitAnalyzer,
    raw: str,
    ignore_patterns: set,
    skip_dirs: set,
    doc_type: str = "functions",
) -> SectionCandidate:
    chunk = analyzer.chunk_from_entry(raw, ignore_patterns, skip_dirs)
    requested = posix_rel(raw)
    requested_full = os.path.join(analyzer.repo_path, requested) if requested else ""
    posix = posix_rel(chunk.file_paths[0] if chunk.file_paths else raw)
    stem = Path(posix).stem if posix else chunk.feature_name
    if requested_full and os.path.isdir(requested_full):
        name = slug_type_name(Path(requested).name or chunk.feature_name)
        title = Path(requested).name or chunk.feature_name
    elif requested_full and os.path.isfile(requested_full):
        name = slug_type_name(stem)
        title = stem
    else:
        name = slug_type_name(chunk.feature_name)
        title = chunk.feature_name
    mapped = KIND_TO_SECTION.get("function", "functions")
    chosen_type = doc_type if doc_type != "features" else mapped
    return SectionCandidate(
        doc_type=chosen_type,
        name=name,
        title=title,
        kind="function",
        description=chunk.description,
        file_paths=list(chunk.file_paths),
        included=True,
        extra=True,
        sample_snippets=dict(chunk.sample_snippets),
    )


def selected_sections(candidates: Sequence[SectionCandidate]) -> List[SectionCandidate]:
    return [item for item in candidates if item.included]


KIND_HEADINGS = {
    "architecture": "Architecture",
    "overview": "Architecture",
    "database": "Migrations",
    "models": "Eloquent models",
    "functions": "Functions",
    "routes": "Routes",
    "pages": "Pages",
    "features": "Features",
    "other": "Also important",
    "model": "Eloquent models",
    "migration": "Migrations",
    "filament-resource": "Pages",
    "filament-page": "Pages",
    "controller": "Functions",
    "policy": "Policies",
    "job": "Jobs",
    "service": "Services",
    "action": "Actions",
    "function": "Functions",
    "page": "Pages",
    "component": "Components",
    "route": "Routes",
    "module": "Functions",
}

KIND_ITEM_LABELS = {
    "model": "Eloquent model",
    "models": "Eloquent model",
    "migration": "migration",
    "database": "migration",
    "architecture": "architecture",
    "overview": "architecture",
    "filament-resource": "Filament resource",
    "filament-page": "Filament page",
    "controller": "controller",
    "function": "function",
    "functions": "function",
    "page": "page",
    "pages": "page",
    "route": "route",
    "routes": "route",
    "module": "function",
}


def kind_heading(kind: str) -> str:
    key = (kind or "").strip().lower()
    if key in KIND_HEADINGS:
        return KIND_HEADINGS[key]
    return (kind or "other").replace("-", " ").title() or "Other"


def kind_item_label(kind: str) -> str:
    key = (kind or "").strip().lower()
    if key in KIND_ITEM_LABELS:
        return KIND_ITEM_LABELS[key]
    return key.replace("-", " ") or "item"


def group_match_keys(kind: str) -> set:
    heading = kind_heading(kind).lower()
    keys = {
        kind.lower(),
        heading,
        heading.replace(" ", "-"),
        heading.replace(" ", ""),
    }
    extras = {
        "database": {"migrations", "migration", "db"},
        "migration": {"migrations", "database", "db"},
        "models": {"model", "eloquent", "eloquent-models", "eloquent models"},
        "model": {"models", "eloquent"},
    }
    keys.update(extras.get(kind.lower(), set()))
    return keys


def resolve_picker_group(query: str, groups: Sequence[str]) -> Optional[str]:
    text = (query or "").strip().lower()
    if not text:
        return None
    for kind in groups:
        if text in group_match_keys(kind):
            return kind
    return None


def toggle_group_included(candidates: Sequence[SectionCandidate], group: str) -> bool:
    """Select all in a picker group, or deselect all if every item is already on."""
    indices = [i for i, item in enumerate(candidates) if picker_group(item) == group]
    if not indices:
        return False
    want = not all(candidates[i].included for i in indices)
    for i in indices:
        candidates[i].included = want
    return True


def picker_group(item: SectionCandidate) -> str:
    if (item.kind or "").strip().lower() == "other":
        return "other"
    if item.doc_type and item.doc_type != "features":
        return item.doc_type
    return (item.kind or item.doc_type or "module").strip().lower() or "module"


def group_candidates(candidates: Sequence[SectionCandidate]) -> List[Tuple[str, List[int]]]:
    """Group picker rows; values are indexes into `candidates`."""
    buckets: dict[str, List[int]] = {}
    for index, item in enumerate(candidates):
        buckets.setdefault(picker_group(item), []).append(index)
    grouped: List[Tuple[str, List[int]]] = []
    order = [
        "architecture", "overview",
        "models", "database", "functions", "routes", "pages", "features",
        *APP_KIND_ORDER, "other",
    ]
    seen_keys = set()
    for kind in order:
        if kind in buckets and kind not in seen_keys:
            grouped.append((kind, buckets.pop(kind)))
            seen_keys.add(kind)
    for kind in sorted(buckets):
        grouped.append((kind, buckets[kind]))
    return grouped


def _generation_context(app_repo_path: str, config: DocFlowConfig) -> Tuple[Optional[str], set, set]:
    """Resolve framework profile, merged ignore patterns, and skip-as-feature dirs."""
    framework_name = resolve_framework_name(app_repo_path, config.generation.framework)
    ignore = effective_ignore(app_repo_path, config.generation.ignore, framework_name)
    skip = skip_as_feature_dirs(framework_name)
    return framework_name, ignore, skip


def _documentation_guidance(
    docs_repo_path: str,
    framework_name: Optional[str],
) -> Optional[str]:
    return stack_guidance(docs_repo_path, framework_name)


def _persist_detected_framework(
    config: DocFlowConfig,
    framework_name: Optional[str],
    app_repo_path: str,
    docs_repo_path: str,
) -> None:
    if config.generation.framework == "auto" and framework_name:
        config.generation.framework = framework_name
        save_project_config(config, app_repo_path, docs_repo_path)


def is_initialized(docs_repo_path: str) -> bool:
    if not docs_repo_path:
        return False
    return is_docs_project(docs_repo_path)


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
    model: str = ""
    plan_model: str = ""


@dataclass(frozen=True)
class ModelChoice:
    """One LLM a coding agent can run."""

    key: str
    value: str
    label: str
    group: str  # current | third_party
    group_label: str = ""


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
    features: List[FeatureRunResult] = field(default_factory=list)
    synced_remote: bool = False
    app_branch: str = ""
    new_items: List[str] = field(default_factory=list)


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
    concurrency: int = 1
    agent_name: str = ""
    agent_model: str = ""
    plan_model: str = ""
    app_branch: str = ""


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
    ("cursor-agent", "cursor-agent — Cursor agent (non-interactive)"),
    ("cursor-interactive", "cursor-interactive — Cursor agent (interactive)"),
    ("claude", "claude — Claude Code CLI"),
    ("cline", "cline — Cline"),
    ("manual", "manual — write prompts only, do not run an agent"),
    ("custom", "custom — your own shell command"),
)

CURSOR_AGENT_KEYS = frozenset({"cursor", "cursor-agent", "cursor-interactive"})
AGY_AGENT_KEYS = frozenset({"agy", "agy-interactive"})
PREFERRED_APP_BRANCHES = ("main", "master", "develop")
DEFAULT_CURSOR_MODEL = "composer-2.5"
DEFAULT_CURSOR_PLAN_MODEL = "composer-2.5"
DEFAULT_CURSOR_WORK_MODEL = "composer-2.5-fast"
CURSOR_GROUP_LABELS = {
    "current": "Cursor included usage",
    "third_party": "Third-party API usage",
}
AGY_GROUP_LABELS = {
    "current": "Current",
    "third_party": "Third-party",
}
_MODEL_FLAG = re.compile(
    r"\s+--model(?:\s+|=)(?:'(?:\\'|[^'])*'|\"(?:\\\"|[^\"])*\"|\S+)"
)
_MODEL_VALUE = re.compile(
    r"--model(?:\s+|=)('(?:\\'|[^'])*'|\"(?:\\\"|[^\"])*\"|\S+)"
)


def command_without_model(command: str) -> str:
    return re.sub(r"\s+", " ", _MODEL_FLAG.sub("", command or "")).strip()


def model_from_command(command: str) -> str:
    match = _MODEL_VALUE.search(command or "")
    if not match:
        return ""
    raw = match.group(1).strip()
    if len(raw) >= 2 and raw[0] in {"'", '"'} and raw[-1] == raw[0]:
        return raw[1:-1]
    return raw


def agent_key_from_command(command: str) -> str:
    """Map a saved shell command back to an AGENT_CHOICES key."""
    cmd = command_without_model(command)
    if not cmd:
        return ""
    matches = [
        key
        for key, preset in AGENT_PRESETS.items()
        if key not in {"manual", "cursor"} and command_without_model(preset) == cmd
    ]
    if matches:
        return matches[0]
    first = cmd.split()[0]
    aliases = {"agent": "cursor-agent", "agy": "agy", "claude": "claude", "cline": "cline", "opencode": "opencode"}
    return aliases.get(first, "")


def infer_agent_name(config: Optional[DocFlowConfig]) -> str:
    if config is None:
        return ""
    name = (config.agent.name or "").strip().lower()
    if name and name not in {"saved", "shell"}:
        return name
    if (config.agent.mode or "").lower() == "manual":
        return "manual"
    return agent_key_from_command(config.agent.command or "")


def infer_agent_model(config: Optional[DocFlowConfig]) -> str:
    if config is None:
        return ""
    saved = (config.agent.model or "").strip()
    if saved:
        return saved
    return model_from_command(config.agent.command or "")


def infer_plan_model(config: Optional[DocFlowConfig]) -> str:
    if config is None:
        return ""
    return (config.agent.plan_model or "").strip()


def remember_agent(config: DocFlowConfig, spec: AgentSpec) -> None:
    """Keep the last agent + models so the next run can reuse them."""
    name = spec.name if spec.name not in {"saved", "shell", ""} else agent_key_from_command(spec.command)
    if spec.mode == "manual":
        name = "manual"
    config.agent.mode = spec.mode
    config.agent.command = spec.command
    config.agent.name = name or infer_agent_name(config)
    work = (spec.model or "").strip() or model_from_command(spec.command)
    config.agent.model = work
    if (spec.plan_model or "").strip():
        config.agent.plan_model = spec.plan_model.strip()


def project_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def default_docs_path(app_repo_path: str) -> str:
    return os.path.abspath(
        os.path.join(app_repo_path, "..", f"{os.path.basename(app_repo_path)}-docs")
    )


def docs_dir_from_config(config: DocFlowConfig) -> str:
    """Docs repo root for a loaded config (`.docflow/config.yml` or legacy `.docflow.yml`)."""
    if config.source_path:
        return docs_root_from_config_path(config.source_path)
    if config.docs.repo_path:
        return os.path.abspath(config.docs.repo_path)
    return ""


def docs_repo_from_cwd(cwd: Optional[str] = None) -> str:
    """If this folder is a docs project, return it.

    A leftover config in an app/tool repo often still points at the real docs
    folder (`docs.repo_path`). Prefer that when it is a DocFlow project.
    """
    folder = os.path.abspath(cwd or os.getcwd())
    if not is_initialized(folder):
        return ""
    config = DocFlowConfig.load(docs_repo_path=folder)
    pointed = (config.docs.repo_path or "").strip()
    if pointed:
        pointed = os.path.abspath(pointed)
        if pointed != folder and is_initialized(pointed):
            return pointed
    return folder


def is_configured(config: Optional[DocFlowConfig] = None) -> bool:
    cfg = config or DocFlowConfig.load()
    docs_dir = docs_dir_from_config(cfg)
    app_path = (cfg.app.repo_path or "").strip()
    return bool(docs_dir and is_initialized(docs_dir) and app_path)


def agent_supports_models(agent_key: str) -> bool:
    return agent_key in CURSOR_AGENT_KEYS or agent_key in AGY_AGENT_KEYS


def parse_cursor_model_list(output: str) -> List[Tuple[str, str]]:
    """Parse `agent models` text into (id, label) pairs."""
    rows: List[Tuple[str, str]] = []
    seen = set()
    for raw in (output or "").splitlines():
        line = raw.strip()
        if not line or " - " not in line:
            continue
        lower = line.lower()
        if lower.startswith("available") or lower.startswith("tip:"):
            continue
        key, label = line.split(" - ", 1)
        key = key.strip()
        label = label.strip()
        if not key or " " in key or key in seen:
            continue
        seen.add(key)
        rows.append((key, label))
    return rows


def parse_agy_model_list(output: str) -> List[Tuple[str, str]]:
    """Parse `agy models` text into (id, display name) pairs."""
    rows: List[Tuple[str, str]] = []
    seen = set()
    for raw in (output or "").splitlines():
        line = raw.strip()
        if not line or line.lower().startswith("fetching"):
            continue
        if "\t" in line:
            key, label = line.split("\t", 1)
        else:
            parts = re.split(r"\s{2,}", line, maxsplit=1)
            if len(parts) != 2:
                continue
            key, label = parts
        key, label = key.strip(), label.strip()
        if not key or key in seen:
            continue
        if not re.match(r"^[A-Za-z][A-Za-z0-9._+-]*$", key):
            continue
        seen.add(key)
        rows.append((key, label or key))
    return rows


def _label_normal_variants(rows: Sequence[Tuple[str, str]]) -> List[Tuple[str, str]]:
    keys = {key for key, _ in rows}
    labeled: List[Tuple[str, str]] = []
    for key, label in rows:
        if (
            f"{key}-fast" in keys
            and "fast" not in label.lower()
            and "(normal)" not in label.lower()
        ):
            label = f"{label} (normal)"
        labeled.append((key, label))
    return labeled


def _is_cursor_current(key: str) -> bool:
    return key == "auto" or key.startswith(("composer-", "cursor-"))


def _cursor_current_rank(key: str) -> Tuple[int, str, int]:
    if key == "auto":
        return (0, "", 0)
    if key.startswith("composer-"):
        return (1, key.replace("-fast", ""), 1 if key.endswith("-fast") else 0)
    if key.startswith("cursor-"):
        return (2, key, 0)
    return (3, key, 0)


def catalog_cursor_models(rows: Sequence[Tuple[str, str]]) -> List[ModelChoice]:
    """Included usage (Composer, Grok, Auto) first; third-party API models underneath."""
    rows = _label_normal_variants(list(rows))
    if not any(key == "auto" for key, _ in rows):
        rows = [("auto", "Auto (default)")] + rows
    current = [(k, lab) for k, lab in rows if _is_cursor_current(k)]
    third = [(k, lab) for k, lab in rows if not _is_cursor_current(k)]
    current.sort(key=lambda item: _cursor_current_rank(item[0]))
    third.sort(key=lambda item: item[1].lower())
    choices = [
        ModelChoice(
            key=k,
            value=k,
            label=lab,
            group="current",
            group_label=CURSOR_GROUP_LABELS["current"],
        )
        for k, lab in current
    ]
    choices.extend(
        ModelChoice(
            key=k,
            value=k,
            label=lab,
            group="third_party",
            group_label=CURSOR_GROUP_LABELS["third_party"],
        )
        for k, lab in third
    )
    return choices


def default_cursor_model(catalog: Sequence[ModelChoice]) -> str:
    """DocFlow default for Cursor: composer-2.5, else first composer-*, else auto."""
    keys = [c.key for c in catalog]
    if DEFAULT_CURSOR_MODEL in keys:
        return DEFAULT_CURSOR_MODEL
    for key in keys:
        if key.startswith("composer-") and not key.endswith("-fast"):
            return key
    for key in keys:
        if key.startswith("composer-"):
            return key
    if "auto" in keys:
        return "auto"
    return keys[0] if keys else ""


def default_cursor_plan_model(catalog: Sequence[ModelChoice]) -> str:
    """Bigger default for search / stack survey."""
    keys = [c.key for c in catalog]
    if DEFAULT_CURSOR_PLAN_MODEL in keys:
        return DEFAULT_CURSOR_PLAN_MODEL
    return default_cursor_model(catalog)


def default_cursor_work_model(catalog: Sequence[ModelChoice]) -> str:
    """Smaller default for writing docs from a prepared prompt."""
    keys = [c.key for c in catalog]
    if DEFAULT_CURSOR_WORK_MODEL in keys:
        return DEFAULT_CURSOR_WORK_MODEL
    for key in keys:
        if key.endswith("-fast"):
            return key
    return default_cursor_model(catalog)


def catalog_agy_models(rows: Sequence[Tuple[str, str]]) -> List[ModelChoice]:
    """Gemini first (Antigravity current); Claude/GPT and others under Third-party."""
    current = [(k, lab) for k, lab in rows if k.startswith("gemini-")]
    third = [(k, lab) for k, lab in rows if not k.startswith("gemini-")]
    choices = [
        ModelChoice(
            key="default",
            value="",
            label="Default (agy default)",
            group="current",
            group_label=AGY_GROUP_LABELS["current"],
        )
    ]
    choices.extend(
        ModelChoice(
            key=k,
            value=k,
            label=lab,
            group="current",
            group_label=AGY_GROUP_LABELS["current"],
        )
        for k, lab in current
    )
    choices.extend(
        ModelChoice(
            key=k,
            value=k,
            label=lab,
            group="third_party",
            group_label=AGY_GROUP_LABELS["third_party"],
        )
        for k, lab in third
    )
    return choices


def list_cursor_models(timeout: float = 20.0) -> List[Tuple[str, str]]:
    """Ask the Cursor CLI which models this account can use. Empty if unavailable."""
    try:
        proc = subprocess.run(
            ["agent", "models"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return []
    return parse_cursor_model_list((proc.stdout or "") + "\n" + (proc.stderr or ""))


def list_agy_models(timeout: float = 20.0) -> List[Tuple[str, str]]:
    """Ask the Antigravity CLI which models this account can use."""
    try:
        proc = subprocess.run(
            ["agy", "models"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return []
    return parse_agy_model_list((proc.stdout or "") + "\n" + (proc.stderr or ""))


def list_agent_models(agent_key: str) -> List[ModelChoice]:
    """Grouped model catalog for the selected coding agent."""
    if agent_key in CURSOR_AGENT_KEYS:
        return catalog_cursor_models(list_cursor_models())
    if agent_key in AGY_AGENT_KEYS:
        return catalog_agy_models(list_agy_models())
    return []


def apply_agent_model(spec: AgentSpec, model: Optional[str]) -> AgentSpec:
    """Insert or strip `--model` on Cursor/agy/Claude command templates."""
    cmd = _MODEL_FLAG.sub("", spec.command or "")
    chosen = (model or "").strip()
    work = "" if not chosen or chosen == "auto" else chosen
    if work:
        quoted = shlex.quote(work)
        stripped = cmd.lstrip()
        if spec.name in CURSOR_AGENT_KEYS or stripped.startswith("agent "):
            cmd = re.sub(r"^(\s*agent)\b", rf"\1 --model {quoted}", cmd, count=1)
        elif spec.name in AGY_AGENT_KEYS or stripped.startswith("agy "):
            cmd = re.sub(r"^(\s*agy)\b", rf"\1 --model {quoted}", cmd, count=1)
        elif spec.name == "claude" or stripped.startswith("claude "):
            cmd = re.sub(r"^(\s*claude)\b", rf"\1 --model {quoted}", cmd, count=1)
    return AgentSpec(
        mode=spec.mode,
        command=cmd,
        name=spec.name,
        model=work,
        plan_model=spec.plan_model,
    )


def attach_agent_models(
    spec: AgentSpec,
    model: Optional[str] = None,
    plan_model: Optional[str] = None,
) -> AgentSpec:
    """Set the work model on the command and remember the plan model separately."""
    work = (model if model is not None else spec.model) or ""
    plan = (plan_model if plan_model is not None else spec.plan_model) or ""
    updated = apply_agent_model(spec, work)
    updated.plan_model = "" if plan.strip() == "auto" else plan.strip()
    return updated


def resolve_agent(
    agent: Optional[str] = None,
    mode: Optional[str] = None,
    command: Optional[str] = None,
    config: Optional[DocFlowConfig] = None,
    model: Optional[str] = None,
    plan_model: Optional[str] = None,
) -> Optional[AgentSpec]:
    """Resolve agent execution from flags, then saved config. Returns None if unset."""
    spec: Optional[AgentSpec] = None
    if command:
        spec = AgentSpec(mode="shell", command=command, name="custom")
    elif agent:
        name = agent.lower()
        if name == "manual":
            spec = AgentSpec(mode="manual", command="", name="manual")
        elif name != "custom":
            cmd = AGENT_PRESETS.get(name, f"{agent} {{prompt_file}}")
            spec = AgentSpec(mode="shell", command=cmd, name=name)
    elif mode:
        if mode == "manual":
            spec = AgentSpec(mode="manual", command="", name="manual")
        else:
            cfg_cmd = (config.agent.command if config else "") or AGENT_PRESETS["agy"]
            spec = AgentSpec(mode=mode, command=cfg_cmd, name=infer_agent_name(config) or "shell")
    elif config and config.source_path:
        name = infer_agent_name(config)
        saved_mode = (config.agent.mode or "manual").lower()
        if name == "manual" or saved_mode == "manual":
            spec = AgentSpec(mode="manual", command="", name="manual")
        elif name == "custom":
            spec = AgentSpec(mode="shell", command=config.agent.command or "", name="custom")
        elif name and name in AGENT_PRESETS:
            spec = AgentSpec(mode="shell", command=AGENT_PRESETS[name], name=name)
        else:
            spec = AgentSpec(
                mode=saved_mode if saved_mode in {"shell", "manual"} else "shell",
                command=config.agent.command or AGENT_PRESETS["agy"],
                name=name or "saved",
            )
        if not model:
            model = infer_agent_model(config)
        if not plan_model:
            plan_model = infer_plan_model(config)
    if spec is None:
        return None
    return attach_agent_models(spec, model=model or spec.model, plan_model=plan_model or spec.plan_model)


def resolve_paths(
    repo: Optional[str] = None,
    docs: Optional[str] = None,
    require: bool = True,
    use_last: bool = True,
) -> ResolvedPaths:
    docs_repo_path = ""
    if docs:
        docs_repo_path = os.path.abspath(docs)
    elif repo:
        entry = find_by_app(repo)
        if entry:
            docs_repo_path = entry.docs_path
        else:
            docs_repo_path = docs_repo_from_cwd()
    else:
        docs_repo_path = docs_repo_from_cwd()
        if not docs_repo_path and use_last:
            entry = last_usable_project()
            if entry:
                docs_repo_path = entry.docs_path

    if not docs_repo_path:
        if require:
            raise ConfigError(
                "No DocFlow project is selected. Run `docflow init` or "
                "`docflow projects`, or pass --docs / --repo."
            )
        return ResolvedPaths(app_repo_path="", docs_repo_path="", config=DocFlowConfig())

    config = DocFlowConfig.load(docs_repo_path=docs_repo_path)
    app_repo_path = ""
    if repo:
        app_repo_path = os.path.abspath(repo)
    elif config.app.repo_path:
        app_repo_path = os.path.abspath(config.app.repo_path)
    else:
        indexed = find_by_docs(docs_repo_path)
        if indexed and indexed.app_path:
            app_repo_path = indexed.app_path

    if require and not app_repo_path:
        raise ConfigError(
            "Application repo is not set in the docs `.docflow/config.yml`. "
            "Run `docflow init` or pass --repo."
        )
    if require and not is_initialized(docs_repo_path):
        raise ConfigError(
            f"Docs folder is not a DocFlow project: {docs_repo_path}. "
            "Run `docflow init` or `docflow projects add --docs PATH`."
        )

    return ResolvedPaths(
        app_repo_path=app_repo_path,
        docs_repo_path=docs_repo_path,
        config=config,
    )


def save_project_config(config: DocFlowConfig, app_repo_path: str, docs_repo_path: str) -> List[str]:
    saved = [config.save(docs_repo_path)]
    register_project(docs_repo_path, app_repo_path, config.project.name)
    return saved


def _conventions_text(docs_repo_path: str, copy_from_package: bool = False) -> str:
    dest = write_conventions_path(docs_repo_path) if copy_from_package else find_conventions_path(docs_repo_path)
    src = os.path.join(project_root(), "CONVENTIONS.md")
    if copy_from_package and os.path.exists(src):
        os.makedirs(os.path.dirname(write_conventions_path(docs_repo_path)), exist_ok=True)
        shutil.copy(src, write_conventions_path(docs_repo_path))
        with open(src, "r", encoding="utf-8") as f:
            return f.read()
    existing = dest if dest and os.path.exists(dest) else find_conventions_path(docs_repo_path)
    if existing and os.path.exists(existing):
        with open(existing, "r", encoding="utf-8") as f:
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


def default_app_branch(app_repo_path: str) -> str:
    """Prefer main, then master, then develop, then the current checkout."""
    if not app_repo_path or not os.path.isdir(app_repo_path):
        return "HEAD"
    try:
        names = list_app_branches(app_repo_path)
    except Exception:
        names = []
    by_lower = {name.lower(): name for name in names}
    for want in PREFERRED_APP_BRANCHES:
        if want in by_lower:
            return by_lower[want]
    current = _current_branch(app_repo_path)
    if current and current != "HEAD":
        return current
    return names[0] if names else "HEAD"


def resolve_branch_rev(app_repo_path: str, branch: str) -> str:
    """Map a branch name to a local rev, falling back to origin/<name>."""
    name = (branch or "").strip() or "HEAD"
    if name == "HEAD":
        return "HEAD"
    if _rev_exists(app_repo_path, name):
        return name
    remote = f"origin/{name}"
    if _rev_exists(app_repo_path, remote):
        return remote
    return name


def infer_app_branch(config: Optional[DocFlowConfig], app_repo_path: str = "") -> str:
    saved = ""
    if config is not None:
        saved = (getattr(config.app, "branch", None) or "").strip()
    if saved:
        if not app_repo_path:
            return saved
        if _rev_exists(app_repo_path, saved) or _rev_exists(app_repo_path, f"origin/{saved}"):
            return saved
    if app_repo_path:
        return default_app_branch(app_repo_path)
    return saved or "HEAD"


def merge_base_sha(app_repo_path: str, a: str, b: str) -> str:
    try:
        proc = _git(app_repo_path, ["merge-base", a, b])
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if proc.returncode != 0:
        return ""
    return (proc.stdout or "").strip()


def documented_unit_names(config: DocFlowConfig, docs_repo_path: str) -> set:
    names: set = set()
    if config.generation.features:
        names.update(config.generation.features)
    for extra in config.generation.extra_features or []:
        if extra.name:
            names.add(extra.name)
    for doc_type in config.docs.types:
        folder = os.path.join(docs_repo_path, doc_type.name)
        if not os.path.isdir(folder):
            continue
        if doc_type.name == "architecture":
            names.add("architecture")
            continue
        for entry in os.listdir(folder):
            if os.path.isdir(os.path.join(folder, entry)):
                names.add(entry)
    return names


def list_commits_in_range(app_repo_path: str, base_ref: str, head_ref: str) -> List[CommitInfo]:
    analyzer = GitAnalyzer(app_repo_path)
    return [CommitInfo(**row) for row in analyzer.commits_between(base_ref, head_ref)]


def _state_path(docs_repo_path: str) -> str:
    path = write_state_path(docs_repo_path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return path


def load_generate_cursor(docs_repo_path: str) -> Optional[GenerateCursor]:
    path = find_state_path(docs_repo_path)
    if not path or not os.path.isfile(path):
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


def _git_env() -> dict:
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


def _git(app_repo_path: str, args: Sequence[str], timeout: float = 120) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", app_repo_path, *args],
        capture_output=True,
        text=True,
        env=_git_env(),
        timeout=timeout,
    )


def _git_text(proc: subprocess.CompletedProcess) -> str:
    return ((proc.stdout or "") + (proc.stderr or "")).strip()


def _has_remotes(app_repo_path: str) -> bool:
    try:
        proc = _git(app_repo_path, ["remote"])
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0 and bool((proc.stdout or "").strip())


def _rev_exists(app_repo_path: str, rev: str) -> bool:
    if not rev:
        return False
    try:
        proc = _git(app_repo_path, ["rev-parse", "--verify", rev])
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0


def _current_branch(app_repo_path: str) -> str:
    try:
        proc = _git(app_repo_path, ["rev-parse", "--abbrev-ref", "HEAD"])
    except (OSError, subprocess.TimeoutExpired):
        return "HEAD"
    name = (proc.stdout or "").strip()
    return name or "HEAD"


def _remote_tip(app_repo_path: str, rev: str = "HEAD") -> Optional[str]:
    """Best remote tracking ref for this checkout or named branch, after fetch."""
    candidates: List[str] = []
    local = (rev or "HEAD").strip() or "HEAD"
    if local in ("HEAD", _current_branch(app_repo_path)):
        try:
            upstream = _git(app_repo_path, ["rev-parse", "--abbrev-ref", "@{upstream}"])
        except (OSError, subprocess.TimeoutExpired):
            upstream = None
        if upstream and upstream.returncode == 0:
            name = (upstream.stdout or "").strip()
            if name:
                candidates.append(name)
        candidates.extend(["origin/HEAD", "origin/main", "origin/master"])
    else:
        candidates.append(f"origin/{local}")
    for name in candidates:
        if _rev_exists(app_repo_path, name):
            return name
    return None


def _commits_not_in_local(app_repo_path: str, local_rev: str, remote_rev: str) -> int:
    try:
        proc = _git(app_repo_path, ["rev-list", "--count", f"{local_rev}..{remote_rev}"])
    except (OSError, subprocess.TimeoutExpired):
        return 0
    if proc.returncode != 0:
        return 0
    try:
        return int((proc.stdout or "0").strip() or "0")
    except ValueError:
        return 0


def fetch_app_repo(
    app_repo_path: str,
    on_progress: Optional[Callable[[str], None]] = None,
) -> Tuple[bool, str]:
    """Update remote-tracking refs. False if there is no remote or fetch failed."""
    app_repo_path = os.path.abspath(app_repo_path)
    if not _has_remotes(app_repo_path):
        return False, ""
    if on_progress:
        on_progress(f"git fetch in {app_repo_path}…")
    try:
        proc = _git(app_repo_path, ["fetch", "--all", "--prune"])
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    output = _git_text(proc)
    if on_progress and output:
        on_progress(output)
    return proc.returncode == 0, output


def ensure_app_repo_current(
    app_repo_path: str,
    docs_repo_path: str = "",
    rev: str = "HEAD",
    on_progress: Optional[Callable[[str], None]] = None,
) -> PullResult:
    """Fetch remotes and fast-forward the working branch when origin is ahead."""
    app_repo_path = os.path.abspath(app_repo_path)
    fetched, fetch_out = fetch_app_repo(app_repo_path, on_progress=on_progress)
    if not fetched:
        return PullResult(
            success=True,
            output=fetch_out,
            app_repo_path=app_repo_path,
            already_up_to_date=True,
        )
    local_rev = (rev or "HEAD").strip() or "HEAD"
    remote_tip = _remote_tip(app_repo_path, local_rev)
    if not remote_tip:
        return PullResult(
            success=True,
            output=fetch_out,
            app_repo_path=app_repo_path,
            already_up_to_date=True,
        )
    ahead = _commits_not_in_local(app_repo_path, local_rev, remote_tip)
    if ahead <= 0:
        return PullResult(
            success=True,
            output=fetch_out,
            app_repo_path=app_repo_path,
            already_up_to_date=True,
        )
    if on_progress:
        on_progress(f"Remote is {ahead} commit(s) ahead. Fast-forwarding to {remote_tip}…")
    current = _current_branch(app_repo_path)
    on_current = local_rev in ("HEAD", "", current)
    try:
        if on_current:
            proc = _git(app_repo_path, ["merge", "--ff-only", remote_tip])
        else:
            ancestor = _git(app_repo_path, ["merge-base", "--is-ancestor", local_rev, remote_tip])
            if ancestor.returncode != 0:
                msg = f"Local {local_rev} has diverged from {remote_tip}; using local commits."
                if on_progress:
                    on_progress(msg)
                return PullResult(
                    success=True,
                    output=msg,
                    app_repo_path=app_repo_path,
                    already_up_to_date=True,
                )
            proc = _git(app_repo_path, ["update-ref", f"refs/heads/{local_rev}", remote_tip])
    except (OSError, subprocess.TimeoutExpired) as exc:
        return PullResult(success=False, output=str(exc), app_repo_path=app_repo_path)
    output = "\n".join(part for part in (fetch_out, _git_text(proc)) if part)
    success = proc.returncode == 0
    if on_progress and _git_text(proc):
        on_progress(_git_text(proc))
    new_commits: List[CommitInfo] = []
    last = None
    cursor = load_generate_cursor(docs_repo_path) if docs_repo_path else None
    if cursor:
        last = CommitInfo(sha=cursor.head_sha, short_sha=cursor.short_sha, message=cursor.message)
    if success and docs_repo_path:
        _, new_commits, _ = new_commits_since(app_repo_path, docs_repo_path, rev=local_rev)
    return PullResult(
        success=success,
        output=output,
        app_repo_path=app_repo_path,
        new_commits=new_commits,
        last_documented=last,
        already_up_to_date=False,
    )


def pull_app_repo(
    app_repo_path: str,
    docs_repo_path: str = "",
    on_progress: Optional[Callable[[str], None]] = None,
) -> PullResult:
    app_repo_path = os.path.abspath(app_repo_path)
    if on_progress:
        on_progress(f"git pull in {app_repo_path}…")
    try:
        proc = _git(app_repo_path, ["pull"])
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
        cfg = DocFlowConfig.load(docs_repo_path=docs_repo_path)
        rev = resolve_branch_rev(app_repo_path, infer_app_branch(cfg, app_repo_path))
        _, new_commits, _ = new_commits_since(app_repo_path, docs_repo_path, rev=rev)
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
_IMPORT_PROGRESS_EVERY = 10


def _job_concurrency(config: DocFlowConfig, override: Optional[int] = None) -> int:
    if override is not None:
        return clamp_concurrency(override, 1)
    raw = os.getenv("DOCFLOW_JOBS")
    if raw not in (None, ""):
        return default_concurrency()
    return clamp_concurrency(config.generation.concurrency, 1)


def _agent_capture(concurrency: int, capture_output: bool) -> Optional[bool]:
    if concurrency > 1:
        return True
    return True if capture_output else None


def _complete_prompt(prompt_file: str, docs_repo_path: str) -> None:
    if not os.path.exists(prompt_file):
        return
    completed_dir = completed_prompts_dir(docs_repo_path)
    os.makedirs(completed_dir, exist_ok=True)
    shutil.move(prompt_file, os.path.join(completed_dir, os.path.basename(prompt_file)))


def _run_shell_jobs(
    runner: AgentRunner,
    specs: Sequence[Tuple[str, str, str]],
    docs_repo_path: str,
    concurrency: int,
    capture_output: bool,
    on_progress: Optional[Callable[[str], None]],
    run_control: Optional[RunControl] = None,
) -> List[FeatureRunResult]:
    """Run agent jobs in parallel. specs are (result_name, prompt_file, stored_prompt_path)."""
    capture = _agent_capture(concurrency, capture_output)
    on_output = on_progress if concurrency <= 1 else None

    def make_run(result_name: str, prompt_file: str, stored_prompt: str):
        def run() -> FeatureRunResult:
            res = runner.run(
                prompt_file,
                docs_repo_path,
                capture=capture,
                on_output=on_output,
            )
            if res.success:
                _complete_prompt(prompt_file, docs_repo_path)
            return FeatureRunResult(
                feature_name=result_name,
                prompt_file=stored_prompt,
                success=res.success,
                error_message=res.error_message,
                output_log=res.output_log or "",
            )

        return run

    job_objs = [Job(key=name, run=make_run(name, prompt_file, stored)) for name, prompt_file, stored in specs]
    raw = run_jobs(job_objs, concurrency=concurrency, on_progress=on_progress, run_control=run_control)
    results: List[FeatureRunResult] = []
    for (name, _prompt_file, stored), result in zip(specs, raw):
        if isinstance(result, FeatureRunResult):
            results.append(result)
        else:
            results.append(
                FeatureRunResult(
                    feature_name=name,
                    prompt_file=stored,
                    success=False,
                    error_message="job failed",
                )
            )
    return results


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
    if on_progress:
        on_progress(f"Importing from {src}")
    copied: List[str] = []
    skipped: List[str] = []
    considered = 0

    def maybe_count_progress() -> None:
        if on_progress and considered and considered % _IMPORT_PROGRESS_EVERY == 0:
            on_progress(f"Importing… {len(copied)} copied, {len(skipped)} skipped")

    def consider(full_path: str, rel: str) -> None:
        nonlocal considered
        if Path(full_path).suffix.lower() not in _IMPORT_EXTS and Path(full_path).name.upper() != "README":
            return
        dest = os.path.join(dest_root, rel)
        if os.path.exists(dest):
            skipped.append(rel)
            considered += 1
            maybe_count_progress()
            return
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.copy2(full_path, dest)
        copied.append(rel)
        considered += 1
        maybe_count_progress()

    if os.path.isfile(src):
        consider(src, os.path.basename(src))
    else:
        for root, dirs, files in os.walk(src):
            dirs[:] = [d for d in dirs if d not in {".git", "node_modules", ".venv", "__pycache__"}]
            for name in files:
                full = os.path.join(root, name)
                rel = os.path.relpath(full, src)
                consider(full, rel)

    if on_progress and considered and considered % _IMPORT_PROGRESS_EVERY != 0:
        on_progress(f"Importing… {len(copied)} copied, {len(skipped)} skipped")

    type_added = False
    cfg = DocFlowConfig.load(docs_repo_path)
    if cfg.source_path:
        existing = list(cfg.docs.types) if cfg.docs.types else list(DEFAULT_DOC_TYPES)
        if dest_type not in {t.name for t in existing}:
            existing.append(DocTypeSettings(name=dest_type, description="Imported existing documentation"))
            cfg.docs.types = existing
            try:
                cfg.save(docs_repo_path)
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
    names: List[str] = []
    for prompts_dir in prompt_search_dirs(docs_repo_path):
        if not os.path.isdir(prompts_dir):
            continue
        names.extend(f for f in os.listdir(prompts_dir) if f.endswith(".md"))
    return sorted(set(names))


def get_dashboard(
    repo: Optional[str] = None,
    docs: Optional[str] = None,
    use_last: bool = True,
) -> Dashboard:
    paths = resolve_paths(repo, docs, require=False, use_last=use_last)
    app_path = paths.app_repo_path
    docs_path = paths.docs_repo_path
    cfg = paths.config
    features = list_features(docs_path) if docs_path else []
    pending = list_pending_prompts(docs_path) if docs_path else []
    types = configured_types(cfg)
    last_documented = None
    new_commits: List[CommitInfo] = []
    app_branch = infer_app_branch(cfg, app_path)
    if app_path and docs_path and os.path.isdir(app_path):
        try:
            rev = resolve_branch_rev(app_path, app_branch)
            cursor, new_commits, _stale = new_commits_since(app_path, docs_path, rev=rev)
            if cursor:
                last_documented = CommitInfo(
                    sha=cursor.head_sha,
                    short_sha=cursor.short_sha,
                    message=cursor.message,
                )
        except Exception:
            last_documented = None
            new_commits = []
    project_name = (cfg.project.name or "").strip()
    if not project_name or project_name == "Project":
        indexed = find_by_docs(docs_path) if docs_path else None
        if indexed and indexed.name:
            project_name = indexed.name
        elif app_path:
            project_name = os.path.basename(app_path.rstrip(os.sep)) or project_name
        elif docs_path:
            project_name = os.path.basename(docs_path.rstrip(os.sep)) or project_name
    return Dashboard(
        project_name=project_name or "Project",
        app_repo_path=app_path,
        docs_repo_path=docs_path,
        app_exists=bool(app_path and os.path.exists(app_path)),
        docs_exists=bool(docs_path and os.path.exists(docs_path)),
        agent_mode=cfg.agent.mode,
        agent_command=cfg.agent.command,
        platform=cfg.platform.type,
        features=features,
        pending=pending,
        configured=is_configured(cfg) or bool(app_path and docs_path and is_initialized(docs_path)),
        source_path=cfg.source_path,
        doc_types=[f"{t.name}: {t.description}" if t.description else t.name for t in types],
        last_documented=last_documented,
        new_commits=new_commits,
        concurrency=clamp_concurrency(cfg.generation.concurrency, 1),
        agent_name=infer_agent_name(cfg),
        agent_model=infer_agent_model(cfg),
        plan_model=infer_plan_model(cfg),
        app_branch=app_branch,
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
    concurrency: Optional[int] = None,
    on_review_sections: Optional[Callable[[List[SectionCandidate]], Optional[List[SectionCandidate]]]] = None,
    include_sections: Optional[Sequence[str]] = None,
    exclude_sections: Optional[Sequence[str]] = None,
    extra_sections: Optional[Sequence[str]] = None,
    run_control: Optional[RunControl] = None,
    branch: str = "",
) -> InitResult:
    def progress(message: str) -> None:
        if on_progress:
            on_progress(message)

    app_repo_path = os.path.abspath(app_repo_path)
    docs_repo_path = os.path.abspath(docs_repo_path)
    assert_can_init(docs_repo_path)

    config = config or DocFlowConfig.load(docs_repo_path=docs_repo_path)
    config.app.repo_path = app_repo_path
    config.docs.repo_path = docs_repo_path
    config.agent.mode = agent.mode
    config.agent.command = agent.command
    remember_agent(config, agent)
    chosen_types = types or configured_types(config)
    if not chosen_types:
        chosen_types = list(DEFAULT_DOC_TYPES)
    config.docs.types = [
        DocTypeSettings(name=slug_type_name(t.name), description=t.description) for t in chosen_types
    ]
    if not config.project.name or config.project.name == "Project":
        config.project.name = os.path.basename(app_repo_path)
    if concurrency is not None:
        config.generation.concurrency = clamp_concurrency(concurrency, 1)
    tracked_branch = (branch or "").strip() or default_app_branch(app_repo_path)
    config.app.branch = tracked_branch

    analyzer = GitAnalyzer(app_repo_path)
    framework_name, ignore_patterns, skip_dirs = _generation_context(app_repo_path, config)
    arch_seeds = architecture_seed_paths(app_repo_path, framework_name)
    builder = PromptBuilder()
    work_model = agent.model or model_from_command(agent.command)
    plan_model = agent.plan_model or work_model
    plan_runner = AgentRunner(
        mode=agent.mode,
        command_template=apply_agent_model(agent, plan_model).command,
    )
    runner = AgentRunner(
        mode=agent.mode,
        command_template=apply_agent_model(agent, work_model).command,
    )
    features: List[FeatureRunResult] = []

    created_docs = not os.path.isdir(docs_repo_path)
    os.makedirs(docs_repo_path, exist_ok=True)
    conventions_text = _conventions_text(docs_repo_path, copy_from_package=True)

    stack_payload = load_stack_file(docs_repo_path)
    tracked_rev = resolve_branch_rev(app_repo_path, tracked_branch)
    if agent.mode == "shell" and not stack_payload:
        progress("Asking the plan model what to document from composer/packages…")
        survey_prompt = pending_prompt_path(docs_repo_path, "init-stack-survey.md")
        survey_context = PromptContext(
            task_type="stack-survey",
            project_name=config.project.name,
            feature_name="stack-survey",
            app_repo_path=app_repo_path,
            docs_repo_path=docs_repo_path,
            conventions_text=conventions_text,
            doc_type="stack",
            doc_type_description="Identify application units and sections to document vs skip.",
            output_dir=".",
            available_sections=[
                {"name": t.name, "description": t.description} for t in config.docs.types
            ],
        )
        builder.save_prompt(survey_context, survey_prompt)
        survey_res = plan_runner.run(
            survey_prompt,
            docs_repo_path,
            capture=True if capture_output else None,
            on_output=on_progress,
        )
        features.append(
            FeatureRunResult(
                feature_name="stack-survey",
                prompt_file=rel_pending_prompt("init-stack-survey.md"),
                success=survey_res.success,
                error_message=survey_res.error_message,
                output_log=survey_res.output_log,
            )
        )
        if survey_res.success:
            progress("Stack survey complete.")
        else:
            progress(f"Stack survey failed: {survey_res.error_message or 'unknown error'}")
        stack_payload = load_stack_file(docs_repo_path)

    candidates = discover_init_sections(
        analyzer,
        config.docs.types,
        ignore_patterns,
        skip_dirs,
        arch_seeds=arch_seeds,
        on_progress=on_progress,
        stack_payload=stack_payload,
        rev=tracked_rev,
    )
    apply_section_filters(candidates, include_sections or (), exclude_sections or ())
    for raw in extra_sections or ():
        if str(raw).strip():
            candidates.append(extra_section_from_entry(analyzer, raw, ignore_patterns, skip_dirs))

    if on_review_sections:
        progress("Waiting for you to confirm the agent's list…")
        reviewed = on_review_sections(candidates)
        if reviewed is None:
            if created_docs:
                shutil.rmtree(docs_repo_path, ignore_errors=True)
            raise InitCancelled("Init cancelled — no docs were written.")
        candidates = list(reviewed)

    expanded: List[SectionCandidate] = []
    for item in candidates:
        if item.extra and not item.file_paths:
            filled = extra_section_from_entry(analyzer, item.name, ignore_patterns, skip_dirs)
            filled.included = item.included
            expanded.append(filled)
        else:
            expanded.append(item)
    candidates = expanded

    chosen = selected_sections(candidates)
    if not chosen:
        if created_docs:
            shutil.rmtree(docs_repo_path, ignore_errors=True)
        raise ConfigError("No items selected to document.")

    type_by_name = {t.name: t for t in config.docs.types}
    kept_types: List[DocTypeSettings] = []
    seen_types = set()
    for item in chosen:
        if item.doc_type in seen_types:
            continue
        seen_types.add(item.doc_type)
        kept_types.append(
            type_by_name.get(item.doc_type)
            or DocTypeSettings(name=item.doc_type, description=item.description)
        )
    config.docs.types = kept_types or list(config.docs.types)
    config.generation.features = [
        item.name for item in chosen if item.doc_type != "architecture"
    ]
    config.generation.extra_features = [
        ExtraFeatureSettings(
            name=item.name,
            paths=list(item.file_paths[:40]),
            doc_type=item.doc_type,
        )
        for item in chosen
        if item.file_paths
    ]

    progress("Creating blank docs skeleton…")
    for doc_type in config.docs.types:
        os.makedirs(os.path.join(docs_repo_path, doc_type.name), exist_ok=True)
    config_paths = save_project_config(config, app_repo_path, docs_repo_path)
    if framework_name:
        _persist_detected_framework(config, framework_name, app_repo_path, docs_repo_path)

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

    doc_guidance = _documentation_guidance(docs_repo_path, framework_name)

    jobs: List[Tuple[str, str, str, FeatureChunk]] = []
    type_desc_by_name = {t.name: t.description for t in config.docs.types}
    for item in chosen:
        chunk = item.to_chunk()
        jobs.append(
            (
                item.doc_type,
                type_desc_by_name.get(item.doc_type, item.description),
                type_output_dir(item.doc_type, item.name),
                chunk,
            )
        )

    total = len(jobs)
    progress(f"Writing init prompts for {total} section(s) with the work model.")
    imported_note = None
    if imported.copied:
        imported_note = "Imported files (do not overwrite):\n" + "\n".join(f"- {p}" for p in imported.copied[:40])

    prepared: List[Tuple[str, str, str]] = []
    for index, (doc_type, type_desc, output_dir, chunk) in enumerate(jobs, start=1):
        feature_name = chunk.feature_name
        rel_prompt_file = rel_pending_prompt(f"init-{doc_type}-{feature_name}.md")
        prompt_file = pending_prompt_path(docs_repo_path, f"init-{doc_type}-{feature_name}.md")
        context = PromptContext(
            task_type="init",
            project_name=config.project.name,
            feature_name=feature_name,
            app_repo_path=app_repo_path,
            docs_repo_path=docs_repo_path,
            feature_chunk=chunk,
            existing_index_md=imported_note if doc_type == imported.dest_type else None,
            conventions_text=conventions_text,
            doc_type=doc_type,
            doc_type_description=type_desc,
            output_dir=output_dir,
            extra_instructions=doc_guidance,
        )
        progress(f"[{index}/{total}] Writing prompt for {doc_type}/{feature_name}…")
        builder.save_prompt(context, prompt_file)
        result_name = f"{doc_type}/{feature_name}"
        if agent.mode == "shell":
            prepared.append((result_name, prompt_file, rel_prompt_file))
            continue
        res = runner.run(
            prompt_file,
            docs_repo_path,
            capture=True if capture_output else None,
            on_output=on_progress,
        )
        if res.success:
            progress(f"[{index}/{total}] ✓ {doc_type}/{feature_name}")
        else:
            progress(f"[{index}/{total}] ✗ {doc_type}/{feature_name}: {res.error_message}")
        features.append(
            FeatureRunResult(
                feature_name=result_name,
                prompt_file=rel_prompt_file,
                success=res.success,
                error_message=res.error_message,
                output_log=res.output_log,
            )
        )

    if agent.mode == "shell" and prepared:
        workers = _job_concurrency(config, concurrency)
        features = features + _run_shell_jobs(
            runner,
            prepared,
            docs_repo_path,
            concurrency=workers,
            capture_output=capture_output,
            on_progress=on_progress,
            run_control=run_control,
        )

    progress("Generating llms.txt…")
    LLMSTxtGenerator(docs_repo_path).generate(config.project.name)
    try:
        mark_repo_documented(app_repo_path, docs_repo_path, rev=tracked_rev)
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
        pending_dir=pending_prompts_dir(docs_repo_path),
        config_paths=config_paths,
        imported_copied=imported.copied,
        imported_skipped=imported.skipped,
        types=[t.name for t in config.docs.types],
    )


def _merge_section_into_config(config: DocFlowConfig, item: SectionCandidate) -> None:
    features = list(config.generation.features or [])
    if item.doc_type != "architecture" and item.name not in features:
        features.append(item.name)
        config.generation.features = features
    extras = list(config.generation.extra_features or [])
    if item.file_paths and not any(extra.name == item.name for extra in extras):
        extras.append(
            ExtraFeatureSettings(
                name=item.name,
                paths=list(item.file_paths[:40]),
                doc_type=item.doc_type,
            )
        )
        config.generation.extra_features = extras
    type_names = {t.name for t in config.docs.types}
    if item.doc_type and item.doc_type not in type_names:
        config.docs.types.append(DocTypeSettings(name=item.doc_type, description=item.description))


def _run_init_jobs_for_sections(
    chosen: Sequence[SectionCandidate],
    config: DocFlowConfig,
    app_repo_path: str,
    docs_repo_path: str,
    agent: AgentSpec,
    capture_output: bool,
    on_progress: Optional[Callable[[str], None]],
    concurrency: Optional[int],
    run_control: Optional[RunControl],
    extra_instructions: str = "",
    conventions_text: str = "",
) -> List[FeatureRunResult]:
    builder = PromptBuilder()
    work_model = agent.model or model_from_command(agent.command)
    runner = AgentRunner(
        mode=agent.mode,
        command_template=apply_agent_model(agent, work_model).command,
    )
    type_desc_by_name = {t.name: t.description for t in config.docs.types}
    prepared: List[Tuple[str, str, str]] = []
    features: List[FeatureRunResult] = []
    total = len(chosen)
    for index, item in enumerate(chosen, start=1):
        chunk = item.to_chunk()
        output_dir = type_output_dir(item.doc_type, item.name)
        os.makedirs(os.path.join(docs_repo_path, output_dir), exist_ok=True)
        feature_name = chunk.feature_name
        rel_prompt_file = rel_pending_prompt(f"init-{item.doc_type}-{feature_name}.md")
        prompt_file = pending_prompt_path(docs_repo_path, f"init-{item.doc_type}-{feature_name}.md")
        context = PromptContext(
            task_type="init",
            project_name=config.project.name,
            feature_name=feature_name,
            app_repo_path=app_repo_path,
            docs_repo_path=docs_repo_path,
            feature_chunk=chunk,
            conventions_text=conventions_text,
            doc_type=item.doc_type,
            doc_type_description=type_desc_by_name.get(item.doc_type, item.description),
            output_dir=output_dir,
            extra_instructions=extra_instructions,
        )
        if on_progress:
            on_progress(f"[{index}/{total}] Writing prompt for {item.doc_type}/{feature_name}…")
        builder.save_prompt(context, prompt_file)
        result_name = f"{item.doc_type}/{feature_name}"
        if agent.mode == "shell":
            prepared.append((result_name, prompt_file, rel_prompt_file))
            continue
        res = runner.run(
            prompt_file,
            docs_repo_path,
            capture=True if capture_output else None,
            on_output=on_progress,
        )
        features.append(
            FeatureRunResult(
                feature_name=result_name,
                prompt_file=rel_prompt_file,
                success=res.success,
                error_message=res.error_message,
                output_log=res.output_log,
            )
        )
    if agent.mode == "shell" and prepared:
        workers = _job_concurrency(config, concurrency)
        features = features + _run_shell_jobs(
            runner,
            prepared,
            docs_repo_path,
            concurrency=workers,
            capture_output=capture_output,
            on_progress=on_progress,
            run_control=run_control,
        )
    return features


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
    sync_remote: bool = True,
    concurrency: Optional[int] = None,
    run_control: Optional[RunControl] = None,
    app_branch: str = "",
    on_review_sections: Optional[Callable[[List[SectionCandidate]], Optional[List[SectionCandidate]]]] = None,
) -> GenerateResult:
    app_repo_path = os.path.abspath(app_repo_path)
    docs_repo_path = os.path.abspath(docs_repo_path)
    config = config or DocFlowConfig.load(docs_repo_path=docs_repo_path)
    remember_agent(config, agent)
    previous_branch = (config.app.branch or "").strip()
    tracked = (app_branch or "").strip() or infer_app_branch(config, app_repo_path)
    branch_changed = bool(previous_branch and tracked and previous_branch != tracked)
    config.app.branch = tracked
    if concurrency is not None:
        config.generation.concurrency = clamp_concurrency(concurrency, 1)
    save_project_config(config, app_repo_path, docs_repo_path)

    is_full = full
    included_count = commit_count or 1
    synced_remote = False
    tracked_rev = resolve_branch_rev(app_repo_path, tracked)
    sync_rev = branch or tracked_rev
    if sync_remote and not from_ref and not to_ref:
        sync = ensure_app_repo_current(
            app_repo_path,
            docs_repo_path,
            rev=sync_rev,
            on_progress=on_progress,
        )
        synced_remote = bool(sync.success and not sync.already_up_to_date)
        if not sync.success and on_progress:
            on_progress(f"Could not update from remote: {sync.output or 'unknown error'}. Using local commits.")

    analyzer = GitAnalyzer(app_repo_path)
    framework_name, ignore_patterns, skip_dirs = _generation_context(app_repo_path, config)
    conventions_text = _conventions_text(docs_repo_path)
    doc_guidance = _documentation_guidance(docs_repo_path, framework_name)
    new_item_names: List[str] = []
    new_item_runs: List[FeatureRunResult] = []
    if branch_changed and not feature and not from_ref and not to_ref:
        if on_progress:
            on_progress(f"Application branch is now {tracked}. Checking for new items…")
        stack_payload = load_stack_file(docs_repo_path)
        arch_seeds = architecture_seed_paths(app_repo_path, framework_name)
        fresh = discover_init_sections(
            analyzer,
            config.docs.types,
            ignore_patterns,
            skip_dirs,
            arch_seeds=arch_seeds,
            on_progress=on_progress,
            stack_payload=stack_payload,
            rev=tracked_rev,
        )
        documented = documented_unit_names(config, docs_repo_path)
        candidates = [
            item
            for item in fresh
            if item.name not in documented
            and not (item.doc_type == "architecture" and "architecture" in documented)
        ]
        for item in candidates:
            item.included = suggested_section_included(item.name, kind=item.kind)
        if candidates and on_review_sections:
            if on_progress:
                on_progress("Waiting for you to confirm new items…")
            reviewed = on_review_sections(candidates)
            candidates = selected_sections(reviewed) if reviewed else []
        elif candidates:
            candidates = selected_sections(candidates)
        if candidates:
            for item in candidates:
                _merge_section_into_config(config, item)
            save_project_config(config, app_repo_path, docs_repo_path)
            new_item_names = [item.name for item in candidates]
            new_item_runs = _run_init_jobs_for_sections(
                candidates,
                config,
                app_repo_path,
                docs_repo_path,
                agent,
                capture_output=capture_output,
                on_progress=on_progress,
                concurrency=concurrency,
                run_control=run_control,
                extra_instructions=doc_guidance,
                conventions_text=conventions_text,
            )

    used_cursor = False
    watermark_stale = False
    explicit_range = bool(from_ref or to_ref or is_full or commit_count is not None or branch)
    if not is_full and not from_ref and not to_ref:
        rev = tracked_rev if not branch else resolve_branch_rev(app_repo_path, branch)
        if not explicit_range:
            cursor = load_generate_cursor(docs_repo_path)
            if cursor and analyzer.is_ancestor(cursor.head_sha, rev):
                new_rows = analyzer.commits_between(cursor.head_sha, rev)
                if not new_rows:
                    if on_progress:
                        on_progress(
                            f"Already documented through {cursor.short_sha} {cursor.message}. "
                            "Pass --commits / --full to regenerate."
                        )
                    if new_item_runs:
                        try:
                            mark_repo_documented(app_repo_path, docs_repo_path, rev=rev)
                        except Exception:
                            pass
                        return GenerateResult(
                            app_repo_path=app_repo_path,
                            docs_repo_path=docs_repo_path,
                            agent_mode=agent.mode,
                            agent_command=agent.command,
                            is_full=False,
                            base_ref=cursor.head_sha,
                            head_ref=rev,
                            task_type="init",
                            feature_name=", ".join(new_item_names),
                            prompt_file="",
                            no_changes=False,
                            already_current=False,
                            used_cursor=True,
                            commit_count=0,
                            synced_remote=synced_remote,
                            features=new_item_runs,
                            app_branch=tracked,
                            new_items=new_item_names,
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
                        synced_remote=synced_remote,
                        app_branch=tracked,
                    )
                from_ref = cursor.head_sha
                to_ref = analyzer.head_commit(rev)["sha"] if analyzer.head_commit(rev) else rev
                included_count = len(new_rows)
                used_cursor = True
            else:
                if cursor:
                    watermark_stale = True
                    base = merge_base_sha(app_repo_path, cursor.head_sha, rev) if branch_changed else ""
                    if base:
                        if on_progress:
                            on_progress(
                                f"Branch changed to {tracked}. Updating from the common ancestor."
                            )
                        from_ref = base
                        to_ref = analyzer.head_commit(rev)["sha"] if analyzer.head_commit(rev) else rev
                    else:
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
    head_ref = to_ref or branch or tracked_rev
    conventions_text = _conventions_text(docs_repo_path)

    manifest = analyzer.extract_diff(
        base_ref=base_ref,
        head_ref=head_ref,
        full_diff_threshold=config.generation.full_diff_threshold,
        ignore_patterns=ignore_patterns,
    )
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
        if new_item_runs:
            return GenerateResult(
                app_repo_path=app_repo_path,
                docs_repo_path=docs_repo_path,
                agent_mode=agent.mode,
                agent_command=agent.command,
                is_full=is_full,
                base_ref=base_ref,
                head_ref=head_ref,
                task_type="init",
                feature_name=", ".join(new_item_names),
                prompt_file="",
                no_changes=False,
                commits=included_commits,
                commit_count=included_count,
                used_cursor=used_cursor,
                watermark_stale=watermark_stale,
                synced_remote=synced_remote,
                features=new_item_runs,
                app_branch=tracked,
                new_items=new_item_names,
            )
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
            synced_remote=synced_remote,
            app_branch=tracked,
            new_items=new_item_names,
        )

    section_names = generate_section_names(
        manifest.changed_files, feature, skip_as_feature=skip_dirs, config=config
    )
    allowed = allowed_feature_names(config)
    if allowed and not feature:
        filtered = [name for name in section_names if name in allowed]
        if not filtered and is_full:
            filtered = list(config.generation.features or [])
        section_names = filtered
        if not section_names and not is_full:
            try:
                mark_repo_documented(app_repo_path, docs_repo_path, included_commits, head_ref)
            except Exception:
                pass
            if new_item_runs:
                return GenerateResult(
                    app_repo_path=app_repo_path,
                    docs_repo_path=docs_repo_path,
                    agent_mode=agent.mode,
                    agent_command=agent.command,
                    is_full=is_full,
                    base_ref=base_ref,
                    head_ref=head_ref,
                    task_type="init",
                    feature_name=", ".join(new_item_names),
                    prompt_file="",
                    no_changes=False,
                    commits=included_commits,
                    commit_count=included_count,
                    used_cursor=used_cursor,
                    watermark_stale=watermark_stale,
                    synced_remote=synced_remote,
                    features=new_item_runs,
                    app_branch=tracked,
                    new_items=new_item_names,
                )
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
                synced_remote=synced_remote,
            )
    task_type = "full-regen" if is_full else "update"
    builder = PromptBuilder()
    runner = AgentRunner(mode=agent.mode, command_template=agent.command)
    doc_guidance = _documentation_guidance(docs_repo_path, framework_name)
    feature_runs: List[FeatureRunResult] = []
    last_res: Optional[AgentRunResult] = None
    last_prompt = ""
    total = len(section_names)
    prepared: List[Tuple[str, str, str]] = []

    for index, target_feature in enumerate(section_names, start=1):
        section_files = (
            list(manifest.changed_files)
            if feature
            else [
                f for f in manifest.changed_files
                if documented_name_for_path(f.path, config, skip_dirs) == target_feature
            ]
        )
        section_manifest = manifest.model_copy(update={"changed_files": section_files or list(manifest.changed_files)})
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

        context = PromptContext(
            task_type=task_type,
            project_name=config.project.name,
            feature_name=target_feature,
            app_repo_path=app_repo_path,
            docs_repo_path=docs_repo_path,
            change_manifest=section_manifest,
            existing_index_md=existing_index,
            existing_context_json=existing_context,
            conventions_text=conventions_text,
            doc_type=doc_type,
            doc_type_description=type_desc,
            output_dir=output_dir,
            extra_instructions=doc_guidance,
        )
        prompt_file = pending_prompt_path(docs_repo_path, f"{task_type}-{target_feature}.md")
        prefix = f"[{index}/{total}] " if total > 1 else ""
        if on_progress:
            on_progress(f"{prefix}Writing {task_type} prompt for {target_feature}…")
        builder.save_prompt(context, prompt_file)
        if agent.mode == "shell":
            prepared.append((target_feature, prompt_file, prompt_file))
            continue
        res = runner.run(
            prompt_file,
            docs_repo_path,
            capture=True if capture_output else None,
            on_output=on_progress,
        )
        last_res = res
        last_prompt = prompt_file
        feature_runs.append(
            FeatureRunResult(
                feature_name=target_feature,
                prompt_file=prompt_file,
                success=res.success,
                error_message=res.error_message,
                output_log=res.output_log or "",
            )
        )

    if agent.mode == "shell" and prepared:
        workers = _job_concurrency(config, concurrency)
        feature_runs = _run_shell_jobs(
            runner,
            prepared,
            docs_repo_path,
            concurrency=workers,
            capture_output=capture_output,
            on_progress=on_progress,
            run_control=run_control,
        )
    feature_runs = new_item_runs + feature_runs

    if feature_runs:
        last = feature_runs[-1]
        last_prompt = last.prompt_file
        last_res = AgentRunResult(
            success=last.success,
            mode=agent.mode if agent.mode in ("shell", "manual") else "shell",
            prompt_file_path=last.prompt_file,
            output_log=last.output_log or "",
            error_message=last.error_message,
        )
    all_ok = all(item.success for item in feature_runs)

    if all_ok:
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
        feature_name=", ".join(section_names),
        prompt_file=last_prompt,
        no_changes=False,
        run=last_res,
        commits=included_commits,
        commit_count=included_count,
        used_cursor=used_cursor,
        watermark_stale=watermark_stale,
        features=feature_runs,
        synced_remote=synced_remote,
        app_branch=tracked,
        new_items=new_item_names,
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
            repo_owner_name=git_origin_slug(docs_repo_path),
        )
        result.mr_success = bool(res.get("success"))
        result.mr_url = res.get("mr_url")
        result.mr_message = res.get("error") or res.get("message")
    return result
