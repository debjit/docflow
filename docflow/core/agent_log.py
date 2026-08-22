"""
Persistent agent-run logs under `.docflow/logs/` in the docs repo.

Every agent execution (agy, opencode, claude, cursor-agent, cline, custom
shell commands) gets one JSONL summary row in `agent-runs.jsonl` and a raw
stdout/stderr transcript file under `runs/`. Permission refusals and other
agent-side failures therefore stay greppable on disk after the run.
"""

import json
import os
import re
from datetime import datetime, timezone
from typing import Optional

from docflow.core.models import AgentRunResult
from docflow.core.workspace import agent_logs_dir

_TRANSCRIPTS_DIRNAME = "runs"
INDEX_FILENAME = "agent-runs.jsonl"


def agent_runs_index_path(docs_repo_path: str) -> str:
    return os.path.join(agent_logs_dir(docs_repo_path), INDEX_FILENAME)


def agent_transcripts_dir(docs_repo_path: str) -> str:
    return os.path.join(agent_logs_dir(docs_repo_path), _TRANSCRIPTS_DIRNAME)


def agent_slug_from_command(command_template: str) -> str:
    """First token of an agent command template as a filesystem-safe slug."""
    text = (command_template or "").strip()
    token = text.split()[0] if text else ""
    slug = re.sub(r"[^a-z0-9]+", "-", token.lower()).strip("-")
    return slug or "agent"


def record_agent_run(
    docs_repo_path: str,
    result: AgentRunResult,
    command_template: str = "",
    transcript_text: Optional[str] = None,
) -> Optional[str]:
    """Persist one agent run: transcript file plus summary row.

    Never raises — logging must not break agent execution. Returns the
    transcript path, or None when nothing could be written.
    """
    try:
        root = os.path.abspath(docs_repo_path or "")
        if not root or not os.path.isdir(root):
            return None

        now = datetime.now(timezone.utc)
        stamp = now.strftime("%Y%m%dT%H%M%S")
        slug = agent_slug_from_command(command_template)
        if slug == "agent" and result.mode == "manual":
            return None

        transcripts = agent_transcripts_dir(root)
        os.makedirs(transcripts, exist_ok=True)
        transcript = os.path.join(transcripts, f"{stamp}-{slug}.log")
        counter = 2
        while os.path.exists(transcript):
            transcript = os.path.join(transcripts, f"{stamp}-{counter}-{slug}.log")
            counter += 1

        body = transcript_text if transcript_text is not None else (result.output_log or "")
        with open(transcript, "w", encoding="utf-8") as fh:
            fh.write(body)
            if body and not body.endswith("\n"):
                fh.write("\n")

        row = {
            "timestamp": now.isoformat(timespec="seconds"),
            "success": bool(result.success),
            "mode": result.mode,
            "agent": slug,
            "command": command_template or "",
            "prompt_file": result.prompt_file_path,
            "error_message": result.error_message or "",
            "transcript": transcript,
        }
        with open(agent_runs_index_path(root), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        return transcript
    except Exception:
        return None
