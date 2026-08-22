"""
DocFlow working files live under `.docflow/` in the docs repo.

Human and LLM documentation stays in type folders (architecture, models, …).
Config, prompts, stack survey, and generate state are not documentation.
"""

from __future__ import annotations

import os
from typing import List, Optional

DOCFLOW_DIRNAME = ".docflow"
CONFIG_FILENAME = "config.yml"
LEGACY_CONFIG = ".docflow.yml"
STACK_FILENAME = "stack.json"
LEGACY_STACK = ".docflow-stack.json"
STATE_FILENAME = "state.json"
LEGACY_STATE = ".docflow-state.json"
CONVENTIONS_FILENAME = "CONVENTIONS.md"

SPLIT_DOC_TYPES = frozenset(
    {"models", "functions", "routes", "pages", "database", "features"}
)


def docflow_dir(docs_repo_path: str) -> str:
    return os.path.join(os.path.abspath(docs_repo_path), DOCFLOW_DIRNAME)


def _first_existing(docs_repo_path: str, *relative: str) -> Optional[str]:
    root = os.path.abspath(docs_repo_path)
    for rel in relative:
        path = os.path.join(root, rel)
        if os.path.isfile(path):
            return path
    return None


def find_config_path(docs_repo_path: str) -> Optional[str]:
    if not docs_repo_path:
        return None
    return _first_existing(
        docs_repo_path,
        os.path.join(DOCFLOW_DIRNAME, CONFIG_FILENAME),
        LEGACY_CONFIG,
    )


def write_config_path(docs_repo_path: str) -> str:
    return os.path.join(docflow_dir(docs_repo_path), CONFIG_FILENAME)


def is_docs_project(docs_repo_path: str) -> bool:
    return find_config_path(docs_repo_path) is not None


def docs_root_from_config_path(config_path: str) -> str:
    """Docs repo root for a config file (`.docflow/config.yml` or legacy `.docflow.yml`)."""
    path = os.path.abspath(config_path)
    parent = os.path.dirname(path)
    if os.path.basename(parent) == DOCFLOW_DIRNAME:
        return os.path.dirname(parent)
    return parent


def find_stack_path(docs_repo_path: str) -> Optional[str]:
    return _first_existing(
        docs_repo_path,
        os.path.join(DOCFLOW_DIRNAME, STACK_FILENAME),
        LEGACY_STACK,
    )


def write_stack_path(docs_repo_path: str) -> str:
    return os.path.join(docflow_dir(docs_repo_path), STACK_FILENAME)


def find_state_path(docs_repo_path: str) -> Optional[str]:
    return _first_existing(
        docs_repo_path,
        os.path.join(DOCFLOW_DIRNAME, STATE_FILENAME),
        LEGACY_STATE,
    )


def write_state_path(docs_repo_path: str) -> str:
    return os.path.join(docflow_dir(docs_repo_path), STATE_FILENAME)


def find_conventions_path(docs_repo_path: str) -> Optional[str]:
    return _first_existing(
        docs_repo_path,
        os.path.join(DOCFLOW_DIRNAME, CONVENTIONS_FILENAME),
        CONVENTIONS_FILENAME,
    )


def write_conventions_path(docs_repo_path: str) -> str:
    return os.path.join(docflow_dir(docs_repo_path), CONVENTIONS_FILENAME)


def agent_logs_dir(docs_repo_path: str) -> str:
    return os.path.join(docflow_dir(docs_repo_path), "logs")


def pending_prompts_dir(docs_repo_path: str) -> str:
    return os.path.join(docflow_dir(docs_repo_path), "prompts", "pending")


def completed_prompts_dir(docs_repo_path: str) -> str:
    return os.path.join(docflow_dir(docs_repo_path), "prompts", "completed")


def pending_prompt_path(docs_repo_path: str, filename: str) -> str:
    folder = pending_prompts_dir(docs_repo_path)
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, filename)


def rel_pending_prompt(filename: str) -> str:
    return f"{DOCFLOW_DIRNAME}/prompts/pending/{filename}"


def prompt_search_dirs(docs_repo_path: str) -> List[str]:
    root = os.path.abspath(docs_repo_path)
    return [
        pending_prompts_dir(root),
        os.path.join(root, "prompts", "pending"),
    ]
