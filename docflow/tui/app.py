"""
DocFlow Textual TUI.
"""

from __future__ import annotations

import asyncio
import os
import re
from collections import deque
from typing import Optional

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Footer, Header, Input, Label, LoadingIndicator, Log, OptionList, ProgressBar, Select, SelectionList, Static, TextArea
from textual.widgets.option_list import Option
from textual.widgets.selection_list import Selection

from docflow.core.agent_runner import AGENT_PRESETS
from docflow.core.job_runner import RunControl
from docflow.core.operations import (
    AGENT_CHOICES,
    CURSOR_AGENT_KEYS,
    ConfigError,
    DEFAULT_CURSOR_MODEL,
    DEFAULT_DOC_TYPES,
    InitCancelled,
    SectionCandidate,
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
    selected_sections,
)
from docflow.core.projects import load_index, open_project

_STATUS_RE = re.compile(
    r"^(?:"
    r"\[(?:\d+/\d+|failed|done|running)\b|"
    r"Scanning |Writing |Running |Generating |Importing |Waiting |"
    r"Creating |Including |Stack survey|git |Remote is |"
    r"Setup |Update |Published|Pulled |Already |No sections"
    r")",
    re.IGNORECASE,
)
_FRACTION_RE = re.compile(r"\[(\d+)/(\d+)")


def is_status_line(message: str) -> bool:
    """True for DocFlow step lines; False for agent/tool stdout that belongs in Logs."""
    stripped = (message or "").strip()
    if not stripped:
        return False
    if "\n" in stripped and len(stripped) > 160:
        return False
    if len(stripped) > 240:
        return False
    if stripped.startswith(("✓", "✗", "•")):
        return True
    return bool(_STATUS_RE.match(stripped))


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
    """Filterable model list: included usage on top, third-party underneath."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._choices = []
        self._selected_value = ""
        self._id_to_value: dict[str, str] = {}
        self._suppress_highlight = False

    def compose(self) -> ComposeResult:
        yield Label("Model")
        yield Input(placeholder="Filter models…  try composer, grok, gemini", id="model-filter")
        yield OptionList(id="model-list")

    def selected_value(self) -> str:
        return self._selected_value

    def set_loading(self) -> None:
        self._selected_value = ""
        listing = self.query_one("#model-list", OptionList)
        listing.clear_options()
        listing.add_option(Option("Loading models…", disabled=True))

    def set_choices(self, choices, selected: str = "") -> None:
        self._choices = list(choices)
        if selected:
            self._selected_value = selected
        elif not self._selected_value and choices:
            keys = {c.key: c.value for c in choices}
            if DEFAULT_CURSOR_MODEL in keys:
                self._selected_value = keys[DEFAULT_CURSOR_MODEL]
            else:
                current = [c for c in choices if c.group == "current"]
                self._selected_value = (current[0] if current else choices[0]).value
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
        if self._suppress_highlight:
            return
        self._remember(event.option)

    def _remember(self, option: Optional[Option]) -> None:
        if option is None or option.disabled or not option.id:
            return
        if option.id in self._id_to_value:
            self._selected_value = self._id_to_value[option.id]

    def _end_suppress_highlight(self) -> None:
        self._suppress_highlight = False

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

        add_group(
            (current[0].group_label if current else "") or "Cursor included usage",
            current,
        )
        add_group(
            (third[0].group_label if third else "") or "Third-party API usage",
            third,
        )
        listing.clear_options()
        if not options:
            listing.add_option(Option("No models match that filter", disabled=True))
            return
        self._suppress_highlight = True
        wanted = self._selected_value
        listing.add_options(options)
        highlight = None
        for i in range(listing.option_count):
            option = listing.get_option_at_index(i)
            if option.disabled or getattr(option, "_divider", False) or not option.id:
                continue
            if highlight is None:
                highlight = i
            if self._id_to_value.get(option.id) == wanted:
                highlight = i
                break
        if highlight is not None:
            listing.highlighted = highlight
        self.call_after_refresh(self._end_suppress_highlight)


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


def _resolve_screen_model(agent_key: str, model: Optional[str]) -> str:
    chosen = (model or "").strip()
    if agent_key in CURSOR_AGENT_KEYS and not chosen:
        return DEFAULT_CURSOR_MODEL
    return chosen


def _default_types_text() -> str:
    return "\n".join(f"{t.name}: {t.description}" for t in DEFAULT_DOC_TYPES)


class ProjectPickerScreen(ModalScreen[Optional[dict]]):
    """Open or switch a registered docs project. Never writes into the app repo."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def compose(self) -> ComposeResult:
        self._id_to_entry = {}
        with Vertical(id="dialog"):
            yield Label("Open a docs project", classes="title")
            yield OptionList(id="project-list")
            with Horizontal(classes="buttons"):
                yield Button("Open", variant="primary", id="ok")
                yield Button("New", id="new")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        listing = self.query_one("#project-list", OptionList)
        listing.clear_options()
        entries = load_index()
        if not entries:
            listing.add_option(Option("No projects yet — choose New", disabled=True))
            return
        for i, entry in enumerate(entries):
            oid = f"p{i}"
            self._id_to_entry[oid] = entry
            listing.add_option(Option(f"{entry.name}  {entry.docs_path}", id=oid))
        listing.highlighted = 0

    def action_cancel(self) -> None:
        self.dismiss(None)

    def _selected_entry(self):
        listing = self.query_one("#project-list", OptionList)
        if listing.highlighted is None:
            return None
        option = listing.get_option_at_index(listing.highlighted)
        if option is None or option.disabled or not option.id:
            return None
        return self._id_to_entry.get(option.id)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "cancel":
            self.dismiss(None)
            return
        if event.button.id == "new":
            self.dismiss({"new": True})
            return
        entry = self._selected_entry()
        if entry is None:
            self.dismiss(None)
            return
        open_project(entry.docs_path)
        self.dismiss({"docs": entry.docs_path, "repo": entry.app_path, "new": False})


