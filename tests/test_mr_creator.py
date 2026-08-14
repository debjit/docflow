"""
Tests for MRCreator module.
"""

import pytest
from docflow.git_ops.mr_creator import MRCreator


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
