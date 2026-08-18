"""Shared pytest fixtures."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def isolate_docflow_user_config(tmp_path_factory, monkeypatch):
    """Never read or write the real user DocFlow index during tests."""
    root = tmp_path_factory.mktemp("docflow-xdg")
    home = root / "home"
    home.mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(root))
    monkeypatch.setenv("HOME", str(home))