def _screen_dashboard(widget):
    app = getattr(widget, "app", None)
    repo = getattr(app, "_repo", "") or None
    docs = getattr(app, "_docs", "") or None
    return get_dashboard(repo, docs)


class SetupScreen(ModalScreen[Optional[dict]]):
    """First-time project setup. Init only runs in an empty docs folder."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def compose(self) -> ComposeResult:
        dash = _screen_dashboard(self)
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


class SectionPickerScreen(ModalScreen[Optional[list]]):
    """After scan: choose which discovered sections to document, and add extras."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, candidates: list, **kwargs) -> None:
        super().__init__(**kwargs)
        self._items = list(candidates)

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label("What should DocFlow document?", classes="title")
            yield Static(
                "Space toggles a section. Uncheck git/CI/tooling. "
                "Add a module name or path if the scan missed something.",
                id="section-help",
            )
            yield SelectionList(id="section-list")
            yield Label("Add a module name or path")
            yield Input(placeholder="app/Services or payments", id="add-path")
            with Horizontal(classes="buttons"):
                yield Button("Add", id="add")
                yield Button("Continue", variant="primary", id="ok")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        self._rebuild()

    def _rebuild(self) -> None:
        listing = self.query_one("#section-list", SelectionList)
        listing.clear_options()
        for i, item in enumerate(self._items):
            listing.add_option(Selection(item.label, f"s{i}", item.included))

    def _sync_included(self) -> None:
        listing = self.query_one("#section-list", SelectionList)
        selected = set(listing.selected)
        for i, item in enumerate(self._items):
            item.included = f"s{i}" in selected

    def _add_extra(self) -> None:
        raw = self.query_one("#add-path", Input).value.strip()
        if not raw:
            return
        self._sync_included()
        self._items.append(
            SectionCandidate(
                doc_type="features",
                name=raw,
                description=f"Extra section '{raw}'",
                included=True,
                extra=True,
            )
        )
        self.query_one("#add-path", Input).value = ""
        self._rebuild()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "add-path":
            event.stop()
            self._add_extra()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "cancel":
            self.dismiss(None)
            return
        if event.button.id == "add":
            self._add_extra()
            return
        self._sync_included()
        picked = selected_sections(self._items)
        if not picked:
            self.query_one("#section-help", Static).update(
                "Select at least one section, or add a module/path."
            )
            return
        self.dismiss(self._items)


