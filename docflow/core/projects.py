"""
User-level index of DocFlow docs projects.

Stored at `$XDG_CONFIG_HOME/docflow/projects.yml` (default `~/.config/docflow/projects.yml`).
This is not a project config file and is never written into an application repo.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import List, Optional

import yaml


@dataclass(frozen=True)
class ProjectEntry:
    name: str
    docs_path: str
    app_path: str
    last_opened: str


def index_path() -> str:
    xdg = os.environ.get("XDG_CONFIG_HOME", "").strip()
    if xdg:
        base = os.path.abspath(os.path.expanduser(xdg))
    else:
        base = os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "docflow", "projects.yml")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _abs(path: str) -> str:
    return os.path.abspath(os.path.expanduser(path or ""))


def _is_same_or_inside(path: str, root: str) -> bool:
    if not path or not root:
        return False
    path = _abs(path)
    root = _abs(root)
    if path == root:
        return True
    prefix = root.rstrip(os.sep) + os.sep
    return path.startswith(prefix)


def _entry_from_mapping(raw: object) -> Optional[ProjectEntry]:
    if not isinstance(raw, dict):
        return None
    docs_path = str(raw.get("docs_path") or "").strip()
    if not docs_path:
        return None
    return ProjectEntry(
        name=str(raw.get("name") or os.path.basename(docs_path)),
        docs_path=_abs(docs_path),
        app_path=_abs(str(raw.get("app_path") or "")) if raw.get("app_path") else "",
        last_opened=str(raw.get("last_opened") or ""),
    )


def _read_index() -> tuple[List[ProjectEntry], str]:
    path = index_path()
    if not os.path.isfile(path):
        return [], ""
    with open(path, "r", encoding="utf-8") as handle:
        loaded = yaml.safe_load(handle)
    if not isinstance(loaded, dict):
        return [], ""
    rows = loaded.get("projects") or []
    if not isinstance(rows, list):
        return [], str(loaded.get("current") or "")
    entries: List[ProjectEntry] = []
    seen = set()
    for item in rows:
        entry = _entry_from_mapping(item)
        if entry is None or entry.docs_path in seen:
            continue
        seen.add(entry.docs_path)
        entries.append(entry)
    current = _abs(str(loaded.get("current") or "")) if loaded.get("current") else ""
    return entries, current


def load_index() -> List[ProjectEntry]:
    return _read_index()[0]


def save_index(entries: List[ProjectEntry], current: Optional[str] = None) -> str:
    path = index_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if current is None:
        _, current = _read_index()
    current_abs = _abs(current) if current else ""
    docs_paths = {entry.docs_path for entry in entries}
    if current_abs not in docs_paths:
        current_abs = entries[-1].docs_path if entries else ""
    payload = {
        "current": current_abs,
        "projects": [asdict(entry) for entry in entries],
    }
    with open(path, "w", encoding="utf-8") as handle:
        yaml.safe_dump(payload, handle, sort_keys=False)
    return path


def find_by_docs(path: str) -> Optional[ProjectEntry]:
    target = _abs(path)
    for entry in load_index():
        if entry.docs_path == target:
            return entry
    return None


def find_by_app(path: str) -> Optional[ProjectEntry]:
    target = _abs(path or os.getcwd())
    matches = [entry for entry in load_index() if _is_same_or_inside(target, entry.app_path)]
    if not matches:
        return None
    return max(matches, key=lambda item: (item.last_opened, len(item.app_path)))


def last_project() -> Optional[ProjectEntry]:
    entries, current = _read_index()
    if not entries:
        return None
    if current:
        for entry in entries:
            if entry.docs_path == current:
                return entry
    return max(enumerate(entries), key=lambda item: (item[1].last_opened or "", item[0]))[1]


def _default_name(docs_path: str, name: str) -> str:
    cleaned = (name or "").strip()
    if cleaned and cleaned != "Project":
        return cleaned
    return os.path.basename(docs_path.rstrip(os.sep)) or docs_path


def register_project(docs_path: str, app_path: str = "", name: str = "") -> ProjectEntry:
    docs_abs = _abs(docs_path)
    app_abs = _abs(app_path) if app_path else ""
    if not app_abs or not (name or "").strip() or name.strip() == "Project":
        from docflow.config.settings import DocFlowConfig

        cfg = DocFlowConfig.load(docs_repo_path=docs_abs)
        if not app_abs and cfg.app.repo_path:
            app_abs = _abs(cfg.app.repo_path)
        if not (name or "").strip() or name.strip() == "Project":
            name = cfg.project.name if cfg.project.name and cfg.project.name != "Project" else name
    entry = ProjectEntry(
        name=_default_name(docs_abs, name),
        docs_path=docs_abs,
        app_path=app_abs,
        last_opened=_now_iso(),
    )
    entries = [item for item in load_index() if item.docs_path != docs_abs]
    entries.append(entry)
    save_index(entries, current=docs_abs)
    return entry


def unregister_project(docs_path: str) -> bool:
    docs_abs = _abs(docs_path)
    entries = load_index()
    kept = [item for item in entries if item.docs_path != docs_abs]
    if len(kept) == len(entries):
        return False
    save_index(kept)
    return True


def open_project(docs_path: str) -> ProjectEntry:
    docs_abs = _abs(docs_path)
    existing = find_by_docs(docs_abs)
    if existing is None:
        existing = register_project(docs_abs)
    updated = ProjectEntry(
        name=existing.name,
        docs_path=existing.docs_path,
        app_path=existing.app_path,
        last_opened=_now_iso(),
    )
    entries = [item for item in load_index() if item.docs_path != docs_abs]
    entries.append(updated)
    save_index(entries, current=docs_abs)
    return updated


def prune_missing() -> List[ProjectEntry]:
    kept: List[ProjectEntry] = []
    for entry in load_index():
        yml = os.path.join(entry.docs_path, ".docflow.yml")
        if os.path.isdir(entry.docs_path) and os.path.isfile(yml):
            kept.append(entry)
    save_index(kept)
    return kept
