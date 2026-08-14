"""
DocFlow Textual TUI.
"""

from __future__ import annotations

import asyncio
import os
import threading
from collections import deque
from typing import Optional

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Footer, Header, Input, Label, LoadingIndicator, OptionList, Select, Static, TextArea
from textual.widgets.option_list import Option

from docflow.core.agent_runner import AGENT_PRESETS
from docflow.core.operations import (
    AGENT_CHOICES,
    ConfigError,
    DEFAULT_DOC_TYPES,
    agent_supports_models,
    default_docs_path,
    generate_docs,
    get_dashboard,
    import_docs,
    init_docs,
    list_agent_models,
    list_app_branches,
    list_recent_commits,
    parse_doc_types_text,
    publish_docs,
    pull_app_repo,
    resolve_agent,
    resolve_paths,
)


def _agent_select_options():
    return [(label, key) for key, label in AGENT_CHOICES if key != "custom"]


def _agent_key_from_dash(dash) -> str:
    """Map saved agent config to a Select value."""
    if not dash.configured or dash.agent_mode == "manual":
        return "manual"
    cmd = dash.agent_command or ""
    select_keys = {key for key, _ in AGENT_CHOICES if key != "custom"}
    for key, preset in AGENT_PRESETS.items():
        if key in select_keys and cmd == preset:
            return key
    return "agy"