class ImportScreen(ModalScreen[Optional[dict]]):
    """Copy existing files into a type folder. Never overwrites."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def compose(self) -> ComposeResult:
        dash = _screen_dashboard(self)
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
        dash = _screen_dashboard(self)
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
            dash = _screen_dashboard(self)
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
                lines.append("Nothing new locally. Update docs will fetch the remote first.")
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


class RegenLastScreen(ModalScreen[Optional[dict]]):
    """Offer to redo the last documented commit with another agent/model."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def compose(self) -> ComposeResult:
        dash = _screen_dashboard(self)
        with Vertical(id="dialog"):
            yield Label("Already documented", classes="title")
            yield Static(
                "HEAD is already covered by the last docs update.\n"
                "Regenerate that commit with another LLM, or exit."
            )
            if dash.last_documented:
                yield Label(
                    f"Last documented: {dash.last_documented.short_sha}  "
                    f"{dash.last_documented.message}"
                )
            yield Label("Agent")
            yield Select(
                _agent_select_options(),
                value=_agent_key_from_dash(dash),
                id="agent",
                allow_blank=False,
            )
            yield ModelPicker(id="model-picker")
            with Horizontal(classes="buttons"):
                yield Button("Regenerate", variant="primary", id="ok")
                yield Button("Exit", id="cancel")

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
        self.dismiss(
            {
                "agent": str(self.query_one("#agent", Select).value),
                "model": _selected_model(self),
            }
        )


