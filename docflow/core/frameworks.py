"""
Framework detection and ignore profiles for application-focused documentation.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Set

from docflow.core.git_analyzer import DEFAULT_IGNORE
from docflow.core.workspace import find_stack_path, write_stack_path

STACK_FILENAME = "stack.json"

# Applied for every repo, even when no framework is detected.
ALWAYS_IGNORE = {
    "vendor/",
    "node_modules/",
    "storage/",
    "bootstrap/cache/",
    "public/hot",
    "public/build/",
    "public/index.php",
}

LARAVEL_SKIP_AS_FEATURE = {"bootstrap", "public", "vendor", "storage"}


@dataclass(frozen=True)
class FrameworkProfile:
    name: str
    ignore: Set[str] = field(default_factory=set)
    skip_as_feature: Set[str] = field(default_factory=set)
    document_dirs: List[str] = field(default_factory=list)
    default_guidance: str = ""


LARAVEL_PROFILE = FrameworkProfile(
    name="laravel",
    ignore={
        "storage/",
        "bootstrap/cache/",
        "public/hot",
        "public/build/",
        "public/index.php",
    },
    skip_as_feature=set(LARAVEL_SKIP_AS_FEATURE),
    document_dirs=[
        "app/",
        "routes/",
        "database/",
        "resources/js/",
        "resources/views/",
        "config/",
        "composer.json",
        "package.json",
        "artisan",
    ],
    default_guidance=(
        "This application uses Laravel. Public Laravel documentation already exists.\n"
        "Do NOT explain bootstrap, the service container, artisan internals, Illuminate, "
        "or vendor packages.\n"
        "When Filament, Inertia, Vue, or React are present, do NOT document those libraries "
        "themselves — document this application's models, controllers, policies, jobs, "
        "Filament resources/pages, Inertia pages, Vue/React components, routes, migrations, "
        "and project-specific config."
    ),
)


def _read_json(path: Path) -> Optional[dict]:
    if not path.is_file():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _composer_requires_laravel(repo_path: str) -> bool:
    composer = _read_json(Path(repo_path) / "composer.json")
    if not composer:
        return False
    require = composer.get("require") or {}
    if not isinstance(require, dict):
        return False
    for key in require:
        if key == "laravel/framework" or str(key).startswith("laravel/"):
            return True
    return False


def detect_laravel(repo_path: str) -> bool:
    """True when at least two Laravel fingerprints match."""
    root = Path(repo_path)
    signals = 0
    if (root / "artisan").is_file():
        signals += 1
    if _composer_requires_laravel(repo_path):
        signals += 1
    if (root / "bootstrap" / "app.php").is_file():
        signals += 1
    return signals >= 2


def resolve_framework_name(repo_path: str, mode: str = "auto") -> Optional[str]:
    """Resolve active framework profile name from config mode and repo fingerprints."""
    normalized = (mode or "auto").strip().lower()
    if normalized == "none":
        return None
    if normalized == "laravel":
        return "laravel"
    if normalized == "auto" and detect_laravel(repo_path):
        return "laravel"
    return None


def get_profile(name: Optional[str]) -> Optional[FrameworkProfile]:
    if name == "laravel":
        return LARAVEL_PROFILE
    return None


def effective_ignore(
    repo_path: str,
    extra: Optional[List[str]] = None,
    framework_name: Optional[str] = None,
) -> Set[str]:
    """Merge default, always-ignore, framework profile, and user ignore patterns."""
    patterns: Set[str] = set(DEFAULT_IGNORE)
    patterns.update(ALWAYS_IGNORE)
    profile = get_profile(framework_name)
    if profile:
        patterns.update(profile.ignore)
    for raw in extra or []:
        pattern = (raw or "").strip()
        if pattern:
            patterns.add(pattern)
    return patterns


def skip_as_feature_dirs(framework_name: Optional[str]) -> Set[str]:
    profile = get_profile(framework_name)
    if profile:
        return set(profile.skip_as_feature)
    return set()


def stack_file_path(docs_repo_path: str) -> str:
    found = find_stack_path(docs_repo_path)
    if found:
        return found
    path = write_stack_path(docs_repo_path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return path


def load_stack_file(docs_repo_path: str) -> Optional[dict]:
    return _read_json(Path(stack_file_path(docs_repo_path)))


def stack_guidance(docs_repo_path: str, framework_name: Optional[str] = None) -> Optional[str]:
    """Return guidance from stack file or framework default profile."""
    stack = load_stack_file(docs_repo_path)
    if stack:
        guidance = stack.get("guidance")
        if isinstance(guidance, str) and guidance.strip():
            return guidance.strip()
    profile = get_profile(framework_name)
    if profile and profile.default_guidance:
        return profile.default_guidance
    return None


def architecture_seed_paths(repo_path: str, framework_name: Optional[str]) -> List[str]:
    """Evidence files for the architecture overview — not CLI or every app file."""
    del framework_name
    root = Path(repo_path)
    seeds: List[str] = []
    for rel in (
        "composer.json",
        "package.json",
        "README.md",
        "docker-compose.yml",
        "compose.yaml",
        "docker-compose.yaml",
    ):
        if (root / rel).is_file():
            seeds.append(rel)
    return seeds