class ModelPicker(Vertical):
    """Filterable model list: Current on top, third-party underneath."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._choices = []
        self._selected_value = ""
        self._id_to_value: dict[str, str] = {}

    def compose(self) -> ComposeResult:
        yield Label("Model")
        yield Input(placeholder="Filter models…  try composer, grok, gemini", id="model-filter")
        yield OptionList(id="model-list")

    def selected_value(self) -> str:
        return self._selected_value

    def set_loading(self) -> None:
        listing = self.query_one("#model-list", OptionList)
        listing.clear_options()
        listing.add_option(Option("Loading models…", disabled=True))

    def set_choices(self, choices, selected: str = "") -> None:
        self._choices = list(choices)
        if selected:
            self._selected_value = selected
        elif not self._selected_value and choices:
            self._selected_value = choices[0].value
        query = ""
        try:
            query = self.query_one("#model-filter", Input).value
        except Exception:
            pass
        self._rebuild(query)

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "model-filter":
            event.stop()
            self._rebuild(event.value)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        self._remember(event.option)

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        event.stop()
        self._remember(event.option)

    def _remember(self, option: Optional[Option]) -> None:
        if option is None or option.disabled or not option.id:
            return
        if option.id in self._id_to_value:
            self._selected_value = self._id_to_value[option.id]

    def _match(self, choice, query: str) -> bool:
        if not query:
            return True
        blob = f"{choice.label} {choice.key} {choice.value}".lower()
        return query in blob

    def _rebuild(self, query: str) -> None:
        listing = self.query_one("#model-list", OptionList)
        q = (query or "").strip().lower()
        current = [c for c in self._choices if c.group == "current" and self._match(c, q)]
        third = [c for c in self._choices if c.group == "third_party" and self._match(c, q)]
        options: list = []
        self._id_to_value = {}
        index = 0

        def add_group(title: str, items) -> None:
            nonlocal index
            if not items:
                return
            if options:
                options.append(None)
            options.append(Option(title, disabled=True))
            for choice in items:
                oid = f"m{index}"
                index += 1
                self._id_to_value[oid] = choice.value
                marker = "▸ " if choice.value == self._selected_value else "  "
                options.append(Option(f"{marker}{choice.label}", id=oid))

        add_group("Current", current)
        add_group("Third-party", third)
        listing.clear_options()
        if not options:
            listing.add_option(Option("No models match that filter", disabled=True))
            return
        listing.add_options(options)
        highlight = None
        for i in range(listing.option_count):
            option = listing.get_option_at_index(i)
            if option.disabled or getattr(option, "_divider", False) or not option.id:
                continue
            if highlight is None:
                highlight = i
            if self._id_to_value.get(option.id) == self._selected_value:
                highlight = i
                break
        if highlight is not None:
            listing.highlighted = highlight
            self._remember(listing.get_option_at_index(highlight))


def _sync_model_select(screen, agent_key: str) -> None:
    picker = screen.query_one("#model-picker", ModelPicker)
    show = agent_supports_models(agent_key)
    picker.display = show
    if show:
        picker.set_loading()
        screen.run_worker(_load_models(screen, agent_key), exclusive=True, group="models")


async def _load_models(screen, agent_key: str) -> None:
    choices = await asyncio.to_thread(list_agent_models, agent_key)
    if not screen.is_attached:
        return
    current = str(screen.query_one("#agent", Select).value)
    if current != agent_key:
        return
    screen.query_one("#model-picker", ModelPicker).set_choices(choices)


def _selected_model(screen) -> str:
    picker = screen.query_one("#model-picker", ModelPicker)
    if not picker.display:
        return ""
    return picker.selected_value()


def _default_types_text() -> str:
    return "\n".join(f"{t.name}: {t.description}" for t in DEFAULT_DOC_TYPES)


class SetupScreen(ModalScreen[Optional[dict]]):
    """First-time project setup. Init only runs in an empty docs folder."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def compose(self) -> ComposeResult:
        dash = get_dashboard()
        app_default = dash.app_repo_path or os.getcwd()
        docs_default = dash.docs_repo_path or default_docs_path(app_default)
        agent_default = _agent_key_from_dash(dash)
        with Vertical(id="dialog"):
            yield Label("Set up DocFlow", classes="title")
            yield Label("Application repo")
            yield Input(value=app_default, id="app-path")
            yield Label("Docs repo (must be empty)")
            yield Input(value=docs_default, id="docs-path")
            yield Label("Agent")
            yield Select(
                _agent_select_options(),
                value=agent_default,
                id="agent",
                allow_blank=False,
            )
            yield ModelPicker(id="model-picker")
            yield Label("Doc types (one per line: name: description)")
            yield TextArea(_default_types_text(), id="types")
            yield Label("Import from path/folder (optional, never overwrites)")
            yield Input(placeholder="leave blank to skip", id="import-from")
            yield Label("Import into type")
            yield Input(placeholder="defaults to the first type", id="import-into")
            with Horizontal(classes="buttons"):
                yield Button("Start", variant="primary", id="ok")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        _sync_model_select(self, str(self.query_one("#agent", Select).value))

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "agent":
            _sync_model_select(self, str(event.value))

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "cancel":
            self.dismiss(None)
            return
        agent_key = str(self.query_one("#agent", Select).value)
        types = parse_doc_types_text(self.query_one("#types", TextArea).text)
        self.dismiss(
            {
                "app": self.query_one("#app-path", Input).value.strip(),
                "docs": self.query_one("#docs-path", Input).value.strip(),
                "agent": agent_key,
                "model": _selected_model(self),
                "types": types or list(DEFAULT_DOC_TYPES),
                "import_from": self.query_one("#import-from", Input).value.strip(),
                "import_into": self.query_one("#import-into", Input).value.strip(),
            }
        )


