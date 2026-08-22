"""
Tests for AgentRunner module.
"""

import os
import tempfile
from docflow.core.agent_runner import AGENT_PRESETS, AgentRunner, explain_agent_failure


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


def test_presets_do_not_cat_prompt_into_argv():
    for name, cmd in AGENT_PRESETS.items():
        assert "$(cat" not in cmd, name


def test_old_cat_template_rewritten_echo_does_not_cat():
    runner = AgentRunner(mode="shell", command_template='echo "$(cat {prompt_file})"')
    with tempfile.TemporaryDirectory() as tmpdir:
        prompt_file = os.path.join(tmpdir, "prompt.md")
        with open(prompt_file, "w") as f:
            f.write("SECRET_PROMPT_BODY")

        res = runner.run(prompt_file, tmpdir, capture=True)
        assert res.success is True
        assert "SECRET_PROMPT_BODY" not in res.output_log
        assert "Follow every instruction in" in res.output_log
        assert os.path.abspath(prompt_file) in res.output_log.replace("'", "")


def test_error_message_does_not_use_none_stderr_placeholder():
    msg = explain_agent_failure(126, None, None)
    assert msg is not None
    assert "None" not in msg
    assert "126" in msg

    runner = AgentRunner(mode="shell", command_template="echo fail >&2; exit 126")
    with tempfile.TemporaryDirectory() as tmpdir:
        prompt_file = os.path.join(tmpdir, "prompt.md")
        with open(prompt_file, "w") as f:
            f.write("# Prompt")
        res = runner.run(prompt_file, tmpdir, capture=True)
        assert res.success is False
        assert res.error_message is not None
        assert res.error_message.strip() != "None"
        assert ": None" not in res.error_message


def test_explain_agent_failure_arg_max():
    msg = explain_agent_failure(1, "", "bash: /usr/bin/agy: Argument list too long")
    assert "Argument list too long" in msg
    assert "file path" in msg.lower()
    assert "$(cat {prompt_file})" in msg
