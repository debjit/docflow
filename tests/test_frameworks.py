"""
Tests for framework detection and ignore profiles.
"""

import json
import os
import tempfile

import pytest
from git import Repo

from docflow.core.frameworks import (
    STACK_FILENAME,
    detect_laravel,
    effective_ignore,
    resolve_framework_name,
    skip_as_feature_dirs,
    stack_file_path,
    stack_guidance,
)
from docflow.core.git_analyzer import GitAnalyzer, path_is_ignored


@pytest.fixture
def laravel_repo():
    with tempfile.TemporaryDirectory() as tmpdir:
        os.makedirs(os.path.join(tmpdir, "bootstrap"), exist_ok=True)
        os.makedirs(os.path.join(tmpdir, "app", "Models"), exist_ok=True)
        os.makedirs(os.path.join(tmpdir, "vendor", "laravel"), exist_ok=True)
        os.makedirs(os.path.join(tmpdir, "bootstrap", "cache"), exist_ok=True)
        os.makedirs(os.path.join(tmpdir, "routes"), exist_ok=True)

        with open(os.path.join(tmpdir, "artisan"), "w", encoding="utf-8") as f:
            f.write("#!/usr/bin/env php\n")
        with open(os.path.join(tmpdir, "bootstrap", "app.php"), "w", encoding="utf-8") as f:
            f.write("<?php\n")
        with open(os.path.join(tmpdir, "composer.json"), "w", encoding="utf-8") as f:
            json.dump({"require": {"laravel/framework": "^11.0"}}, f)
        with open(os.path.join(tmpdir, "app", "Models", "User.php"), "w", encoding="utf-8") as f:
            f.write("<?php\nclass User {}\n")
        with open(os.path.join(tmpdir, "routes", "web.php"), "w", encoding="utf-8") as f:
            f.write("<?php\n")
        with open(os.path.join(tmpdir, "bootstrap", "providers.php"), "w", encoding="utf-8") as f:
            f.write("<?php\n")

        repo = Repo.init(tmpdir)
        repo.index.add(["artisan", "bootstrap/app.php", "composer.json", "app/Models/User.php", "routes/web.php", "bootstrap/providers.php"])
        repo.index.commit("init laravel skeleton")

        yield tmpdir


def test_detect_laravel(laravel_repo):
    assert detect_laravel(laravel_repo) is True
    assert resolve_framework_name(laravel_repo, "auto") == "laravel"
    assert resolve_framework_name(laravel_repo, "none") is None
    assert resolve_framework_name(laravel_repo, "laravel") == "laravel"


def test_effective_ignore_includes_vendor(laravel_repo):
    ignore = effective_ignore(laravel_repo, extra=["custom/"], framework_name="laravel")
    assert path_is_ignored("vendor/laravel/framework/src/Application.php", ignore)
    assert path_is_ignored("storage/logs/laravel.log", ignore)
    assert path_is_ignored("bootstrap/cache/packages.php", ignore)
    assert not path_is_ignored("app/Models/User.php", ignore)
    assert path_is_ignored("custom/foo.txt", ignore)


def test_scan_features_skips_laravel_scaffolding(laravel_repo):
    analyzer = GitAnalyzer(laravel_repo)
    ignore = effective_ignore(laravel_repo, framework_name="laravel")
    skip = skip_as_feature_dirs("laravel")
    chunks = analyzer.scan_features(
        ignore_patterns=ignore,
        include_architecture=False,
        skip_as_feature=skip,
    )
    names = {c.feature_name for c in chunks}
    assert "vendor" not in names
    assert "bootstrap" not in names
    assert "public" not in names
    assert "Models" in names or "routes" in names


def test_stack_guidance_from_file(laravel_repo):
    with tempfile.TemporaryDirectory() as docs:
        stack = {
            "platform": "laravel",
            "libraries": ["filament", "inertia"],
            "guidance": "Document Filament resources only.",
        }
        with open(stack_file_path(docs), "w", encoding="utf-8") as f:
            json.dump(stack, f)
        assert stack_guidance(docs, "laravel") == "Document Filament resources only."


def test_stack_guidance_falls_back_to_profile(laravel_repo):
    with tempfile.TemporaryDirectory() as docs:
        guidance = stack_guidance(docs, "laravel")
        assert guidance is not None
        assert "Laravel" in guidance


def test_stack_filename_constant():
    assert STACK_FILENAME == ".docflow-stack.json"