class ImportScreen(ModalScreen[Optional[dict]]):
    """Copy existing files into a type folder. Never overwrites."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def compose(self) -> ComposeResult:
        dash = get_dashboard()
        default_type = ""
        if dash.doc_types:
            default_type = dash.doc_types[0].split(":")[0].strip()
        with Vertical(id="dialog"):
            yield Label("Import existing files", classes="title")
            yield Label("Path or folder")
            yield Input(placeholder="/path/to/old/docs", id="import-from")
            yield Label("Into doc type")
            yield Input(value=default_type, placeholder="front-end", id="import-into")
            with Horizontal(classes="buttons"):
                yield Button("Import", variant="primary", id="ok")
                yield Button("Cancel", id="cancel")

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "cancel":
            self.dismiss(None)
            return
        self.dismiss(
            {
                "import_from": self.query_one("#import-from", Input).value.strip(),
                "import_into": self.query_one("#import-into", Input).value.strip(),
            }
        )


class GenerateScreen(ModalScreen[Optional[dict]]):
    """Update existing docs from new commits, last N, or a full regen."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def compose(self) -> ComposeResult:
        dash = get_dashboard()
        self._repo = dash.app_repo_path if dash.app_exists else ""
        self._docs = dash.docs_repo_path or ""
        branches = []
        if self._repo:
            try:
                branches = list_app_branches(self._repo)
            except Exception:
                branches = []
        tip_options = [("HEAD (current checkout)", "HEAD")]
        for name in branches:
            if name != "HEAD":
                tip_options.append((name, name))
        with Vertical(id="dialog"):
            yield Label("Update documentation", classes="title")
            yield Label("What to use")
            yield Select(
                [
                    ("New commits since last update", "new"),
                    ("Last N commits", "commits"),
                    ("Full regeneration", "full"),
                ],
                value="new",
                id="source",
                allow_blank=False,
            )
            yield Label("Head / branch", id="tip-label")
            yield Select(tip_options, value="HEAD", id="tip", allow_blank=False)
            yield Label("How many commits back from that head", id="count-label")
            yield Input(value="1", id="commit-count")
            yield Label("Commits included")
            yield Static("Loading commit preview…", id="preview")
            yield Label("Feature / type (optional)")
            yield Input(placeholder="leave blank to infer from the diff", id="feature")
            yield Label("Agent")
            yield Select(
                _agent_select_options(),
                value=_agent_key_from_dash(dash),
                id="agent",
                allow_blank=False,
            )
            yield ModelPicker(id="model-picker")
            with Horizontal(classes="buttons"):
                yield Button("Generate", variant="primary", id="ok")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        self._apply_mode("new")
        _sync_model_select(self, str(self.query_one("#agent", Select).value))

    def _commit_count(self) -> int:
        raw = self.query_one("#commit-count", Input).value.strip()
        try:
            return max(1, int(raw))
        except ValueError:
            return 1

    def _tip(self) -> str:
        value = str(self.query_one("#tip", Select).value or "HEAD")
        return value if value != "Select.BLANK" else "HEAD"

    def _preview(self) -> str:
        source = str(self.query_one("#source", Select).value)
        if source == "full":
            return "Rebuild existing docs from the current codebase (not a commit range)."
        if not self._repo:
            return "No app repo configured."
        if source == "new":
            dash = get_dashboard()
            lines = []
            if dash.last_documented:
                lines.append(
                    f"Last documented: {dash.last_documented.short_sha}  {dash.last_documented.message}"
                )
            else:
                lines.append("No previous update recorded — will use the latest commit.")
            if dash.new_commits:
                lines.append(f"{len(dash.new_commits)} new commit(s):")
                for commit in dash.new_commits[:15]:
                    lines.append(f"{commit.short_sha}  {commit.message}")
                if len(dash.new_commits) > 15:
                    lines.append(f"… {len(dash.new_commits) - 15} more")
            else:
                lines.append("Nothing new. Pull to fetch from the server, or pick last N / full.")
            return "\n".join(lines)
        count = self._commit_count()
        try:
            commits = list_recent_commits(self._repo, count=count, rev=self._tip())
        except Exception as exc:
            return f"Could not read commits: {exc}"
        if not commits:
            return "No commits found on that head/branch."
        shown = commits[: min(len(commits), 20)]
        lines = [f"{c.short_sha}  {c.message}" for c in shown]
        if len(commits) > 20:
            lines.append(f"… {len(commits) - 20} more")
        return "\n".join(lines)

    def _refresh_preview(self) -> None:
        self.query_one("#preview", Static).update(self._preview())

    def _apply_mode(self, mode: str) -> None:
        show_range = mode == "commits"
        for widget_id in ("tip", "tip-label", "commit-count", "count-label"):
            self.query_one(f"#{widget_id}").display = show_range
        self._refresh_preview()

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "source":
            self._apply_mode(str(event.value))
        elif event.select.id == "tip":
            self._refresh_preview()
        elif event.select.id == "agent":
            _sync_model_select(self, str(event.value))

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "commit-count":
            self._refresh_preview()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "cancel":
            self.dismiss(None)
            return
        source = str(self.query_one("#source", Select).value)
        tip = self._tip()
        self.dismiss(
            {
                "full": source == "full",
                "since_last": source == "new",
                "commit_count": None if source != "commits" else self._commit_count(),
                "branch": "" if source != "commits" or tip == "HEAD" else tip,
                "feature": self.query_one("#feature", Input).value.strip(),
                "agent": str(self.query_one("#agent", Select).value),
                "model": _selected_model(self),
            }
        )


