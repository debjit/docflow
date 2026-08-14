"""
Agent runner supporting preset agent CLI commands, custom shell execution, and manual mode.
"""

import os
import subprocess
import shlex
import sys
from typing import Callable, Dict, Optional

from docflow.core.models import AgentRunResult


AGENT_PRESETS: Dict[str, str] = {
    "agy": 'agy --dangerously-skip-permissions --add-dir {docs_repo} -p "$(cat {prompt_file})"',
    "agy-interactive": 'agy --dangerously-skip-permissions --add-dir {docs_repo} -i "$(cat {prompt_file})"',
    "opencode": 'opencode "$(cat {prompt_file})"',
    "cursor": 'agent --workspace {docs_repo} --force --trust -p "$(cat {prompt_file})"',
    "cursor-agent": 'agent --workspace {docs_repo} --force --trust -p "$(cat {prompt_file})"',
    "cursor-interactive": 'agent --workspace {docs_repo} "$(cat {prompt_file})"',
    "claude": 'claude -p "$(cat {prompt_file})"',
    "cline": "cline {prompt_file}",
    "manual": "manual",
}


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

        formatted_cmd = self.command_template.format(
            prompt_file=shlex.quote(abs_prompt_path),
            docs_repo=shlex.quote(abs_docs_path),
        )

        try:
            is_tty = False
            try:
                if sys.stdout and hasattr(sys.stdout, "isatty"):
                    is_tty = sys.stdout.isatty()
            except Exception:
                is_tty = False

            use_tty = is_tty if capture is None else not capture

            if use_tty:
                # Direct TTY execution for interactive terminal sessions (agy, opencode, cursor)
                res = subprocess.run(
                    formatted_cmd,
                    shell=True,
                    cwd=abs_docs_path,
                    stdin=sys.stdin,
                    stdout=sys.stdout,
                    stderr=sys.stderr
                )
                output_text = "Agent executed successfully."
                err_text = getattr(res, "stderr", "")
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
                    error_message=f"Agent command exit code {returncode}: {output_text[-500:]}",
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
                    error_message=f"Agent command exit code {res.returncode}: {err_text}"
                )
        except Exception as e:
            return AgentRunResult(
                success=False,
                mode="shell",
                prompt_file_path=abs_prompt_path,
                error_message=f"Subprocess execution failed: {str(e)}"
            )