class PublishScreen(ModalScreen[Optional[dict]]):
    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def compose(self) -> ComposeResult:
        dash = _screen_dashboard(self)
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
    #busy-row {
        height: auto;
        padding: 0 1;
        margin-top: 1;
    }
    #busy {
        width: auto;
        margin-right: 1;
    }
    #step {
        width: 1fr;
        height: auto;
        color: $text-muted;
    }
    #run-progress {
        width: 24;
        margin-left: 1;
    }
    #main {
        height: 1fr;
    }
    #work {
        height: 1fr;
        margin: 0 1;
    }
    #progress-pane, #log-pane {
        height: 1fr;
        border: round $primary;
        padding: 0 1 1 1;
    }
    #progress-pane {
        width: 2fr;
        margin-right: 1;
    }
    #log-pane {
        width: 3fr;
    }
    .pane-title {
        text-style: bold;
        height: 1;
        margin: 0 0 1 0;
    }
    #progress-scroll {
        height: 1fr;
    }
    #progress {
        height: auto;
    }
    #log {
        height: 1fr;
        background: $surface;
        padding: 0;
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
    #dialog Input, #dialog Select, #dialog TextArea, #preview, #model-picker, #section-list {
        margin-bottom: 1;
    }
    #section-list {
        height: 16;
        border: tall $border-blurred;
        background: $surface;
        padding: 0 1;
    }
    #section-help {
        color: $text-muted;
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
        Binding("s", "switch", "Switch"),
        Binding("m", "mcp", "MCP"),
        Binding("f8", "toggle_pause", "Pause/Resume"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self, repo: str = "", docs: str = "", **kwargs) -> None:
        super().__init__(**kwargs)
        self._repo = repo or ""
        self._docs = docs or ""

    def _dashboard(self):
        return get_dashboard(self._repo or None, self._docs or None)

    def _resolve(self, require: bool = True):
        return resolve_paths(self._repo or None, self._docs or None, require=require)

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Vertical(id="main"):
            yield Static("Loading…", id="summary")
            with Horizontal(id="busy-row"):
                yield LoadingIndicator(id="busy")
                yield Static("Ready", id="step")
                yield ProgressBar(id="run-progress", total=None, show_eta=False, show_percentage=False)
            with Horizontal(id="work"):
                with Vertical(id="progress-pane"):
                    yield Static("Progress", classes="pane-title", id="progress-title")
                    with VerticalScroll(id="progress-scroll"):
                        yield Static("Ready. Press u to pull, g to update docs from new commits.", id="progress")
                with Vertical(id="log-pane"):
                    yield Static("Logs", classes="pane-title", id="log-title")
                    yield Log(id="log", highlight=False, max_lines=500)
            with Horizontal(id="actions"):
                yield Button("Setup", id="btn-setup")
                yield Button("Pull", id="btn-pull")
                yield Button("Update docs", variant="primary", id="btn-generate")
                yield Button("Publish", id="btn-publish")
                yield Button("MCP (SSE)", id="btn-mcp")
                yield Button("Refresh", id="btn-refresh")
                yield Button("Switch", id="btn-switch")
                yield Button("Pause", id="btn-pause", disabled=True)
        yield Footer()

    def on_mount(self) -> None:
        self._progress_lines: deque[str] = deque(maxlen=80)
        self._log_lines: deque[str] = deque(maxlen=500)
        self._paused_progress: list[str] = []
        self._paused_logs: list[str] = []
        self._run_control = RunControl()
        self._busy = False
        self.query_one("#busy", LoadingIndicator).display = False
        self.query_one("#run-progress", ProgressBar).display = False
        self.sub_title = "Ready"
        self.refresh_summary()

    def _short(self, message: str) -> str:
        return message.strip().split("\n")[0][:90]

    def _render_progress(self) -> None:
        text = "\n".join(self._progress_lines) if self._progress_lines else ""
        self.query_one("#progress", Static).update(text or "—")

    def _set_step(self, message: str) -> None:
        short = self._short(message) if message else "Ready"
        self.query_one("#step", Static).update(short)
        self.sub_title = short

    def _update_fraction(self, message: str) -> None:
        bar = self.query_one("#run-progress", ProgressBar)
        match = _FRACTION_RE.search(message or "")
        if match:
            current, total = int(match.group(1)), int(match.group(2))
            bar.display = True
            bar.update(total=max(total, 1), progress=min(current, total))
        elif self._busy:
            bar.display = True

    def _append_progress(self, message: str) -> None:
        line = message.strip()
        if not line:
            return
        self._progress_lines.append(line)
        self._set_step(line)
        self._update_fraction(line)
        if self._run_control.paused:
            self._paused_progress.append(line)
            return
        self._render_progress()
        self.query_one("#progress-scroll", VerticalScroll).scroll_end(animate=False)

    def _append_log(self, message: str) -> None:
        for line in message.splitlines() or [message]:
            text = line.rstrip()
            if not text:
                continue
            self._log_lines.append(text)
            if self._run_control.paused:
                self._paused_logs.append(text)
                continue
            self.query_one("#log", Log).write_line(text)

    def _log(self, message: str) -> None:
        self._append_log(message)

    def _progress(self, message: str) -> None:
        if is_status_line(message):
            self._append_progress(message)
        else:
            self._append_log(message)

    def _thread_progress(self, message: str) -> None:
        self.call_from_thread(self._progress, message)

    def _begin_run(self, title: str) -> None:
        self._progress_lines.clear()
        self._log_lines.clear()
        self._paused_progress.clear()
        self._paused_logs.clear()
        self._run_control.resume()
        self._render_progress()
        self.query_one("#log", Log).clear()
        self._append_progress(title)
        self._set_busy(True)

    def _finish_run(self, summary: str) -> None:
        if self._run_control.paused:
            self._resume_output()
        self._append_progress(summary)
        self._set_busy(False)

    def _pause_output(self) -> None:
        if not self._busy or self._run_control.paused:
            return
        self._run_control.pause()
        self.query_one("#btn-pause", Button).label = "Resume"
        self.query_one("#log-title", Static).update("Logs  (paused — F8 or Resume)")
        self.query_one("#progress-title", Static).update("Progress  (paused)")

    def _resume_output(self) -> None:
        if not self._run_control.paused:
            return
        self._run_control.resume()
        if self._paused_progress:
            self._render_progress()
            self.query_one("#progress-scroll", VerticalScroll).scroll_end(animate=False)
            self._paused_progress.clear()
        if self._paused_logs:
            self.query_one("#log", Log).write_lines(self._paused_logs)
            self._paused_logs.clear()
        self.query_one("#btn-pause", Button).label = "Pause"
        self.query_one("#log-title", Static).update("Logs")
        self.query_one("#progress-title", Static).update("Progress")

    def action_toggle_pause(self) -> None:
        if not self._busy:
            return
        if self._run_control.paused:
            self._resume_output()
        else:
            self._pause_output()

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.query_one("#busy", LoadingIndicator).display = busy
        bar = self.query_one("#run-progress", ProgressBar)
        bar.display = busy
        if not busy:
            self._run_control.resume()
            self.query_one("#btn-pause", Button).label = "Pause"
            self.query_one("#log-title", Static).update("Logs")
            self.query_one("#progress-title", Static).update("Progress")
        pause_btn = self.query_one("#btn-pause", Button)
        pause_btn.disabled = not busy
        for button_id in ("btn-setup", "btn-pull", "btn-generate", "btn-publish", "btn-mcp", "btn-refresh", "btn-switch"):
            self.query_one(f"#{button_id}", Button).disabled = busy

    def refresh_summary(self) -> None:
        dash = self._dashboard()
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
            "btn-switch": self.action_switch,
            "btn-pause": self.action_toggle_pause,
        }
        action = mapping.get(event.button.id or "")
        if action:
            event.stop()
            action()

    def action_refresh(self) -> None:
        self.refresh_summary()
        dash = self._dashboard()
        if dash.new_commits:
            self.sub_title = f"{len(dash.new_commits)} new commit(s) waiting"
        elif dash.last_documented:
            self.sub_title = f"Documented through {dash.last_documented.short_sha}"
        else:
            self.sub_title = "Ready"

    def action_pull(self) -> None:
        self._run_dialog(self._do_pull())

    def action_setup(self) -> None:
        dash = self._dashboard()
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
            paths = self._resolve(require=True)
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
        spec = resolve_agent(
            agent=data["agent"],
            model=_resolve_screen_model(data["agent"], data.get("model")),
        )
        if spec is None:
            spec = resolve_agent(agent="manual")
        self._begin_run("Scanning the app repo…")
        loop = asyncio.get_running_loop()

        def review(candidates):
            future = asyncio.run_coroutine_threadsafe(
                self.push_screen_wait(SectionPickerScreen(list(candidates))),
                loop,
            )
            return future.result()

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
                on_review_sections=review,
                run_control=self._run_control,
            )
        except InitCancelled as exc:
            self._finish_run(str(exc))
            return
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
            paths = self._resolve(require=False)
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

    def action_switch(self) -> None:
        self._run_dialog(self._do_switch())

    async def _do_switch(self) -> Optional[dict]:
        picked = await self.push_screen_wait(ProjectPickerScreen())
        if not picked:
            return None
        if picked.get("new"):
            await self._do_setup()
            return picked
        self._docs = picked.get("docs") or ""
        self._repo = picked.get("repo") or ""
        self.refresh_summary()
        self._log(f"Opened {self._docs}")
        return picked

    async def _do_generate(self) -> None:
        try:
            paths = self._resolve(require=True)
        except ConfigError:
            self._log("No docs project is open. Pick one from Switch, or create one with Setup.")
            picked = await self._do_switch()
            if not picked or picked.get("new"):
                return
            try:
                paths = self._resolve(require=True)
            except ConfigError:
                self._finish_run("Still no project selected. Use Switch or Setup.")
                return
        data = await self.push_screen_wait(GenerateScreen())
        if not data:
            return
        spec = resolve_agent(
            agent=data.get("agent"),
            model=_resolve_screen_model(data.get("agent") or "", data.get("model")),
            config=paths.config,
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
                run_control=self._run_control,
            )
        except Exception as exc:
            self._finish_run(f"Update failed: {exc}")
            return
        if result.already_current:
            self._set_busy(False)
            self.refresh_summary()
            regen = await self.push_screen_wait(RegenLastScreen())
            if not regen:
                self._finish_run("Already documented. Exited without regenerating.")
                return
            spec = resolve_agent(
                agent=regen.get("agent"),
                model=_resolve_screen_model(regen.get("agent") or "", regen.get("model")),
                config=paths.config,
            ) or spec
            self._begin_run("Regenerating last documented commit…")
            try:
                result = await asyncio.to_thread(
                    generate_docs,
                    app_repo_path=paths.app_repo_path,
                    docs_repo_path=paths.docs_repo_path,
                    agent=spec,
                    config=paths.config,
                    from_ref="",
                    to_ref="",
                    branch="",
                    feature=data["feature"],
                    full=False,
                    capture_output=True,
                    on_progress=self._thread_progress,
                    commit_count=1,
                    sync_remote=False,
                    run_control=self._run_control,
                )
            except Exception as exc:
                self._finish_run(f"Regenerate failed: {exc}")
                return
        if result.already_current:
            self._finish_run("Already documented through current HEAD.")
        elif result.no_changes:
            self._finish_run("Update finished: no changed files in those commits")
        elif result.features and not all(item.success for item in result.features):
            failed = [item.feature_name for item in result.features if not item.success]
            self._finish_run(f"Update failed for: {', '.join(failed)}")
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
            paths = self._resolve(require=False)
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
            paths = self._resolve(require=True)
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


def run_tui(repo: str = "", docs: str = "") -> None:
    DocFlowApp(repo=repo, docs=docs).run()
