"""
Discover application units to document — models, routes, pages, functions —
not CLI glue, folders, or developer tooling (git, GitHub CLI, CI).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import List, Optional, Sequence, Set

from docflow.core.git_analyzer import path_is_ignored, posix_rel

TOOLING_KINDS = {
    "ci", "git", "github", "gitlab", "cli", "tooling", "vendor", "framework",
    "docker", "compose", "npm", "composer",
}

TOOLING_NAME_RE = re.compile(
    r"^(git|github|gitlab|gh|gitea|hub|cli|ci|cd|docker|compose|kubectl|"
    r"artisan|composer|npm|yarn|pnpm|node_modules|vendor)$",
    re.IGNORECASE,
)

APP_KIND_ORDER = [
    "overview",
    "model", "migration",
    "filament-resource", "filament-page",
    "function", "controller", "policy", "job", "service", "action",
    "route",
    "page", "component",
    "other",
]

APP_KINDS = set(APP_KIND_ORDER)

KIND_TO_SECTION = {
    "overview": "architecture",
    "architecture": "architecture",
    "model": "models",
    "migration": "database",
    "filament-resource": "pages",
    "filament-page": "pages",
    "page": "pages",
    "component": "pages",
    "controller": "functions",
    "policy": "functions",
    "job": "functions",
    "service": "functions",
    "action": "functions",
    "function": "functions",
    "route": "routes",
    "other": "functions",
}

_KIND_ALIASES = {
    "module": "function",
    "index": "page",
    "functions": "function",
    "models": "model",
    "routes": "route",
    "pages": "page",
    "database": "migration",
}

_KIND_PATTERNS = (
    ("model", "app/Models", "*.php"),
    ("filament-resource", "app/Filament", "*Resource.php"),
    ("filament-page", "app/Filament", "*Page.php"),
    ("controller", "app/Http/Controllers", "*.php"),
    ("policy", "app/Policies", "*.php"),
    ("job", "app/Jobs", "*.php"),
    ("service", "app/Services", "*.php"),
    ("action", "app/Actions", "*.php"),
    ("page", "resources/js/Pages", "*.vue"),
    ("page", "resources/js/Pages", "*.tsx"),
    ("page", "resources/js/Pages", "*.jsx"),
    ("route", "routes", "*.php"),
    ("migration", "database/migrations", "*.php"),
    ("function", "src", "*.py"),
    ("function", "src", "*.ts"),
    ("function", "src", "*.tsx"),
    ("function", "src", "*.js"),
    ("function", "src", "*.jsx"),
)

_SKIP_NAME_PARTS = {
    "test", "tests", "spec", "vendor", "node_modules", "__pycache__",
    "bootstrap", "storage", "public", "github", "gitlab", "cli",
}

_BOOTSTRAP_TITLES = {
    "main", "menu", "cli", "__init__", "wsgi", "asgi", "manage",
    "kernel", "handler", "bootstrap", "providers", "console",
}

_PAGE_KINDS = {"page", "filament-page", "filament-resource", "model", "route", "migration"}


def normalize_kind(kind: str, is_other: bool = False) -> str:
    raw = (kind or ("other" if is_other else "function")).strip().lower()
    return _KIND_ALIASES.get(raw, raw) or ("other" if is_other else "function")


def is_tooling_item(name: str, kind: str = "", path: str = "") -> bool:
    blob = f"{name} {kind} {path}".lower()
    if (kind or "").lower() in TOOLING_KINDS:
        return True
    if TOOLING_NAME_RE.match((name or "").strip()):
        return True
    if any(part in blob for part in ("github cli", "gitlab cli", "gh cli", ".github", ".gitlab")):
        return True
    posix = posix_rel(path or "")
    parts = {p.lower() for p in Path(posix).parts} if posix else set()
    if parts & {".github", ".gitlab", ".git", "vendor", "node_modules", ".docflow"}:
        return True
    return False


def is_bootstrap_item(name: str, kind: str = "", path: str = "") -> bool:
    """CLI/entrypoint files are not application docs (main.py, menu.py, artisan)."""
    if normalize_kind(kind) in _PAGE_KINDS:
        return False
    stem = (name or "").strip().lower()
    if stem in _BOOTSTRAP_TITLES:
        return True
    posix = posix_rel(path or "")
    parts = [p.lower() for p in Path(posix).parts] if posix else []
    if "cli" in parts:
        return True
    if parts and parts[-1] in {"main.py", "menu.py", "cli.py", "artisan"}:
        return True
    return False


def is_folder_path(repo_path: str, rel: str) -> bool:
    posix = posix_rel(rel)
    if posix.endswith("/"):
        return True
    full = Path(repo_path) / posix
    return full.is_dir() and not full.is_file()


def _title_from_path(rel: str) -> str:
    stem = Path(posix_rel(rel)).stem
    if stem.lower().endswith("controller"):
        stem = stem[: -len("controller")] or stem
    return stem or posix_rel(rel)


def _slug(name: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "-", (name or "").strip()).strip("-").lower()
    return cleaned or "item"


def _unique_name(title: str, used: Set[str]) -> str:
    base = _slug(title)
    name = base
    index = 2
    while name in used:
        name = f"{base}-{index}"
        index += 1
    used.add(name)
    return name


def inventory_app_items(
    repo_path: str,
    ignore_patterns: Optional[Set[str]] = None,
    limit: int = 80,
) -> List[dict]:
    """List individual application files worth documenting."""
    root = Path(repo_path)
    ignore = ignore_patterns or set()
    items: List[dict] = []
    used: Set[str] = set()
    seen_paths: Set[str] = set()

    def consider(rel: str, kind: str) -> None:
        posix = posix_rel(rel).replace("\\", "/")
        if posix in seen_paths or path_is_ignored(posix, ignore):
            return
        parts = {p.lower() for p in Path(posix).parts}
        if parts & _SKIP_NAME_PARTS:
            return
        title = _title_from_path(posix)
        kind = normalize_kind(kind)
        if is_tooling_item(title, kind, posix) or is_bootstrap_item(title, kind, posix):
            return
        if title.lower().endswith("test"):
            return
        seen_paths.add(posix)
        name = _unique_name(title, used)
        items.append(
            {
                "id": name,
                "kind": kind,
                "title": title,
                "path": posix,
                "section": KIND_TO_SECTION.get(kind, "functions"),
                "include": kind in APP_KINDS,
            }
        )

    for kind, directory, pattern in _KIND_PATTERNS:
        if len(items) >= limit:
            break
        base = root / directory
        if not base.is_dir():
            continue
        for path in sorted(base.rglob(pattern)):
            if not path.is_file() or len(items) >= limit:
                continue
            consider(str(path.relative_to(root)), kind)

    return items[:limit]


def stack_items_from_payload(payload: Optional[dict], repo_path: str = "") -> List[dict]:
    """Normalize agent-discovered items; drop folders, tooling, and CLI glue."""
    if not isinstance(payload, dict):
        return []
    rows: List[tuple[dict, bool]] = []
    for entry in payload.get("items") or []:
        if isinstance(entry, dict):
            rows.append((entry, False))
    for entry in payload.get("other_items") or []:
        if isinstance(entry, dict):
            rows.append((entry, True))
    items: List[dict] = []
    used: Set[str] = set()
    for entry, is_other in rows:
        title = str(entry.get("title") or entry.get("name") or entry.get("id") or "").strip()
        path = str(entry.get("path") or "").strip()
        kind = normalize_kind(str(entry.get("kind") or ""), is_other=is_other)
        section = str(entry.get("section") or KIND_TO_SECTION.get(kind, "")).strip()
        if not title and path:
            title = _title_from_path(path)
        if not title:
            continue
        if repo_path and path and is_folder_path(repo_path, path):
            continue
        if is_tooling_item(title, kind, path) or is_bootstrap_item(title, kind, path):
            continue
        name = _unique_name(str(entry.get("id") or title), used)
        include = entry.get("include")
        if include is None:
            include = kind in APP_KINDS
        items.append(
            {
                "id": name,
                "kind": kind,
                "title": title,
                "path": path,
                "section": section,
                "include": bool(include),
            }
        )
    return items


def group_items(items: Sequence[dict]) -> List[tuple[str, List[dict]]]:
    """Group items by kind for compact picker display."""
    buckets: dict[str, List[dict]] = {}
    for item in items:
        buckets.setdefault(item.get("kind") or "function", []).append(item)
    grouped: List[tuple[str, List[dict]]] = []
    for kind in APP_KIND_ORDER:
        if kind in buckets:
            grouped.append((kind, buckets.pop(kind)))
    for kind, rows in sorted(buckets.items()):
        grouped.append((kind, rows))
    return grouped
