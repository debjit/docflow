"""
Tests for GitAnalyzer module.
"""

import os
import tempfile
import pytest
from git import Repo
from docflow.core.git_analyzer import GitAnalyzer, feature_bucket_for_path, path_is_ignored


@pytest.fixture
def temp_git_repo():
    with tempfile.TemporaryDirectory() as tmpdir:
        repo = Repo.init(tmpdir)
        # Create initial commit
        file1 = os.path.join(tmpdir, "README.md")
        with open(file1, "w") as f:
            f.write("# Sample Repo\n")
        repo.index.add(["README.md"])
        commit1 = repo.index.commit("Initial commit")

        # Create feature file in src/auth/
        auth_dir = os.path.join(tmpdir, "src", "auth")
        os.makedirs(auth_dir, exist_ok=True)
        file2 = os.path.join(auth_dir, "login.py")
        with open(file2, "w") as f:
            f.write("def login(username, password):\n    return True\n")
        repo.index.add(["src/auth/login.py"])
        commit2 = repo.index.commit("Add auth module")

        yield tmpdir, commit1.hexsha, commit2.hexsha


def test_extract_diff(temp_git_repo):
    tmpdir, commit1, commit2 = temp_git_repo
    analyzer = GitAnalyzer(tmpdir)

    manifest = analyzer.extract_diff(base_ref=commit1, head_ref=commit2)

    assert manifest is not None
    assert len(manifest.changed_files) == 1
    file_change = manifest.changed_files[0]
    assert file_change.path == "src/auth/login.py"
    assert file_change.change_type == "added"
    assert file_change.added_lines > 0
    assert "login(username, password)" in file_change.full_diff


def test_scan_features(temp_git_repo):
    tmpdir, _, _ = temp_git_repo
    analyzer = GitAnalyzer(tmpdir)

    chunks = analyzer.scan_features()
    feature_names = [c.feature_name for c in chunks]

    assert "auth" in feature_names or "core" in feature_names


def test_list_commits_and_range(temp_git_repo):
    tmpdir, commit1, commit2 = temp_git_repo
    analyzer = GitAnalyzer(tmpdir)
    commits = analyzer.list_commits(max_count=5)
    assert len(commits) >= 2
    assert commits[0]["sha"] == commit2
    assert "auth" in commits[0]["message"].lower() or "Add auth" in commits[0]["message"]

    between = analyzer.commits_between(commit1, commit2)
    assert len(between) == 1
    assert between[0]["sha"] == commit2

    branches = analyzer.list_branches()
    assert branches
    on_branch = analyzer.list_commits(max_count=2, rev=branches[0])
    assert len(on_branch) >= 1
    assert analyzer.is_ancestor(commit1, commit2)
    assert analyzer.head_commit()["sha"] == commit2


def test_feature_bucket_and_ignore_globs():
    assert feature_bucket_for_path("src/auth/login.py") == "auth"
    assert feature_bucket_for_path("auth/login.py") == "auth"
    assert feature_bucket_for_path("README.md") == "core"
    ignore = {"node_modules/", "*.lock", "dist"}
    assert path_is_ignored("node_modules/pkg/index.js", ignore)
    assert path_is_ignored("app/package.lock", ignore)
    assert path_is_ignored("dist/bundle.js", ignore)
    assert not path_is_ignored("src/auth/login.py", ignore)
