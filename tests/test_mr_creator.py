"""
Tests for MRCreator module.
"""

import pytest
from docflow.git_ops.mr_creator import MRCreator, parse_git_remote_slug, git_origin_slug


def test_mr_creator_generic_mode():
    creator = MRCreator(platform_type="generic")
    res = creator.create_mr(
        title="Update docs",
        body="Update body",
        source_branch="docs/update-auth-123",
        target_branch="main"
    )

    assert res["success"] is True
    assert res["platform"] == "generic"
    assert "docs/update-auth-123" in res["message"]


def test_mr_creator_github_no_token():
    creator = MRCreator(platform_type="github")
    res = creator.create_mr(
        title="Update docs",
        body="Update body",
        source_branch="docs/update-auth-123"
    )

    # Without GITHUB_TOKEN set, should return descriptive error gracefully
    assert res["success"] is False
    assert "GITHUB_TOKEN" in res["error"]


def test_parse_git_remote_slug():
    assert parse_git_remote_slug("git@github.com:owner/repo.git") == "owner/repo"
    assert parse_git_remote_slug("https://github.com/owner/repo.git") == "owner/repo"
    assert parse_git_remote_slug("https://github.com/owner/repo") == "owner/repo"
    assert parse_git_remote_slug("ssh://git@github.com/acme/docs.git") == "acme/docs"
    assert parse_git_remote_slug("git@gitlab.com:group/sub/project.git") == "group/sub/project"
    assert parse_git_remote_slug("") is None


def test_git_origin_slug(tmp_path):
    from git import Repo

    repo = Repo.init(tmp_path)
    (tmp_path / "README.md").write_text("x\n")
    repo.index.add(["README.md"])
    repo.index.commit("init")
    repo.create_remote("origin", "git@github.com:acme/docs.git")
    assert git_origin_slug(str(tmp_path)) == "acme/docs"
