"""
Tests for PromptBuilder module.
"""

import os
import tempfile
import pytest
from docflow.core.models import PromptContext, FeatureChunk
from docflow.core.prompt_builder import PromptBuilder


def test_prompt_builder_init_render():
    builder = PromptBuilder()
    chunk = FeatureChunk(
        feature_name="authentication",
        description="Auth module",
        file_paths=["src/auth/login.py", "src/auth/jwt.py"]
    )
    context = PromptContext(
        task_type="init",
        project_name="TestProject",
        feature_name="authentication",
        feature_chunk=chunk,
        conventions_text="Follow CONVENTIONS.md"
    )

    rendered = builder.render(context)
    assert '# Documentation Task: Initialize "authentication" (features)' in rendered
    assert "src/auth/login.py" in rendered
    assert "Follow CONVENTIONS.md" in rendered


def test_prompt_builder_save():
    builder = PromptBuilder()
    context = PromptContext(
        task_type="full-regen",
        project_name="TestProject",
        feature_name="payments",
        conventions_text="Conventions text"
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        out_file = os.path.join(tmpdir, "prompts", "pending", "test_prompt.md")
        saved_path = builder.save_prompt(context, out_file)

        assert os.path.exists(saved_path)
        with open(saved_path, "r", encoding="utf-8") as f:
            content = f.read()
        assert "Full Regeneration of \"payments\" (features)" in content
