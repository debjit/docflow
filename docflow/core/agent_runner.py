"""
Agent runner supporting preset agent CLI commands, custom shell execution, and manual mode.
"""

import os
import re
import subprocess
import shlex
import sys
from typing import Callable, Dict, Optional

from docflow.core.models import AgentRunResult


AGENT_PRESETS: Dict[str, str] = {
    "agy": 'agy --dangerously-skip-permissions --add-dir {docs_repo} -p "Follow every instruction in {prompt_file}."',
    "agy-interactive": 'agy --dangerously-skip-permissions --add-dir {docs_repo} -i "Follow every instruction in {prompt_file}."',
    "opencode": 'opencode "Follow every instruction in {prompt_file}."',
    "cursor": 'agent --workspace {docs_repo} --force --trust -p "Follow every instruction in {prompt_file}."',
    "cursor-agent": 'agent --workspace {docs_repo} --force --trust -p "Follow every instruction in {prompt_file}."',
    "cursor-interactive": 'agent --workspace {docs_repo} "Follow every instruction in {prompt_file}."',
    "claude": 'claude -p "Follow every instruction in {prompt_file}."',
    "cline": "cline {prompt_file}",
    "manual": "manual",
}

_PROMPT_POINTER = "Follow every instruction in {prompt_file}."
_CAT_TEMPLATE_RE = re.compile(
    r"\$\(\s*cat\s+(?:[\"']?)\{prompt_file\}(?:[\"']?)\s*\)",
    re.IGNORECASE,
)
_CAT_FORMATTED_RE = re.compile(
    r"\$\(\s*cat\s+([^)]+?)\s*\)",
    re.IGNORECASE,
)


def _rewrite_cat_prompt_template(template: str) -> str:
    """Rewrite saved `$(cat {prompt_file})` commands to a file-path pointer."""
    return _CAT_TEMPLATE_RE.sub(_PROMPT_POINTER, template)


def _neutralize_cat_after_format(cmd: str) -> str:
    """Ensure `$(cat ...)` is never expanded into argv after placeholders are filled."""

    def _repl(match: re.Match) -> str:
        inner = match.group(1).strip().strip("\"'")
        return f"Follow every instruction in {inner}."

    return _CAT_FORMATTED_RE.sub(_repl, cmd)


def explain_agent_failure(returncode: int, output: Optional[str], stderr: Optional[str]) -> str:
    """Build an error_message without leaking a None stderr placeholder."""
    out = output or ""
    err = stderr or ""
    combined = "\n".join(part for part in (out, err) if part).strip()
    combined_l = combined.lower()
    arg_max = (
        "argument list too long" in combined_l
        or "e2big" in combined_l
        or "errno 7" in combined_l
    )
    arg_max_hint = (
        "Prompts are passed by file path now; an old command inlined the prompt "
        "into the argument list via $(cat {prompt_file})."
    )

    if arg_max:
        detail = combined[-500:] if combined else "Argument list too long"
        return f"Agent command exit code {returncode}: {detail}. {arg_max_hint}"

    if returncode == 127:
        detail = combined[-500:] if combined else "command not found"
        return f"Agent command exit code 127: {detail}"

    if returncode == 126:
        if combined:
            return f"Agent command exit code 126: cannot execute. {combined[-500:]}"
        return (
            f"Agent command exit code 126: cannot execute or argument list too long. "
            f"{arg_max_hint}"
        )

    if combined:
        return f"Agent command exit code {returncode}: {combined[-500:]}"
    return f"Agent command exit code {returncode}"


class AgentRunner:
    """Executes or presents prompts to coding agents."""

    def __init__(self, mode: str = "manual", command_template: Optional[str] = None):
        self.mode = mode.lower()
        if self.mode not in ("shell", "manual"):
            raise ValueError(f"Invalid agent runner mode: {mode}. Must be 'shell' or 'manual'.")

        self.command_template = command_template or AGENT_PRESETS["agy"]

    @classmethod
    def get_preset_command(cls, agent_name: str) -> str:
        """Returns default command for a given agent name preset."""
        return AGENT_PRESETS.get(agent_name.lower(), f"{agent_name} {{prompt_file}}")

    def run(
        self,
        prompt_file_path: str,
        docs_repo_path: str,
        capture: Optional[bool] = None,
        on_output: Optional[Callable[[str], None]] = None,
    ) -> AgentRunResult:
        """Runs the agent or prepares prompt file for manual execution.

        If ``capture`` is True, stdout/stderr are captured (for TUI/tests).
        If False, the agent inherits the TTY. ``None`` auto-detects via isatty().
        Captured runs use stdin=DEVNULL so the agent cannot deadlock the TUI.
        """
        abs_prompt_path = os.path.abspath(prompt_file_path)
        abs_docs_path = os.path.abspath(docs_repo_path)

        if not os.path.exists(abs_prompt_path):
            return AgentRunResult(
                success=False,
                mode=self.mode,
                prompt_file_path=abs_prompt_path,
                error_message=f"Prompt file not found: {abs_prompt_path}"
            )

        if self.mode == "manual":
            return AgentRunResult(
                success=True,
                mode="manual",
                prompt_file_path=abs_prompt_path,
                output_log=""
            )

        template = _rewrite_cat_prompt_template(self.command_template)
        formatted_cmd = template.format(
            prompt_file=shlex.quote(abs_prompt_path),
            docs_repo=shlex.quote(abs_docs_path),
        )
        formatted_cmd = _neutralize_cat_after_format(formatted_cmd)

        try:
            is_tty = False
            try:
                if sys.stdout and hasattr(sys.stdout, "isatty"):
                    is_tty = sys.stdout.isatty()
            except Exception:
                is_tty = False

            use_tty = is_tty if capture is None else not capture

            if use_tty:
                # stdout stays on the TTY; stderr is piped so failures are not "None"
                res = subprocess.run(
                    formatted_cmd,
                    shell=True,
                    cwd=abs_docs_path,
                    stdin=sys.stdin,
                    stdout=sys.stdout,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                output_text = "Agent executed successfully."
                err_text = res.stderr or ""
            else:
                env = os.environ.copy()
                env.setdefault("PYTHONUNBUFFERED", "1")
                proc = subprocess.Popen(
                    formatted_cmd,
                    shell=True,
                    cwd=abs_docs_path,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    env=env,
                )
                lines = []
                assert proc.stdout is not None
                for line in proc.stdout:
                    text = line.rstrip("\n")
                    lines.append(text)
                    if on_output and text:
                        on_output(text)
                returncode = proc.wait()
                output_text = "\n".join(lines)
                if returncode == 0:
                    return AgentRunResult(
                        success=True,
                        mode="shell",
                        prompt_file_path=abs_prompt_path,
                        output_log=output_text,
                    )
                return AgentRunResult(
                    success=False,
                    mode="shell",
                    prompt_file_path=abs_prompt_path,
                    output_log=output_text,
                    error_message=explain_agent_failure(returncode, output_text, ""),
                )

            if res.returncode == 0:
                return AgentRunResult(
                    success=True,
                    mode="shell",
                    prompt_file_path=abs_prompt_path,
                    output_log=output_text
                )
            else:
                return AgentRunResult(
                    success=False,
                    mode="shell",
                    prompt_file_path=abs_prompt_path,
                    output_log=output_text,
                    error_message=explain_agent_failure(res.returncode, output_text, err_text)
                )
        except Exception as e:
            return AgentRunResult(
                success=False,
                mode="shell",
                prompt_file_path=abs_prompt_path,
                error_message=f"Subprocess execution failed: {str(e)}"
            )