class PublishScreen(ModalScreen[Optional[dict]]):
    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def compose(self) -> ComposeResult:
        dash = get_dashboard()
        with Vertical(id="dialog"):
            yield Label("Publish documentation", classes="title")
            yield Label("Commit message")
            yield Input(value="docs: update documentation", id="message")
            yield Label("Platform")
            yield Select(
                [("GitHub", "github"), ("GitLab", "gitlab"), ("Generic", "generic")],
                value=dash.platform or "github",
                id="platform",
                allow_blank=False,
            )
            with Horizontal(classes="buttons"):
                yield Button("Publish", variant="primary", id="ok")
                yield Button("Cancel", id="cancel")

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "cancel":
            self.dismiss(None)
            return
        self.dismiss(
            {
                "message": self.query_one("#message", Input).value.strip(),
                "platform": str(self.query_one("#platform", Select).value),
            }
        )


class DocFlowApp(App[None]):
    TITLE = "DocFlow"
    SUB_TITLE = "Ready"
    CSS = """
    Screen {
        background: $surface;
    }
    #summary {
        height: auto;
        padding: 1 2;
        border: round $accent;
        margin: 1 1 0 1;
    }
    #busy {
        height: 1;
        margin: 0 1;
    }
    #log-scroll {
        margin: 1;
        border: round $primary;
        height: 1fr;
        padding: 0 1;
    }
    #log {
        height: auto;
    }
    #actions {
        height: auto;
        padding: 0 1 1 1;
    }
    #actions Button {
        margin-right: 1;
    }
    #dialog {
        width: 84;
        height: auto;
        max-height: 90%;
        padding: 1 2;
        border: round $accent;
        background: $surface;
        margin: 1 3;
        overflow-y: auto;
    }
    #dialog .title {
        text-style: bold;
        margin-bottom: 1;
    }
    #dialog Input, #dialog Select, #dialog TextArea, #preview, #model-picker {
        margin-bottom: 1;
    }
    #dialog TextArea {
        height: 8;
    }
    #model-picker {
        height: auto;
    }
    #model-list {
        height: 14;
        border: tall $border-blurred;
        background: $surface;
        padding: 0 1;
    }
    #model-filter {
        margin-bottom: 0;
    }
    #preview {
        color: $text-muted;
        height: auto;
        max-height: 8;
    }
    .buttons {
        height: auto;
        margin-top: 1;
    }
    .buttons Button {
        margin-right: 1;
    }
    """

    BINDINGS = [
        Binding("g", "generate", "Update"),
        Binding("u", "pull", "Pull"),
        Binding("p", "publish", "Publish"),
        Binding("i", "setup", "Setup"),
        Binding("r", "refresh", "Refresh"),
        Binding("m", "mcp", "MCP"),
        Binding("q", "quit", "Quit"),
    ]

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static("Loading…", id="summary")
        yield LoadingIndicator(id="busy")
        with VerticalScroll(id="log-scroll"):
            yield Static("Ready. Press u to pull, g to update docs from new commits.", id="log")
        with Horizontal(id="actions"):
            yield Button("Setup", id="btn-setup")
            yield Button("Pull", id="btn-pull")
            yield Button("Update docs", variant="primary", id="btn-generate")
            yield Button("Publish", id="btn-publish")
            yield Button("MCP (SSE)", id="btn-mcp")
            yield Button("Refresh", id="btn-refresh")
        yield Footer()

    def on_mount(self) -> None:
        self._lines: deque[str] = deque(maxlen=400)
        self.query_one("#busy", LoadingIndicator).display = False
        self.sub_title = "Ready"
        self.refresh_summary()

    def _short(self, message: str) -> str:
        return message.strip().split("\n")[0][:90]

    def _render_log(self) -> None:
        text = "\n".join(self._lines) if self._lines else ""
        self.query_one("#log", Static).update(text)

    def _log(self, message: str) -> None:
        for line in reversed(message.strip().splitlines() or [message]):
            self._lines.appendleft(line)
        self._render_log()
        self.query_one("#log-scroll", VerticalScroll).scroll_home(animate=False)

    def _progress(self, message: str) -> None:
        self.sub_title = self._short(message)
        self._log(message)

    def _thread_progress(self, message: str) -> None:
        self.call_from_thread(self._progress, message)

    def _begin_run(self, title: str) -> None:
        self._lines.clear()
        self._render_log()
        self.sub_title = title
        self._set_busy(True)

    def _finish_run(self, summary: str) -> None:
        self.sub_title = self._short(summary)
        self._set_busy(False)
        self._log(summary)

    def _set_busy(self, busy: bool) -> None:
        self.query_one("#busy", LoadingIndicator).display = busy
        for button_id in ("btn-setup", "btn-pull", "btn-generate", "btn-publish", "btn-mcp", "btn-refresh"):
            self.query_one(f"#{button_id}", Button).disabled = busy

    def refresh_summary(self) -> None:
        dash = get_dashboard()
        lines = [
            f"[bold]{dash.project_name}[/bold]",
            f"App   {dash.app_repo_path or 'not set'}  ({'ok' if dash.app_exists else 'missing'})",
            f"Docs  {dash.docs_repo_path or 'not set'}  ({'ok' if dash.docs_exists else 'missing'})",
            f"Agent {dash.agent_mode}  {dash.agent_command or 'manual'}",
            f"Types: {', '.join(dash.doc_types) or 'none'}",
            f"Documented: {dash.last_documented.short_sha}  {dash.last_documented.message}"
            if dash.last_documented
            else "Documented: none yet",
            f"New commits ({len(dash.new_commits)}): "
            + (
                ", ".join(f"{c.short_sha} {c.message}" for c in dash.new_commits[:3])
                or "none"
            ),
            f"Features ({len(dash.features)}): {', '.join(dash.features) or 'none'}",
            f"Pending prompts ({len(dash.pending)}): {', '.join(dash.pending) or 'none'}",
        ]
        if dash.source_path:
            lines.append(f"Config {dash.source_path}")
        setup_btn = self.query_one("#btn-setup", Button)
        setup_btn.label = "Import" if dash.configured else "Setup"
        self.query_one("#summary", Static).update("\n".join(lines))

    def _run_dialog(self, coro) -> None:
        # Textual 8: push_screen_wait must run inside a worker, not a message handler.
        self.run_worker(coro, exclusive=True, group="dialog")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        mapping = {
            "btn-setup": self.action_setup,
            "btn-pull": self.action_pull,
            "btn-generate": self.action_generate,
            "btn-publish": self.action_publish,
            "btn-mcp": self.action_mcp,
            "btn-refresh": self.action_refresh,
        }
        action = mapping.get(event.button.id or "")
        if action:
            event.stop()
            action()

    def action_refresh(self) -> None:
        self.refresh_summary()
        dash = get_dashboard()
        if dash.new_commits:
            self.sub_title = f"{len(dash.new_commits)} new commit(s) waiting"
        elif dash.last_documented:
            self.sub_title = f"Documented through {dash.last_documented.short_sha}"
        else:
            self.sub_title = "Ready"

    def action_pull(self) -> None:
        self._run_dialog(self._do_pull())

    def action_setup(self) -> None:
        dash = get_dashboard()
        if dash.configured:
            self._run_dialog(self._do_import())
        else:
            self._run_dialog(self._do_setup())

    def action_generate(self) -> None:
        self._run_dialog(self._do_generate())

    def action_publish(self) -> None:
        self._run_dialog(self._do_publish())

    def action_mcp(self) -> None:
        self._run_dialog(self._do_mcp())

    async def _do_refresh(self) -> None:
        self.action_refresh()

    async def _do_pull(self) -> None:
        try:
            paths = resolve_paths(require=True)
        except ConfigError:
            self._log("Not configured yet. Run Setup first (press i).")
            return
        self._begin_run("git pull…")
        try:
            result = await asyncio.to_thread(
                pull_app_repo,
                paths.app_repo_path,
                paths.docs_repo_path,
                self._thread_progress,
            )
        except Exception as exc:
            self._finish_run(f"git pull failed: {exc}")
            self.refresh_summary()
            return
        if not result.success:
            self._finish_run(f"git pull failed: {result.output or 'unknown error'}")
        elif result.new_commits:
            self._finish_run(
                f"Pulled {len(result.new_commits)} new commit(s). Update docs to cover them."
            )
        elif result.already_up_to_date:
            self._finish_run("Already up to date with the remote.")
        else:
            self._finish_run("git pull finished. No new commits since last docs update.")
        self.refresh_summary()

    async def _do_setup(self) -> None:
        data = await self.push_screen_wait(SetupScreen())
        if not data:
            return
        spec = resolve_agent(agent=data["agent"], model=data.get("model"))
        if spec is None:
            spec = resolve_agent(agent="manual")
        self._begin_run("Setup running…")
        try:
            result = await asyncio.to_thread(
                init_docs,
                app_repo_path=data["app"],
                docs_repo_path=data["docs"],
                agent=spec,
                capture_output=True,
                on_progress=self._thread_progress,
                types=data["types"],
                import_from=data.get("import_from") or None,
                import_into=data.get("import_into") or None,
            )
        except Exception as exc:
            self._finish_run(f"Setup failed: {exc}")
            return
        ok = sum(1 for f in result.features if f.success)
        imported = f", imported {len(result.imported_copied)}" if result.imported_copied else ""
        self._finish_run(
            f"Setup complete: {ok}/{len(result.features)} sections{imported} → {result.docs_repo_path}"
        )
        self.refresh_summary()

    async def _do_import(self) -> None:
        data = await self.push_screen_wait(ImportScreen())
        if not data or not data.get("import_from"):
            return
        try:
            paths = resolve_paths(require=False)
        except ConfigError:
            paths = None
        docs = paths.docs_repo_path if paths else ""
        if not docs:
            self._finish_run("Import failed: docs path is not set. Run Setup first.")
            return
        type_name = data.get("import_into") or "docs"
        self._begin_run(f"Importing into {type_name}…")
        try:
            result = await asyncio.to_thread(
                import_docs,
                data["import_from"],
                docs,
                type_name,
                self._thread_progress,
            )
        except Exception as exc:
            self._finish_run(f"Import failed: {exc}")
            return
        self._finish_run(
            f"Import finished: {len(result.copied)} copied, {len(result.skipped)} skipped → {result.dest_type}/"
        )
        self.refresh_summary()

    async def _do_generate(self) -> None:
        try:
            paths = resolve_paths(require=True)
        except ConfigError:
            self.sub_title = "Not configured — run Setup"
            self._log("Not configured yet. Run Setup first (press i).")
            await self._do_setup()
            return
        data = await self.push_screen_wait(GenerateScreen())
        if not data:
            return
        spec = resolve_agent(
            agent=data.get("agent"), model=data.get("model"), config=paths.config
        ) or resolve_agent(config=paths.config) or resolve_agent(agent="manual")
        branch = data.get("branch") or ""
        if data["full"]:
            label = "Full regeneration"
        elif data.get("since_last"):
            label = "Updating new commits since last docs update"
        elif branch:
            n = data.get("commit_count") or 1
            label = f"Updating {n} commit(s) on {branch}"
        else:
            n = data.get("commit_count") or 1
            label = f"Updating last {n} commit" + ("" if n == 1 else "s") + " on HEAD"
        self._begin_run(f"{label}…")
        try:
            result = await asyncio.to_thread(
                generate_docs,
                app_repo_path=paths.app_repo_path,
                docs_repo_path=paths.docs_repo_path,
                agent=spec,
                config=paths.config,
                from_ref="",
                to_ref="",
                branch=branch,
                feature=data["feature"],
                full=data["full"],
                capture_output=True,
                on_progress=self._thread_progress,
                commit_count=data.get("commit_count"),
            )
        except Exception as exc:
            self._finish_run(f"Update failed: {exc}")
            return
        if result.already_current:
            self._finish_run("Already documented. Pull to fetch new commits, or use last N / full.")
        elif result.no_changes:
            self._finish_run("Update finished: no changed files in those commits")
        elif result.run and result.run.success:
            if result.commits:
                tip = result.commits[0]
                self._finish_run(
                    f"Update finished: {result.task_type} / {result.feature_name} "
                    f"({result.commit_count} commit(s), {tip.short_sha} {tip.message})"
                )
            else:
                self._finish_run(f"Update finished: {result.task_type} / {result.feature_name}")
        elif result.run:
            self._finish_run(f"Update failed: {result.run.error_message}")
        else:
            self._finish_run("Update finished")
        self.refresh_summary()

    async def _do_publish(self) -> None:
        try:
            paths = resolve_paths(require=False)
        except ConfigError:
            paths = None
        docs = paths.docs_repo_path if paths else ""
        if not docs:
            self.sub_title = "Docs path is not set"
            self._log("Docs path is not set. Run Setup first.")
            return
        data = await self.push_screen_wait(PublishScreen())
        if not data:
            return
        self._begin_run("Publishing docs…")
        try:
            result = await asyncio.to_thread(
                publish_docs,
                docs,
                paths.config if paths else None,
                data["platform"],
                data["message"],
            )
        except Exception as exc:
            self._finish_run(f"Publish failed: {exc}")
            return
        if result.mr_url:
            self._finish_run(f"Published {result.branch} → {result.mr_url}")
        elif result.mr_message:
            self._finish_run(f"Published {result.branch}: {result.mr_message}")
        else:
            self._finish_run(f"Published {result.branch}: {result.commit}")

    async def _do_mcp(self) -> None:
        try:
            paths = resolve_paths(require=True)
        except ConfigError as exc:
            self.sub_title = "Not configured"
            self._log(str(exc))
            return
        port = 8080
        docs = paths.docs_repo_path
        self._begin_run(f"Starting MCP SSE on :{port}…")

        def _run() -> None:
            from docflow.mcp.server import create_mcp_server

            server = create_mcp_server(docs)
            server.run(transport="sse", port=port)

        thread = threading.Thread(target=_run, name="docflow-mcp-sse", daemon=True)
        thread.start()
        self._finish_run(f"MCP SSE running for {docs} on port {port}")


def run_tui() -> None:
    DocFlowApp().run()
