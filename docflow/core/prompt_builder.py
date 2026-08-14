"""
Prompt builder for generating caveman skill prompts using Jinja2 templates.
"""

import os
from pathlib import Path
from typing import Optional
from jinja2 import Environment, FileSystemLoader, select_autoescape

from docflow.core.models import PromptContext


class PromptBuilder:
    """Renders caveman prompt markdown files using Jinja2 templates."""

    def __init__(self, templates_dir: Optional[str] = None):
        if templates_dir is None:
            templates_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates")
        self.templates_dir = os.path.abspath(templates_dir)
        self.env = Environment(
            loader=FileSystemLoader(self.templates_dir),
            autoescape=select_autoescape(["html", "xml"]),
            trim_blocks=True,
            lstrip_blocks=True,
        )

    def render(self, context: PromptContext) -> str:
        """Renders the prompt text based on context.task_type."""
        if context.task_type == "init":
            template = self.env.get_template("prompt_init.md.j2")
        elif context.task_type == "update":
            template = self.env.get_template("prompt_update.md.j2")
        elif context.task_type == "full-regen":
            template = self.env.get_template("prompt_full_regen.md.j2")
        else:
            raise ValueError(f"Unknown task type: {context.task_type}")

        return template.render(**context.model_dump())

    def save_prompt(self, context: PromptContext, output_path: str) -> str:
        """Renders prompt and writes it to output_path."""
        rendered = self.render(context)
        out_dir = os.path.dirname(os.path.abspath(output_path))
        os.makedirs(out_dir, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(rendered)
        return os.path.abspath(output_path)
