"""
Tests for LLMSTxtGenerator module.
"""

import os
import tempfile
import pytest
from docflow.core.llms_txt_generator import LLMSTxtGenerator


def test_llms_txt_generator():
    with tempfile.TemporaryDirectory() as docs_dir:
        # Create a sample feature directory
        auth_dir = os.path.join(docs_dir, "features", "authentication")
        os.makedirs(auth_dir, exist_ok=True)
        index_file = os.path.join(auth_dir, "index.md")
        with open(index_file, "w") as f:
            f.write("# Authentication\n\nOAuth2 implementation.\n")

        generator = LLMSTxtGenerator(docs_dir)
        res = generator.generate(project_name="TestApp")

        assert os.path.exists(res["llms_txt"])
        assert os.path.exists(res["llms_full_txt"])

        with open(res["llms_txt"], "r") as f:
            llms_txt = f.read()
        assert "# TestApp" in llms_txt
        assert "features/authentication/index.md" in llms_txt

        with open(res["llms_full_txt"], "r") as f:
            llms_full = f.read()
        assert "BEGIN FEATURE: authentication" in llms_full
        assert "OAuth2 implementation." in llms_full
