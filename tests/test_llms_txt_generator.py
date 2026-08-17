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


def test_llms_txt_includes_configured_doc_types():
    with tempfile.TemporaryDirectory() as docs_dir:
        arch = os.path.join(docs_dir, "architecture")
        os.makedirs(arch, exist_ok=True)
        with open(os.path.join(arch, "index.md"), "w") as f:
            f.write("# Architecture\n\nSystem layout.\n")
        front = os.path.join(docs_dir, "front-end")
        os.makedirs(front, exist_ok=True)
        with open(os.path.join(front, "index.md"), "w") as f:
            f.write("# Front end\n\nUI docs.\n")
        feat = os.path.join(docs_dir, "features", "auth")
        os.makedirs(feat, exist_ok=True)
        with open(os.path.join(feat, "index.md"), "w") as f:
            f.write("# Auth\n")

        generator = LLMSTxtGenerator(docs_dir)
        res = generator.generate(project_name="TestApp")
        with open(res["llms_txt"], "r") as f:
            llms_txt = f.read()
        assert "architecture/index.md" in llms_txt
        assert "front-end/index.md" in llms_txt
        assert "features/auth/index.md" in llms_txt
        with open(res["llms_full_txt"], "r") as f:
            llms_full = f.read()
        assert "BEGIN ARCHITECTURE: architecture" in llms_full
        assert "System layout." in llms_full
