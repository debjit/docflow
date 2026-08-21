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
from docflow.core.job_runner import RunControl, clamp_concurrency
from docflow.core.operations import (
    AGENT_CHOICES,
    CURSOR_AGENT_KEYS,
    ConfigError,
    DEFAULT_CURSOR_MODEL,
    DEFAULT_CURSOR_PLAN_MODEL,
    DEFAULT_CURSOR_WORK_MODEL,
    DEFAULT_DOC_TYPES,
    InitCancelled,
    SectionCandidate,
    agent_supports_models,
    default_docs_path,
    generate_docs,
    get_dashboard,
    group_candidates,
    import_docs,
    init_docs,
    kind_heading,
    list_agent_models,
    default_cursor_plan_model,
    default_cursor_work_model,
    list_app_branches,
    default_app_branch,
    new_commits_since,
    resolve_branch_rev,
    list_recent_commits,
    parse_doc_types_text,
    picker_group,
    publish_docs,
    pull_app_repo,
    resolve_agent,
    resolve_paths,
    selected_sections,
    command_without_model,
)
from docflow.core.projects import load_index, open_project, remove_project

_STATUS_RE = re.compile(
    r"^(?:"
    r"\[(?:\d+/\d+|failed|done|running)\b|"
    r"Scanning |Writing |Running |Generating |Importing |Waiting |"
    r"Creating |Including |Stack survey|git |Remote is |"
    r"Setup |Update |Published|Pulled |Already |No sections|"
    r"Application |Checking "
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
    name = (getattr(dash, "agent_name", "") or "").strip()
    select_keys = {key for key, _ in AGENT_CHOICES if key != "custom"}
    if name in select_keys:
        return name
    if not dash.configured:
        return "agy"
    if dash.agent_mode == "manual":
        return "manual"
    cmd = dash.agent_command or ""
    for key, preset in AGENT_PRESETS.items():
        if key in select_keys and command_without_model(cmd) == command_without_model(preset):
            if key != "cursor":
                return key
    return "agy"


def _branch_select_options(repo: str, selected: str = ""):
    names: list = []
    if repo:
        try:
            names = list_app_branches(repo)
        except Exception:
            names = []
    default = (selected or "").strip() or (default_app_branch(repo) if repo else "HEAD")
    options = []
    seen = set()
    for name in names:
        if name and name not in seen:
            options.append((name, name))
            seen.add(name)
    if default and default not in seen:
        options.insert(0, (default, default))
        seen.add(default)
    if not options:
        options = [("HEAD", "HEAD")]
        default = "HEAD"
    if default not in {value for _label, value in options}:
        default = options[0][1]
    return options, default


class ModelPicker(Vertical):
    """Filterable model list: included usage on top, third-party underneath."""

    def __init__(self, caption: str = "Model", role: str = "work", **kwargs) -> None:
        super().__init__(**kwargs)
        self._caption = caption
        self.role = role
        self._choices = []
        self._selected_value = ""
        self._id_to_value: dict[str, str] = {}
        self._suppress_highlight = False

    def compose(self) -> ComposeResult:
        yield Label(self._caption)
        yield Input(placeholder="Filter models…  try composer, grok, gemini")
        yield OptionList()

    def selected_value(self) -> str:
        return self._selected_value

    def _listing(self) -> OptionList:
        return self.query_one(OptionList)

    def set_loading(self) -> None:
        self._selected_value = ""
        listing = self._listing()
        listing.clear_options()
        listing.add_option(Option("Loading models…", disabled=True))

    def set_choices(self, choices, selected: str = "") -> None:
        self._choices = list(choices)
        if selected:
            self._selected_value = selected
        elif not self._selected_value and choices:
            keys = {c.key: c.value for c in choices}
            if self.role == "plan":
                pick = default_cursor_plan_model(choices)
            else:
                pick = default_cursor_work_model(choices)
            if pick in keys:
                self._selected_value = keys[pick]
            else:
                current = [c for c in choices if c.group == "current"]
                self._selected_value = (current[0] if current else choices[0]).value
        query = ""
        try:
            query = self.query_one(Input).value
        except Exception:
            pass
        self._rebuild(query)

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.parent is self:
            event.stop()
            self._rebuild(event.value)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_list.parent is not self:
            return
        event.stop()
        self._remember(event.option)

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        if event.option_list.parent is not self:
            return
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
        listing = self._listing()
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


def _model_display_name(choices, value: str) -> str:
    if not value or value == "auto":
        return "Auto / Default"
    for c in choices or []:
        if c.value == value or c.key == value:
            return c.label or c.value or value
    return value


def _selected_models(screen) -> tuple[str, str]:
    if hasattr(screen, "_work_model") and hasattr(screen, "_plan_model"):
        return getattr(screen, "_plan_model", ""), getattr(screen, "_work_model", "")
    plan = ""
    work = ""
    for picker in screen.query(ModelPicker):
        if not picker.display:
            continue
        if picker.role == "plan":
            plan = picker.selected_value()
        else:
            work = picker.selected_value()
    return plan, work


class ModelSelectScreen(ModalScreen[Optional[dict]]):
    """Modal dialog to search and pick a model."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(
        self,
        agent_key: str,
        choices: Optional[list] = None,
        selected_work: str = "",
        selected_plan: str = "",
        show_plan: bool = False,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._agent_key = agent_key
        self._choices = list(choices) if choices is not None else None
        self._selected_work = selected_work
        self._selected_plan = selected_plan
        self._show_plan = show_plan

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog", classes="model-select-dialog"):
            yield Label("Select Model", classes="title")
            yield ModelPicker(
                caption="Target Model (write docs)",
                role="work",
                id="work-model-picker",
            )
            if self._show_plan:
                yield ModelPicker(
                    caption="Plan model (search & structure)",
                    role="plan",
                    id="plan-model-picker",
                )
            with Horizontal(classes="buttons"):
                yield Button("Select", variant="primary", id="ok")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        if self._choices is not None and len(self._choices) > 0:
            self._populate(self._choices)
        else:
            self.query_one("#work-model-picker", ModelPicker).set_loading()
            if self._show_plan:
                self.query_one("#plan-model-picker", ModelPicker).set_loading()
            self.run_worker(self._load_choices(), exclusive=True)

    async def _load_choices(self) -> None:
        choices = await asyncio.to_thread(list_agent_models, self._agent_key)
        if not self.is_attached:
            return
        self._choices = choices
        self._populate(choices)

    def _populate(self, choices: list) -> None:
        work_picker = self.query_one("#work-model-picker", ModelPicker)
        work_picker.set_choices(choices, selected=self._selected_work)
        if self._show_plan:
            plan_picker = self.query_one("#plan-model-picker", ModelPicker)
            plan_picker.set_choices(choices, selected=self._selected_plan)

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "cancel":
            self.dismiss(None)
            return
        work = self.query_one("#work-model-picker", ModelPicker).selected_value()
        plan = ""
        if self._show_plan:
            plan = self.query_one("#plan-model-picker", ModelPicker).selected_value()
        self.dismiss({"model": work, "plan_model": plan})


def _resolve_role_model(agent_key: str, model: Optional[str], role: str) -> str:
    chosen = (model or "").strip()
    if agent_key in CURSOR_AGENT_KEYS and not chosen:
        if role == "plan":
            return DEFAULT_CURSOR_PLAN_MODEL
        return DEFAULT_CURSOR_WORK_MODEL if role == "work" else DEFAULT_CURSOR_MODEL
    return chosen


def _jobs_from_input(screen, default: int = 1) -> int:
    try:
        raw = screen.query_one("#jobs", Input).value.strip()
    except Exception:
        return clamp_concurrency(default, 1)
    return clamp_concurrency(raw or default, default)


def _default_types_text() -> str:
    return "\n".join(f"{t.name}: {t.description}" for t in DEFAULT_DOC_TYPES)


class DeleteProjectScreen(ModalScreen[Optional[str]]):
    """Confirm removing a project from the list, optionally deleting its docs folder."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, entry, **kwargs) -> None:
        super().__init__(**kwargs)
        self._entry = entry

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label("Delete this project?", classes="title")
            yield Static(
                f"{self._entry.name}\n{self._entry.docs_path}\n\n"
                "Remove from list keeps the docs folder. "
                "Remove and delete docs folder erases that docs repo."
            )
            with Horizontal(classes="buttons"):
                yield Button("Remove from list", variant="primary", id="list")
                yield Button("Remove and delete docs folder", id="purge")
                yield Button("Cancel", id="cancel")

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "cancel":
            self.dismiss(None)
            return
        self.dismiss(event.button.id)


class ProjectPickerScreen(ModalScreen[Optional[dict]]):
    """Open or switch a registered docs project. Never writes into the app repo."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def compose(self) -> ComposeResult:
        self._id_to_entry = {}
        with Vertical(id="dialog"):
            yield Label("Open a docs project", classes="title")
            yield Static("Select a project, then Open or Delete.", id="project-help")
            yield OptionList(id="project-list")
            with Horizontal(classes="buttons"):
                yield Button("Open", variant="primary", id="ok")
                yield Button("Delete", id="delete")
                yield Button("New", id="new")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        self._rebuild()

    def _rebuild(self) -> None:
        listing = self.query_one("#project-list", OptionList)
        listing.clear_options()
        self._id_to_entry = {}
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

    def _delete_selected(self) -> None:
        entry = self._selected_entry()
        if entry is None:
            return

        def finish(action: Optional[str]) -> None:
            if not action:
                return
            removed, note = remove_project(entry.docs_path, delete_docs=action == "purge")
            if not removed:
                self.query_one("#project-help", Static).update("Could not remove that project.")
                return
            app = self.app
            current = os.path.abspath(getattr(app, "_docs", "") or "")
            if current and current == os.path.abspath(entry.docs_path):
                app._docs = ""
                app._repo = ""
                if hasattr(app, "refresh_summary"):
                    app.refresh_summary()
            message = f"Removed {entry.name}"
            if note:
                message = f"{message} ({note})"
            self.query_one("#project-help", Static).update(message)
            self._rebuild()

        self.app.push_screen(DeleteProjectScreen(entry), finish)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "cancel":
            self.dismiss(None)
            return
        if event.button.id == "new":
            self.dismiss({"new": True})
            return
        if event.button.id == "delete":
            self._delete_selected()
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

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._work_model = ""
        self._plan_model = ""
        self._model_choices: list = []

    def compose(self) -> ComposeResult:
        dash = _screen_dashboard(self)
        app_default = dash.app_repo_path or os.getcwd()
        docs_default = dash.docs_repo_path or default_docs_path(app_default)
        agent_default = _agent_key_from_dash(dash)
        if not dash.configured:
            prefs = getattr(getattr(self, "app", None), "_agent_defaults", {}) or {}
            preferred = prefs.get("name") or ""
            select_keys = {key for key, _ in AGENT_CHOICES if key != "custom"}
            if preferred in select_keys:
                agent_default = preferred
        branch_options, branch_default = _branch_select_options(app_default)
        with Vertical(id="dialog", classes="setup-dialog"):
            yield Label("Set up DocFlow", classes="title")
            with Horizontal(id="setup-columns"):
                with Vertical(classes="setup-pane setup-left"):
                    yield Label("Project settings", classes="pane-title")
                    yield Label("Application repo")
                    yield Input(value=app_default, id="app-path")
                    yield Label("Docs repo (must be empty)")
                    yield Input(value=docs_default, id="docs-path")
                    yield Label("Application branch")
                    yield Select(
                        branch_options,
                        value=branch_default,
                        id="app-branch",
                        allow_blank=False,
                    )
                    yield Label("Import from path/folder (optional, never overwrites)")
                    yield Input(placeholder="leave blank to skip", id="import-from")
                    yield Label("Import into type")
                    yield Input(placeholder="defaults to the first type", id="import-into")
                with Vertical(classes="setup-pane setup-right"):
                    yield Label("Agent", classes="pane-title")
                    yield Select(
                        _agent_select_options(),
                        value=agent_default,
                        id="agent",
                        allow_blank=False,
                    )
                    with Horizontal(id="model-row", classes="model-row"):
                        yield Label("Target Model: loading…", id="model-label")
                        yield Button("Change", id="change-model")
                    yield Label("Doc types (one per line: name: description)")
                    yield TextArea(_default_types_text(), id="types")
                    yield Label("Parallel agents (1 is safest on most PCs)")
                    yield Input(value="1", id="jobs")
            with Horizontal(classes="buttons"):
                yield Button("Start", variant="primary", id="ok")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        self._sync_model_select(str(self.query_one("#agent", Select).value))

    def _sync_model_select(self, agent_key: str) -> None:
        show = agent_supports_models(agent_key)
        model_row = self.query_one("#model-row")
        model_row.display = show
        if show:
            self.query_one("#model-label", Label).update("Target Model: loading…")
            self.run_worker(self._load_models(agent_key), exclusive=True, group="models")

    async def _load_models(self, agent_key: str) -> None:
        choices = await asyncio.to_thread(list_agent_models, agent_key)
        if not self.is_attached:
            return
        current = str(self.query_one("#agent", Select).value)
        if current != agent_key:
            return
        self._model_choices = choices
        dash = _screen_dashboard(self)
        prefs = getattr(getattr(self, "app", None), "_agent_defaults", {}) or {}
        same_agent = (getattr(dash, "agent_name", "") or "") == agent_key
        prefs_match = (prefs.get("name") or "") == agent_key

        if same_agent:
            self._plan_model = getattr(dash, "plan_model", "") or ""
            self._work_model = getattr(dash, "agent_model", "") or ""
        elif prefs_match:
            self._plan_model = prefs.get("plan_model") or ""
            self._work_model = prefs.get("model") or ""
        else:
            self._plan_model = ""
            self._work_model = ""

        if not self._work_model and choices:
            keys = {c.key: c.value for c in choices}
            pick = default_cursor_work_model(choices)
            if pick in keys:
                self._work_model = keys[pick]
            else:
                current_choices = [c for c in choices if c.group == "current"]
                self._work_model = (current_choices[0] if current_choices else choices[0]).value

        if not self._plan_model and choices:
            keys = {c.key: c.value for c in choices}
            pick = default_cursor_plan_model(choices)
            if pick in keys:
                self._plan_model = keys[pick]
            else:
                current_choices = [c for c in choices if c.group == "current"]
                self._plan_model = (current_choices[0] if current_choices else choices[0]).value

        self._update_model_label()

    def _update_model_label(self) -> None:
        display = _model_display_name(self._model_choices, self._work_model)
        if self._plan_model and self._plan_model != self._work_model and self._plan_model != "auto":
            plan_display = _model_display_name(self._model_choices, self._plan_model)
            display = f"{display} (plan: {plan_display})"
        self.query_one("#model-label", Label).update(f"Target Model: {display}")

    def _open_model_picker(self) -> None:
        agent_key = str(self.query_one("#agent", Select).value)
        show_plan = agent_key in CURSOR_AGENT_KEYS

        def _on_picked(result: Optional[dict]) -> None:
            if not result:
                return
            if result.get("model"):
                self._work_model = result["model"]
            if "plan_model" in result:
                self._plan_model = result.get("plan_model") or ""
            self._update_model_label()

        self.app.push_screen(
            ModelSelectScreen(
                agent_key=agent_key,
                choices=self._model_choices,
                selected_work=self._work_model,
                selected_plan=self._plan_model,
                show_plan=show_plan,
            ),
            _on_picked,
        )

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "app-path":
            return
        options, default = _branch_select_options(event.value.strip())
        picker = self.query_one("#app-branch", Select)
        picker.set_options(options)
        picker.value = default

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "agent":
            self._sync_model_select(str(event.value))

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "cancel":
            self.dismiss(None)
            return
        if event.button.id == "change-model":
            self._open_model_picker()
            return
        agent_key = str(self.query_one("#agent", Select).value)
        types = parse_doc_types_text(self.query_one("#types", TextArea).text)
        self.dismiss(
            {
                "app": self.query_one("#app-path", Input).value.strip(),
                "docs": self.query_one("#docs-path", Input).value.strip(),
                "agent": agent_key,
                "model": self._work_model,
                "plan_model": self._plan_model,
                "types": types or list(DEFAULT_DOC_TYPES),
                "import_from": self.query_one("#import-from", Input).value.strip(),
                "import_into": self.query_one("#import-into", Input).value.strip(),
                "jobs": _jobs_from_input(self, 1),
                "branch": str(self.query_one("#app-branch", Select).value or ""),
            }
        )


class SectionPickerScreen(ModalScreen[Optional[list]]):
    """After inventory: choose individual units to document, and add extras."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, candidates: list, **kwargs) -> None:
        super().__init__(**kwargs)
        self._items = list(candidates)
        self._syncing = False

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label("Review the agent's documentation list", classes="title")
            yield Static(
                "These are the units the agent recommended from composer/packages. "
                "Toggle a group heading to select or skip every item in that group "
                "(for example all migrations), then re-check any you still want. "
                "Add a file path if something important is missing. "
                "Git, GitLab, and CI are never listed.",
                id="section-help",
            )
            yield SelectionList(id="section-list")
            yield Label("Add a file path")
            yield Input(placeholder="app/Models/Order.php", id="add-path")
            with Horizontal(classes="buttons"):
                yield Button("Add", id="add")
                yield Button("Continue", variant="primary", id="ok")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        self._rebuild()

    def _rebuild(self) -> None:
        listing = self.query_one("#section-list", SelectionList)
        listing.clear_options()
        for kind, indices in group_candidates(self._items):
            group_on = bool(indices) and all(self._items[i].included for i in indices)
            listing.add_option(
                Selection(f"── {kind_heading(kind)} ──  (all)", f"g-{kind}", group_on)
            )
            for i in indices:
                item = self._items[i]
                listing.add_option(Selection(item.label, f"s{i}", item.included))

    def _set_value(self, listing: SelectionList, value: str, selected: bool) -> None:
        if selected:
            listing.select(value)
        else:
            listing.deselect(value)

    def on_selection_list_selection_toggled(self, event: SelectionList.SelectionToggled) -> None:
        if self._syncing:
            return
        listing = event.selection_list
        value = str(event.selection.value)
        selected = set(listing.selected)
        self._syncing = True
        try:
            if value.startswith("g-"):
                kind = value[2:]
                want = value in selected
                for i, item in enumerate(self._items):
                    if picker_group(item) != kind:
                        continue
                    self._set_value(listing, f"s{i}", want)
                return
            if value.startswith("s"):
                index = int(value[1:])
                kind = picker_group(self._items[index])
                indices = [
                    i for i, item in enumerate(self._items) if picker_group(item) == kind
                ]
                group_on = bool(indices) and all(f"s{i}" in selected for i in indices)
                self._set_value(listing, f"g-{kind}", group_on)
        finally:
            self._syncing = False

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
        stem = os.path.splitext(os.path.basename(raw.replace("\\", "/")))[0] or raw
        self._items.append(
            SectionCandidate(
                doc_type="functions",
                name=raw,
                title=stem,
                kind="function",
                description=f"Extra unit '{raw}'",
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
                "Select at least one item, or add a file path."
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

    def __init__(self, repo: str = "", docs: str = "", **kwargs) -> None:
        super().__init__(**kwargs)
        self._repo = repo
        self._docs = docs
        self._work_model = ""
        self._plan_model = ""
        self._model_choices: list = []

    def compose(self) -> ComposeResult:
        dash = _screen_dashboard(self)
        if not self._repo:
            self._repo = dash.app_repo_path if dash.app_exists else ""
        if not self._docs:
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
        app_branch_options, app_branch_default = _branch_select_options(
            self._repo,
            getattr(dash, "app_branch", "") or "",
        )
        with Vertical(id="dialog"):
            yield Label("Update documentation", classes="title")
            yield Label("Application branch")
            yield Select(
                app_branch_options,
                value=app_branch_default,
                id="app-branch",
                allow_blank=False,
            )
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
            with Horizontal(id="model-row", classes="model-row"):
                yield Label("Target Model: loading…", id="model-label")
                yield Button("Change", id="change-model")
            yield Label("Parallel agents (1 is safest on most PCs)")
            yield Input(value=str(dash.concurrency or 1), id="jobs")
            with Horizontal(classes="buttons"):
                yield Button("Generate", variant="primary", id="ok")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        self._apply_mode("new")
        self._sync_model_select(str(self.query_one("#agent", Select).value))

    def _sync_model_select(self, agent_key: str) -> None:
        show = agent_supports_models(agent_key)
        model_row = self.query_one("#model-row")
        model_row.display = show
        if show:
            self.query_one("#model-label", Label).update("Target Model: loading…")
            self.run_worker(self._load_models(agent_key), exclusive=True, group="models")

    async def _load_models(self, agent_key: str) -> None:
        choices = await asyncio.to_thread(list_agent_models, agent_key)
        if not self.is_attached:
            return
        current = str(self.query_one("#agent", Select).value)
        if current != agent_key:
            return
        self._model_choices = choices
        dash = _screen_dashboard(self)
        prefs = getattr(getattr(self, "app", None), "_agent_defaults", {}) or {}
        same_agent = (getattr(dash, "agent_name", "") or "") == agent_key
        prefs_match = (prefs.get("name") or "") == agent_key

        if same_agent:
            self._plan_model = getattr(dash, "plan_model", "") or ""
            self._work_model = getattr(dash, "agent_model", "") or ""
        elif prefs_match:
            self._plan_model = prefs.get("plan_model") or ""
            self._work_model = prefs.get("model") or ""
        else:
            self._plan_model = ""
            self._work_model = ""

        if not self._work_model and choices:
            keys = {c.key: c.value for c in choices}
            pick = default_cursor_work_model(choices)
            if pick in keys:
                self._work_model = keys[pick]
            else:
                current_choices = [c for c in choices if c.group == "current"]
                self._work_model = (current_choices[0] if current_choices else choices[0]).value

        if not self._plan_model and choices:
            keys = {c.key: c.value for c in choices}
            pick = default_cursor_plan_model(choices)
            if pick in keys:
                self._plan_model = keys[pick]
            else:
                current_choices = [c for c in choices if c.group == "current"]
                self._plan_model = (current_choices[0] if current_choices else choices[0]).value

        self._update_model_label()

    def _update_model_label(self) -> None:
        display = _model_display_name(self._model_choices, self._work_model)
        if self._plan_model and self._plan_model != self._work_model and self._plan_model != "auto":
            plan_display = _model_display_name(self._model_choices, self._plan_model)
            display = f"{display} (plan: {plan_display})"
        self.query_one("#model-label", Label).update(f"Target Model: {display}")

    def _open_model_picker(self) -> None:
        agent_key = str(self.query_one("#agent", Select).value)
        show_plan = agent_key in CURSOR_AGENT_KEYS

        def _on_picked(result: Optional[dict]) -> None:
            if not result:
                return
            if result.get("model"):
                self._work_model = result["model"]
            if "plan_model" in result:
                self._plan_model = result.get("plan_model") or ""
            self._update_model_label()

        self.app.push_screen(
            ModelSelectScreen(
                agent_key=agent_key,
                choices=self._model_choices,
                selected_work=self._work_model,
                selected_plan=self._plan_model,
                show_plan=show_plan,
            ),
            _on_picked,
        )

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
            app_branch = str(self.query_one("#app-branch", Select).value or dash.app_branch or "HEAD")
            lines = [f"Application branch: {app_branch}"]
            if dash.last_documented:
                lines.append(
                    f"Last documented: {dash.last_documented.short_sha}  {dash.last_documented.message}"
                )
            else:
                lines.append("No previous update recorded — will use the latest commit.")
            new_commits = dash.new_commits
            if self._repo and self._docs:
                try:
                    rev = resolve_branch_rev(self._repo, app_branch)
                    _cursor, new_commits, stale = new_commits_since(self._repo, self._docs, rev=rev)
                    if stale:
                        lines.append("Last documented commit is not on this branch — will scan from the common ancestor.")
                except Exception:
                    pass
            if new_commits:
                lines.append(f"{len(new_commits)} new commit(s) on {app_branch}:")
                for commit in new_commits[:15]:
                    lines.append(f"{commit.short_sha}  {commit.message}")
                if len(new_commits) > 15:
                    lines.append(f"… {len(new_commits) - 15} more")
            else:
                lines.append("Nothing new on this branch locally. Update docs will fetch the remote first.")
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
        elif event.select.id == "app-branch":
            self._refresh_preview()
        elif event.select.id == "agent":
            self._sync_model_select(str(event.value))

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
        if event.button.id == "change-model":
            self._open_model_picker()
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
                "model": self._work_model,
                "plan_model": self._plan_model,
                "jobs": _jobs_from_input(self, 1),
                "app_branch": str(self.query_one("#app-branch", Select).value or ""),
            }
        )


class RegenLastScreen(ModalScreen[Optional[dict]]):
    """Offer to redo the last documented commit with another agent/model."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._work_model = ""
        self._plan_model = ""
        self._model_choices: list = []

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
            with Horizontal(id="model-row", classes="model-row"):
                yield Label("Target Model: loading…", id="model-label")
                yield Button("Change", id="change-model")
            with Horizontal(classes="buttons"):
                yield Button("Regenerate", variant="primary", id="ok")
                yield Button("Exit", id="cancel")

    def on_mount(self) -> None:
        self._sync_model_select(str(self.query_one("#agent", Select).value))

    def _sync_model_select(self, agent_key: str) -> None:
        show = agent_supports_models(agent_key)
        model_row = self.query_one("#model-row")
        model_row.display = show
        if show:
            self.query_one("#model-label", Label).update("Target Model: loading…")
            self.run_worker(self._load_models(agent_key), exclusive=True, group="models")

    async def _load_models(self, agent_key: str) -> None:
        choices = await asyncio.to_thread(list_agent_models, agent_key)
        if not self.is_attached:
            return
        current = str(self.query_one("#agent", Select).value)
        if current != agent_key:
            return
        self._model_choices = choices
        dash = _screen_dashboard(self)
        prefs = getattr(getattr(self, "app", None), "_agent_defaults", {}) or {}
        same_agent = (getattr(dash, "agent_name", "") or "") == agent_key
        prefs_match = (prefs.get("name") or "") == agent_key

        if same_agent:
            self._plan_model = getattr(dash, "plan_model", "") or ""
            self._work_model = getattr(dash, "agent_model", "") or ""
        elif prefs_match:
            self._plan_model = prefs.get("plan_model") or ""
            self._work_model = prefs.get("model") or ""
        else:
            self._plan_model = ""
            self._work_model = ""

        if not self._work_model and choices:
            keys = {c.key: c.value for c in choices}
            pick = default_cursor_work_model(choices)
            if pick in keys:
                self._work_model = keys[pick]
            else:
                current_choices = [c for c in choices if c.group == "current"]
                self._work_model = (current_choices[0] if current_choices else choices[0]).value

        if not self._plan_model and choices:
            keys = {c.key: c.value for c in choices}
            pick = default_cursor_plan_model(choices)
            if pick in keys:
                self._plan_model = keys[pick]
            else:
                current_choices = [c for c in choices if c.group == "current"]
                self._plan_model = (current_choices[0] if current_choices else choices[0]).value

        self._update_model_label()

    def _update_model_label(self) -> None:
        display = _model_display_name(self._model_choices, self._work_model)
        if self._plan_model and self._plan_model != self._work_model and self._plan_model != "auto":
            plan_display = _model_display_name(self._model_choices, self._plan_model)
            display = f"{display} (plan: {plan_display})"
        self.query_one("#model-label", Label).update(f"Target Model: {display}")

    def _open_model_picker(self) -> None:
        agent_key = str(self.query_one("#agent", Select).value)
        show_plan = agent_key in CURSOR_AGENT_KEYS

        def _on_picked(result: Optional[dict]) -> None:
            if not result:
                return
            if result.get("model"):
                self._work_model = result["model"]
            if "plan_model" in result:
                self._plan_model = result.get("plan_model") or ""
            self._update_model_label()

        self.app.push_screen(
            ModelSelectScreen(
                agent_key=agent_key,
                choices=self._model_choices,
                selected_work=self._work_model,
                selected_plan=self._plan_model,
                show_plan=show_plan,
            ),
            _on_picked,
        )

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "agent":
            self._sync_model_select(str(event.value))

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "cancel":
            self.dismiss(None)
            return
        if event.button.id == "change-model":
            self._open_model_picker()
            return
        self.dismiss(
            {
                "agent": str(self.query_one("#agent", Select).value),
                "model": self._work_model,
                "plan_model": self._plan_model,
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
    #dialog.setup-dialog {
        width: 100%;
        margin: 1 0;
    }
    #setup-columns {
        height: auto;
    }
    .setup-pane {
        width: 1fr;
        height: auto;
    }
    .setup-left {
        margin-right: 2;
    }
    .setup-right {
        border-left: tall $border-blurred;
        padding-left: 2;
    }
    #dialog .title {
        text-style: bold;
        margin-bottom: 1;
    }
    #dialog Input, #dialog Select, #dialog TextArea, #preview, #model-row, #plan-model-picker, #work-model-picker, #model-picker, #section-list {
        margin-bottom: 1;
    }
    #model-row {
        height: auto;
        align: left middle;
    }
    #model-label {
        height: auto;
        margin-right: 2;
        padding: 0 1 0 0;
        text-style: bold;
    }
    #change-model {
        height: auto;
        min-width: 10;
    }
    .model-select-dialog {
        width: 76;
    }
    #project-list, #section-list {
        height: 12;
        border: tall $border-blurred;
        background: $surface;
        padding: 0 1;
        margin-bottom: 1;
    }
    #section-list {
        height: 16;
    }
    #section-help {
        color: $text-muted;
        margin-bottom: 1;
    }
    #dialog TextArea {
        height: 8;
    }
    #plan-model-picker, #work-model-picker, #model-picker {
        height: auto;
    }
    #plan-model-picker OptionList, #work-model-picker OptionList, #model-picker OptionList {
        height: 8;
        border: tall $border-blurred;
        background: $surface;
        padding: 0 1;
    }
    #plan-model-picker Input, #work-model-picker Input, #model-picker Input {
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
        self._blank_session = False
        self._agent_defaults: dict = {}
        self._previous_session: Optional[tuple] = None

    def _dashboard(self):
        return get_dashboard(
            self._repo or None,
            self._docs or None,
            use_last=not self._blank_session,
        )

    def _resolve(self, require: bool = True):
        return resolve_paths(self._repo or None, self._docs or None, require=require)

    def _clear_run_views(self) -> None:
        self._progress_lines.clear()
        self._log_lines.clear()
        self._paused_progress.clear()
        self._paused_logs.clear()
        self._run_control.resume()
        self._render_progress()
        self.query_one("#log", Log).clear()
        self._set_step("Ready")

    def _begin_new_project(self) -> None:
        dash = self._dashboard()
        self._agent_defaults = {
            "name": dash.agent_name if dash.configured else "",
            "model": dash.agent_model,
            "plan_model": getattr(dash, "plan_model", ""),
        }
        self._previous_session = (self._repo, self._docs, self._blank_session)
        self._repo = ""
        self._docs = ""
        self._blank_session = True
        self.title = "DocFlow"
        self._clear_run_views()
        self.sub_title = "New project"
        self.query_one("#step", Static).update("New project")
        self.refresh_summary()

    def _restore_previous_session(self) -> None:
        if not self._previous_session:
            return
        self._repo, self._docs, self._blank_session = self._previous_session
        self._previous_session = None
        self.refresh_summary()

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
        if self._blank_session:
            self.title = "DocFlow"
            self.query_one("#btn-setup", Button).label = "Setup"
            self.query_one("#summary", Static).update(
                "\n".join(
                    [
                        "Project  [bold]not set[/bold]",
                        "App      not set",
                        "Docs     not set",
                        "Jobs     1 agent(s) at a time",
                        "Agent    not set",
                        "Types: none",
                        "Documented: none yet",
                        "New commits (0): none",
                        "Features (0): none",
                        "Pending prompts (0): none",
                    ]
                )
            )
            return
        dash = self._dashboard()
        self.title = dash.project_name or "DocFlow"
        lines = [
            f"Project  [bold]{dash.project_name or 'not set'}[/bold]",
            f"App      {dash.app_repo_path or 'not set'}  ({'ok' if dash.app_exists else 'missing'})",
            f"Docs     {dash.docs_repo_path or 'not set'}  ({'ok' if dash.docs_exists else 'missing'})",
            f"Branch   {dash.app_branch or 'not set'}",
            f"Jobs     {dash.concurrency} agent(s) at a time",
            f"Agent    {dash.agent_name or dash.agent_mode}"
            + (f"  plan {dash.plan_model}" if getattr(dash, "plan_model", "") else "")
            + (f"  work {dash.agent_model}" if dash.agent_model else "")
            + f"  {dash.agent_command or 'manual'}",
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
            model=_resolve_role_model(data["agent"], data.get("model"), "work"),
            plan_model=_resolve_role_model(data["agent"], data.get("plan_model"), "plan"),
        )
        if spec is None:
            spec = resolve_agent(agent="manual")
        self._begin_run("Asking the agent what belongs in the docs…")
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
                concurrency=data.get("jobs"),
                on_review_sections=review,
                run_control=self._run_control,
                branch=data.get("branch") or "",
            )
        except InitCancelled as exc:
            self._finish_run(str(exc))
            return
        except Exception as exc:
            self._finish_run(f"Setup failed: {exc}")
            return
        ok = sum(1 for f in result.features if f.success)
        imported = f", imported {len(result.imported_copied)}" if result.imported_copied else ""
        self._docs = result.docs_repo_path
        self._repo = result.app_repo_path
        self._blank_session = False
        self._previous_session = None
        open_project(result.docs_repo_path)
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
            self._begin_new_project()
            await self._do_setup()
            if self._blank_session and not self._docs:
                self._restore_previous_session()
            return picked
        self._blank_session = False
        self._previous_session = None
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
            model=_resolve_role_model(data.get("agent") or "", data.get("model"), "work"),
            plan_model=_resolve_role_model(data.get("agent") or "", data.get("plan_model"), "plan"),
            config=paths.config,
        ) or resolve_agent(config=paths.config) or resolve_agent(agent="manual")
        branch = data.get("branch") or ""
        app_branch = data.get("app_branch") or ""
        if data["full"]:
            label = "Full regeneration"
        elif data.get("since_last"):
            label = f"Updating {app_branch or 'tracked branch'} since last docs update"
        elif branch:
            n = data.get("commit_count") or 1
            label = f"Updating {n} commit(s) on {branch}"
        else:
            n = data.get("commit_count") or 1
            label = f"Updating last {n} commit" + ("" if n == 1 else "s") + " on HEAD"
        self._begin_run(f"{label}…")
        loop = asyncio.get_running_loop()

        def review(candidates):
            future = asyncio.run_coroutine_threadsafe(
                self.push_screen_wait(SectionPickerScreen(list(candidates))),
                loop,
            )
            return future.result()

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
                concurrency=data.get("jobs"),
                run_control=self._run_control,
                app_branch=app_branch,
                on_review_sections=review,
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
                model=_resolve_role_model(regen.get("agent") or "", regen.get("model"), "work"),
                plan_model=_resolve_role_model(regen.get("agent") or "", regen.get("plan_model"), "plan"),
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
                    app_branch=app_branch,
                )
            except Exception as exc:
                self._finish_run(f"Regenerate failed: {exc}")
                return
        if result.already_current:
            self._finish_run("Already documented through the application branch.")
        elif result.no_changes:
            extra = (
                f"; added {', '.join(result.new_items)}"
                if getattr(result, "new_items", None)
                else ""
            )
            self._finish_run(f"Update finished: no changed files in those commits{extra}")
        elif result.features and not all(item.success for item in result.features):
            failed = [item.feature_name for item in result.features if not item.success]
            self._finish_run(f"Update failed for: {', '.join(failed)}")
        elif result.run and result.run.success:
            added = (
                f"; new items: {', '.join(result.new_items)}"
                if getattr(result, "new_items", None)
                else ""
            )
            if result.commits:
                tip = result.commits[0]
                self._finish_run(
                    f"Update finished: {result.task_type} / {result.feature_name} "
                    f"({result.commit_count} commit(s), {tip.short_sha} {tip.message}){added}"
                )
            else:
                self._finish_run(f"Update finished: {result.task_type} / {result.feature_name}{added}")
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
