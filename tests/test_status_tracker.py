"""
Tests for StatusTracker module.
"""

import os
import tempfile
import pytest
from git import Repo
from docflow.core.status_tracker import StatusTracker


@pytest.fixture
def temp_git_repos():
    with tempfile.TemporaryDirectory() as app_dir, tempfile.TemporaryDirectory() as docs_dir:
        repo = Repo.init(app_dir)
        file1 = os.path.join(app_dir, "README.md")
        with open(file1, "w") as f:
            f.write("# App\n")
        repo.index.add(["README.md"])
        repo.index.commit("Initial commit")

        # Create a feature branch
        feature_branch = repo.create_head("feature/auth-login")
        feature_branch.checkout()

        file2 = os.path.join(app_dir, "auth.py")
        with open(file2, "w") as f:
            f.write("def login(): pass\n")
        repo.index.add(["auth.py"])
        repo.index.commit("Add auth login function")

        yield app_dir, docs_dir


def test_status_tracker_write_wip_docs(temp_git_repos):
    app_dir, docs_dir = temp_git_repos
    tracker = StatusTracker(app_dir, docs_dir)
    result = tracker.write_wip_docs()

    assert os.path.exists(result["json_path"])
    assert os.path.exists(result["md_path"])

    with open(result["md_path"], "r", encoding="utf-8") as f:
        content = f.read()

    assert "feature/auth-login" in content
    assert "Add auth login function" in content


def test_status_tracker_includes_remote_branch(tmp_path):
    app = tmp_path / "app"
    remote = tmp_path / "remote.git"
    docs = tmp_path / "docs"
    app.mkdir()
    docs.mkdir()
    repo = Repo.init(app)
    Repo.init(remote, bare=True)
    (app / "README.md").write_text("# App\n")
    repo.index.add(["README.md"])
    repo.index.commit("init")
    default = repo.active_branch.name
    origin = repo.create_remote("origin", str(remote))
    origin.push(default)
    feature = repo.create_head("feature/remote-only")
    feature.checkout()
    (app / "extra.py").write_text("x = 1\n")
    repo.index.add(["extra.py"])
    repo.index.commit("remote only work")
    origin.push("feature/remote-only")
    repo.heads[default].checkout()
    repo.delete_head("feature/remote-only", force=True)

    tracker = StatusTracker(str(app), str(docs))
    names = [b["branch"] for b in tracker.scan_wip()["active_branches"]]
    assert "feature/remote-only" in names
