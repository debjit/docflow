"""
Tests for AgentRunner module.
"""

import os
import tempfile
import pytest
from docflow.core.agent_runner import AgentRunner


def test_agent_runner_manual_mode():
    runner = AgentRunner(mode="manual")
    with tempfile.TemporaryDirectory() as tmpdir:
        prompt_file = os.path.join(tmpdir, "prompt.md")
        with open(prompt_file, "w") as f:
            f.write("# Prompt")

        res = runner.run(prompt_file, tmpdir)
        assert res.success is True
        assert res.mode == "manual"
        assert res.prompt_file_path == os.path.abspath(prompt_file)


def test_agent_runner_shell_mode():
    # Use a dummy shell command `echo Hello {prompt_file}`
    runner = AgentRunner(mode="shell", command_template="echo Processed {prompt_file}")
    with tempfile.TemporaryDirectory() as tmpdir:
        prompt_file = os.path.join(tmpdir, "prompt.md")
        with open(prompt_file, "w") as f:
            f.write("# Prompt")

        lines = []
        res = runner.run(prompt_file, tmpdir, capture=True, on_output=lines.append)
        assert res.success is True
        assert res.mode == "shell"
        assert "Processed" in res.output_log
        assert any("Processed" in line for line in lines)
