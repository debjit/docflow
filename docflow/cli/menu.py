"""
Interactive TTY menu shown when `docflow` is run with no subcommand.
"""

from __future__ import annotations

import os
import sys
from typing import List, Optional, Sequence

import click
from rich.prompt import Confirm, Prompt

from docflow.cli import ux
from docflow.config.settings import DocTypeSettings
from docflow.core.agent_runner import AGENT_PRESETS
from docflow.core.operations import (
    AGENT_CHOICES,
    ConfigError,
    CURSOR_AGENT_KEYS,
    DEFAULT_CURSOR_MODEL,
    DEFAULT_CURSOR_PLAN_MODEL,
    DEFAULT_CURSOR_WORK_MODEL,
    DEFAULT_DOC_TYPES,
    AlreadyInitialized,
    InitCancelled,
    SectionCandidate,
    agent_supports_models,
    attach_agent_models,
    assert_can_init,
    default_docs_path,
    default_app_branch,
    generate_docs,
    get_dashboard,
    infer_agent_model,
    infer_agent_name,
    infer_plan_model,
    group_candidates,
    import_docs,
    init_docs,
    kind_heading,
    resolve_picker_group,
    toggle_group_included,
    list_app_branches,
    list_agent_models,
    parse_doc_type,
    publish_docs,
    pull_app_repo,
    resolve_agent,
    resolve_paths,
    selected_sections,
)


def pick_agent(default_key: str = "agy", default_model: str = "", default_plan_model: str = ""):
    ux.console.print("\n[bold]How should DocFlow run your coding agent?[/bold]")
    keys = []
    for i, (key, label) in enumerate(AGENT_CHOICES, start=1):
        ux.console.print(f"  [cyan]{i}[/cyan]. {label}")
        keys.append(key)
    default_idx = str(keys.index(default_key) + 1) if default_key in keys else "1"
    choice = Prompt.ask("Agent", choices=[str(i) for i in range(1, len(keys) + 1)], default=default_idx)
    key = keys[int(choice) - 1]
    if key == "custom":
        cmd = Prompt.ask(
            "Shell command template (`{prompt_file}` and `{docs_repo}` are replaced)",
            default=AGENT_PRESETS["agy"],
        )
        return resolve_agent(command=cmd)
    spec = resolve_agent(agent=key)
    return pick_model(
        spec,
        model=default_model if key == default_key else "",
        plan_model=default_plan_model if key == default_key else "",
    )


def pick_model(spec, model: str = "", plan_model: str = ""):
    if spec is None:
        return spec
    if model and plan_model:
        return attach_agent_models(spec, model=model, plan_model=plan_model)
    if model and not agent_supports_models(spec.name):
        return attach_agent_models(spec, model=model, plan_model=plan_model or model)
    if not agent_supports_models(spec.name) and not (spec.command or "").lstrip().startswith(
        ("agent ", "agy ", "opencode ")
    ):
        return spec
    choices = list_agent_models(spec.name)
    current = [c for c in choices if c.group == "current"]
    third = [c for c in choices if c.group == "third_party"]
    is_cursor = spec.name in CURSOR_AGENT_KEYS or (spec.command or "").lstrip().startswith(
        "agent "
    )
    ux.console.print("\n[bold]Which LLM models?[/bold]")
    ux.console.print("[dim]Plan model searches the app and structures the docs list.[/dim]")
    ux.console.print("[dim]Work model writes each section from that list.[/dim]")
    if current:
        ux.console.print("[dim]Current / included usage[/dim]")
        for choice in current:
            ux.console.print(f"  [cyan]{choice.value or choice.key}[/cyan]  {choice.label}")
    if third:
        ux.console.print("[dim]Third-party[/dim]")
        for choice in third:
            ux.console.print(f"  [cyan]{choice.value or choice.key}[/cyan]  {choice.label}")
    ids = {c.value or c.key for c in choices}
    if is_cursor:
        plan_default = (
            plan_model
            or (DEFAULT_CURSOR_PLAN_MODEL if DEFAULT_CURSOR_PLAN_MODEL in ids else DEFAULT_CURSOR_MODEL)
        )
        work_default = (
            model
            or (DEFAULT_CURSOR_WORK_MODEL if DEFAULT_CURSOR_WORK_MODEL in ids else DEFAULT_CURSOR_MODEL)
        )
    else:
        plan_default = plan_model or model or "default"
        work_default = model or "default"
    chosen_plan = Prompt.ask("Plan model (search & structure)", default=plan_default)
    chosen_work = Prompt.ask("Work model (write docs)", default=work_default)
    if chosen_plan in ("default", "auto"):
        chosen_plan = ""
    if chosen_work in ("default", "auto"):
        chosen_work = ""
    return attach_agent_models(spec, model=chosen_work, plan_model=chosen_plan)


def ensure_paths(repo: str, docs: str, prompt_missing: bool = True):
    try:
        return resolve_paths(repo or None, docs or None, require=True)
    except ConfigError as exc:
        if not prompt_missing:
            raise
        ux.print_warning(str(exc))
        app = Prompt.ask("Application repo path", default=repo or os.getcwd())
        docs_path = Prompt.ask(
            "Docs repo path",
            default=docs or default_docs_path(os.path.abspath(app)),
        )
        return resolve_paths(app, docs_path, require=True)


def ensure_agent(paths, agent: Optional[str] = None, mode: Optional[str] = None, command: Optional[str] = None):
    spec = resolve_agent(agent=agent, mode=mode, command=command, config=paths.config)
    if spec is None:
        spec = pick_agent(
            default_key=infer_agent_name(paths.config) or "agy",
            default_model=infer_agent_model(paths.config),
            default_plan_model=infer_plan_model(paths.config),
        )
    if spec is None:
        raise click.Abort()
    return spec


def collect_doc_types(explicit: Sequence[str] = ()) -> List[DocTypeSettings]:
    if explicit:
        return [parse_doc_type(item) for item in explicit if str(item).strip()]
    if not sys.stdout.isatty():
        return list(DEFAULT_DOC_TYPES)
    ux.console.print("\n[bold]Documentation types[/bold]")
    ux.console.print("Each type is a folder for application docs.")
    ux.console.print(
        "Suggested: [cyan]architecture[/cyan], [cyan]database[/cyan], "
        "[cyan]models[/cyan], [cyan]functions[/cyan], [cyan]routes[/cyan], [cyan]pages[/cyan]"
    )
    types: List[DocTypeSettings] = []
    if Confirm.ask("Use suggested types as a starting set?", default=True):
        types = list(DEFAULT_DOC_TYPES)
    while True:
        name = Prompt.ask("Add a type name (blank to finish)", default="")
        if not str(name).strip():
            break
        desc = Prompt.ask("What kind of docs belong here", default="")
        types.append(parse_doc_type(f"{name}:{desc}" if desc else name))
    return types or list(DEFAULT_DOC_TYPES)


def collect_import(
    types: Sequence[DocTypeSettings],
    import_from: str = "",
    import_into: str = "",
    import_existing: Optional[bool] = None,
) -> tuple[str, str]:
    names = [t.name for t in types]
    default_into = import_into or (names[0] if names else "docs")
    if import_from:
        into = import_into
        if not into and sys.stdout.isatty():
            into = Prompt.ask("Import into which type", default=default_into)
        return import_from, into or default_into
    if import_existing is False:
        return "", ""
    if not sys.stdout.isatty():
        return "", ""
    if Confirm.ask("Import existing files from a path/folder? (never overwrites)", default=False):
        path = Prompt.ask("Path or folder to import")
        into = Prompt.ask("Import into which type", default=default_into)
        return path, into
    return "", ""


def _print_section_candidates(
    candidates: Sequence[SectionCandidate],
) -> tuple[List[int], List[str]]:
    ux.console.print("\n[bold]Agent recommendations — remove or add before writing docs[/bold]")
    ux.console.print(
        "[dim]Number toggles one item. g1 / migrations / models toggles a whole group. "
        "Git, GitLab, and CI are not listed. Add a file path if something is missing.[/dim]"
    )
    order: List[int] = []
    groups: List[str] = []
    for gi, (kind, indices) in enumerate(group_candidates(candidates), start=1):
        groups.append(kind)
        all_on = all(candidates[i].included for i in indices)
        mark = "[green]Y[/green]" if all_on else "[red]N[/red]"
        ux.console.print(
            f"\n  [cyan]g{gi}[/cyan]. [{mark}] [bold]{kind_heading(kind)}[/bold]  [dim](all)[/dim]"
        )
        for i in indices:
            order.append(i)
            item = candidates[i]
            item_mark = "[green]Y[/green]" if item.included else "[red]N[/red]"
            ux.console.print(f"      [cyan]{len(order)}[/cyan]. [{item_mark}] {item.label}")
    included = len(selected_sections(candidates))
    ux.console.print(f"\n[dim]{included}/{len(candidates)} selected[/dim]")
    return order, groups


def review_init_sections(
    candidates: List[SectionCandidate],
    add_extra=None,
) -> Optional[List[SectionCandidate]]:
    """TTY picker: toggle individual units and optionally add more."""
    if not candidates:
        return []
    items = list(candidates)
    while True:
        order, groups = _print_section_candidates(items)
        ux.console.print(
            "  [cyan]number[/cyan] one item   [cyan]g1[/cyan] / [cyan]migrations[/cyan] a group   "
            "[cyan]a[/cyan] add   [cyan]all[/cyan]/[cyan]none[/cyan]   "
            "[cyan]done[/cyan] continue   [cyan]q[/cyan] cancel"
        )
        choice = Prompt.ask("Item", default="done").strip().lower()
        if choice in ("", "done", "y", "yes"):
            picked = selected_sections(items)
            if not picked:
                ux.print_warning("Select at least one item, or add a file path.")
                continue
            return picked
        if choice in ("q", "quit", "cancel"):
            return None
        if choice == "all":
            for item in items:
                item.included = True
            continue
        if choice == "none":
            for item in items:
                item.included = False
            continue
        if choice in ("a", "add"):
            raw = Prompt.ask("File path to add (blank to skip)", default="").strip()
            if not raw:
                continue
            if add_extra:
                extra = add_extra(raw)
            else:
                extra = SectionCandidate(
                    doc_type="functions",
                    name=raw,
                    title=os.path.splitext(os.path.basename(raw.replace("\\", "/")))[0] or raw,
                    kind="function",
                    description=f"Extra unit '{raw}'",
                    included=True,
                    extra=True,
                )
            items.append(extra)
            ux.console.print(f"  Added [cyan]{extra.display_name}[/cyan].")
            continue
        if choice.startswith("g") and choice[1:].isdigit():
            index = int(choice[1:]) - 1
            if 0 <= index < len(groups):
                toggle_group_included(items, groups[index])
            continue
        grouped = resolve_picker_group(choice, groups)
        if grouped is not None:
            toggle_group_included(items, grouped)
            continue
        if choice.isdigit():
            index = int(choice) - 1
            if 0 <= index < len(order):
                items[order[index]].included = not items[order[index]].included
            continue
        ux.print_warning("Use a number, g1, a group name, a, all, none, done, or q.")


def _cli_progress(message: str) -> None:
    ux.console.print(f"  [dim]{message}[/dim]")


def pick_open_project(repo: str = "", docs: str = "", force_list: bool = False) -> tuple[str, str]:
    """Last project → open, or pick from the list. Does not write into the app repo."""
    from docflow.core.projects import last_project, load_index, open_project, remove_project

    if docs:
        return repo, docs
    last = last_project()
    entries = load_index()
    if not sys.stdout.isatty():
        if last:
            return last.app_path or repo, last.docs_path
        return repo, docs
    if last and not force_list:
        ux.console.print(
            f"\nLast project: [cyan]{last.name}[/cyan]  {last.docs_path}"
        )
        if Confirm.ask("Open this project?", default=True):
            open_project(last.docs_path)
            return last.app_path or repo, last.docs_path
    if entries:
        ux.console.print("\n[bold]Open a docs project[/bold]")
        for i, entry in enumerate(entries, start=1):
            ux.console.print(f"  [cyan]{i}[/cyan]. {entry.name}  {entry.docs_path}")
        ux.console.print("  [cyan]n[/cyan]. New project (init)")
        ux.console.print("  [cyan]d[/cyan]. Delete a project")
        choices = [str(i) for i in range(1, len(entries) + 1)] + ["n", "d"]
        choice = Prompt.ask("Project", choices=choices, default="n" if not last else "1")
        if choice == "d":
            which = Prompt.ask(
                "Number to delete",
                choices=[str(i) for i in range(1, len(entries) + 1)],
            )
            entry = entries[int(which) - 1]
            purge = Confirm.ask(
                f"Also delete the docs folder on disk?  ({entry.docs_path})",
                default=False,
            )
            removed, note = remove_project(entry.docs_path, delete_docs=purge)
            if removed:
                ux.console.print(f"  Removed [cyan]{entry.name}[/cyan]. {note}".rstrip())
            else:
                ux.print_warning("Could not remove that project.")
            return pick_open_project(repo, docs, force_list=True)
        if choice != "n":
            entry = entries[int(choice) - 1]
            open_project(entry.docs_path)
            return entry.app_path or repo, entry.docs_path
    return repo, docs


def run_init(repo: str = "", docs: str = "", agent: Optional[str] = None,
             mode: Optional[str] = None, command: Optional[str] = None,
             model: str = "",
             plan_model: str = "",
             branch: str = "",
             import_existing: Optional[bool] = None, import_from: str = "",
             import_into: str = "", doc_types: Sequence[str] = (),
             yes: bool = False,
             include_sections: Sequence[str] = (),
             exclude_sections: Sequence[str] = (),
             extra_sections: Sequence[str] = ()) -> None:
    app = repo or Prompt.ask("Application repo path", default=os.getcwd())
    app = os.path.abspath(app)
    paths = resolve_paths(app, docs or None, require=False)
    docs_path = docs or paths.docs_repo_path or default_docs_path(app)
    if not docs:
        docs_path = Prompt.ask("Docs repo path (separate folder recommended)", default=docs_path)
    docs_path = os.path.abspath(docs_path)
    try:
        assert_can_init(docs_path)
    except (AlreadyInitialized, ConfigError) as exc:
        ux.print_error(str(exc))
        raise click.Abort()
    spec = resolve_agent(
        agent=agent,
        mode=mode,
        command=command,
        config=paths.config,
        model=model,
        plan_model=plan_model,
    ) or pick_agent(
        default_key=infer_agent_name(paths.config) or "agy",
        default_model=infer_agent_model(paths.config) or model,
        default_plan_model=infer_plan_model(paths.config) or plan_model,
    )
    types = collect_doc_types(doc_types)
    source, into = collect_import(types, import_from, import_into, import_existing)
    if not branch and sys.stdout.isatty():
        branches = []
        try:
            branches = list_app_branches(app)
        except Exception:
            branches = []
        default_branch = default_app_branch(app)
        if branches:
            ux.console.print("\n[bold]Application branch[/bold]")
            for name in branches:
                ux.console.print(f"  [cyan]{name}[/cyan]")
        branch = Prompt.ask("Application branch (main / master / develop)", default=default_branch)
    reviewer = review_init_sections if sys.stdout.isatty() and not yes else None
    try:
        result = init_docs(
            app_repo_path=app,
            docs_repo_path=docs_path,
            agent=spec,
            config=paths.config,
            import_existing=bool(source) or bool(import_existing),
            types=types,
            import_from=source or None,
            import_into=into or None,
            on_progress=_cli_progress,
            on_review_sections=reviewer,
            include_sections=include_sections,
            exclude_sections=exclude_sections,
            extra_sections=extra_sections,
            branch=branch,
        )
    except InitCancelled as exc:
        ux.print_warning(str(exc))
        raise click.Abort()
    except ConfigError as exc:
        ux.print_error(str(exc))
        raise click.Abort()
    ux.print_init_result(result)
    ux.print_dashboard(get_dashboard(result.app_repo_path, result.docs_repo_path))


def run_import(docs: str = "", source: str = "", type_name: str = "") -> None:
    paths = resolve_paths(None, docs or None, require=False)
    docs_path = docs or paths.docs_repo_path
    if not docs_path:
        if sys.stdout.isatty():
            docs_path = Prompt.ask("Docs repo path")
        else:
            ux.print_error("Docs repo is not set. Run `docflow init` or pass --docs.")
            raise click.Abort()
    if not source:
        if sys.stdout.isatty():
            source = Prompt.ask("Path or folder to import")
        else:
            ux.print_error("Pass --from PATH for the files or folder to import.")
            raise click.Abort()
    if not type_name:
        dash = get_dashboard(None, docs_path)
        default = (dash.doc_types[0].split(":")[0].strip() if dash.doc_types else "docs")
        if sys.stdout.isatty():
            type_name = Prompt.ask("Import into which type", default=default)
        else:
            ux.print_error("Pass --type NAME for the destination doc type.")
            raise click.Abort()
    try:
        result = import_docs(source, docs_path, type_name)
    except ConfigError as exc:
        ux.print_error(str(exc))
        raise click.Abort()
    ux.print_import_result(result)


def run_generate(repo: str = "", docs: str = "", agent: Optional[str] = None,
                 mode: Optional[str] = None, command: Optional[str] = None,
                 model: str = "",
                 plan_model: str = "",
                 branch: str = "", from_ref: str = "", to_ref: str = "",
                 feature: str = "", full: bool = False, interactive_mode: bool = False,
                 commit_count: Optional[int] = None,
                 concurrency: Optional[int] = None,
                 app_branch: str = "") -> None:
    paths = ensure_paths(repo, docs)
    spec = ensure_agent(paths, agent, mode, command)
    if model or plan_model:
        spec = attach_agent_models(spec, model=model or spec.model, plan_model=plan_model or spec.plan_model)
    is_full = full
    if interactive_mode and not from_ref and not to_ref and not branch and not full and commit_count is None:
        dash = get_dashboard(paths.app_repo_path, paths.docs_repo_path)
        ux.console.print("\n[bold]Update docs from[/bold]")
        new_n = len(dash.new_commits)
        if dash.app_branch:
            ux.console.print(f"  Application branch: [cyan]{dash.app_branch}[/cyan]")
        if dash.last_documented:
            ux.console.print(
                f"  Last documented: [cyan]{dash.last_documented.short_sha}[/cyan]  "
                f"{dash.last_documented.message}"
            )
        ux.console.print(
            f"  [cyan]1[/cyan]. New commits since last update  ({new_n} waiting)"
        )
        ux.console.print("  [cyan]2[/cyan]. Last N commits (any number)")
        ux.console.print("  [cyan]3[/cyan]. Branch — last N commits on a named branch")
        ux.console.print("  [cyan]4[/cyan]. Full regeneration of existing docs")
        ux.console.print("  [cyan]5[/cyan]. Change application branch (main / master / develop)")
        choice = Prompt.ask("Choice", choices=["1", "2", "3", "4", "5"], default="1")
        if choice == "4":
            is_full = True
        elif choice == "5":
            branches = []
            try:
                branches = list_app_branches(paths.app_repo_path)
            except Exception:
                branches = []
            if branches:
                ux.console.print("  Branches:")
                for name in branches:
                    ux.console.print(f"    [cyan]{name}[/cyan]")
            app_branch = Prompt.ask(
                "Application branch",
                default=app_branch or dash.app_branch or default_app_branch(paths.app_repo_path),
            )
            commit_count = None
        elif choice == "1":
            commit_count = None
        elif choice == "2":
            commit_count = int(Prompt.ask("How many commits", default="1"))
            commit_count = max(1, commit_count)
        else:
            branches = []
            try:
                branches = list_app_branches(paths.app_repo_path)
            except Exception:
                branches = []
            if branches:
                ux.console.print("  Branches:")
                for name in branches:
                    ux.console.print(f"    [cyan]{name}[/cyan]")
            branch = Prompt.ask("Branch name", default=branches[0] if branches else "HEAD")
            commit_count = int(Prompt.ask("How many commits on that branch", default="1"))
            commit_count = max(1, commit_count)
    if concurrency is None and interactive_mode:
        from docflow.core.job_runner import clamp_concurrency

        default_jobs = str(clamp_concurrency(paths.config.generation.concurrency, 1))
        concurrency = clamp_concurrency(
            Prompt.ask("Parallel agents (1 is safest)", default=default_jobs),
            1,
        )
    try:
        result = generate_docs(
            app_repo_path=paths.app_repo_path,
            docs_repo_path=paths.docs_repo_path,
            agent=spec,
            config=paths.config,
            from_ref=from_ref,
            to_ref=to_ref,
            branch=branch,
            feature=feature,
            full=is_full,
            commit_count=commit_count,
            concurrency=concurrency,
            on_progress=_cli_progress,
            app_branch=app_branch,
            on_review_sections=review_init_sections if sys.stdout.isatty() else None,
        )
    except Exception as exc:
        ux.print_error(str(exc))
        raise click.Abort()
    ux.print_generate_result(result)
    maybe_regen_last_docs(paths, spec, result, feature=feature)


def maybe_regen_last_docs(paths, spec, result, feature: str = "") -> None:
    """If HEAD is already documented, ask to redo that commit with another LLM."""
    if not result.already_current or not sys.stdout.isatty():
        return
    dash = get_dashboard(paths.app_repo_path, paths.docs_repo_path)
    if dash.last_documented:
        ux.console.print(
            f"  Last documented: [cyan]{dash.last_documented.short_sha}[/cyan]  "
            f"{dash.last_documented.message}"
        )
    if not Confirm.ask(
        "Regenerate the last documented commit with another LLM?",
        default=False,
    ):
        ux.console.print("[dim]Exiting without regenerating.[/dim]")
        return
    default_key = spec.name if spec and spec.name in {key for key, _ in AGENT_CHOICES} else "agy"
    spec = pick_agent(
        default_key=default_key,
        default_model=infer_agent_model(paths.config),
        default_plan_model=infer_plan_model(paths.config),
    )
    if spec is None:
        return
    try:
        again = generate_docs(
            app_repo_path=paths.app_repo_path,
            docs_repo_path=paths.docs_repo_path,
            agent=spec,
            config=paths.config,
            feature=feature,
            commit_count=1,
            sync_remote=False,
        )
    except Exception as exc:
        ux.print_error(str(exc))
        raise click.Abort()
    ux.print_generate_result(again)


def run_status(repo: str = "", docs: str = "") -> None:
    dash = get_dashboard(repo or None, docs or None)
    ux.print_dashboard(dash)
    if not dash.configured:
        ux.next_step("`docflow init` to pair an app repo with a docs repo.")
    elif dash.new_commits:
        ux.next_step("`docflow generate` to update current docs from those new commits.")
    else:
        ux.next_step("`docflow pull` to fetch, or `docflow generate` / `docflow ui`.")


def run_pull(repo: str = "", docs: str = "") -> None:
    try:
        paths = resolve_paths(repo or None, docs or None, require=True)
    except ConfigError as exc:
        ux.print_error(str(exc))
        raise click.Abort()
    result = pull_app_repo(paths.app_repo_path, paths.docs_repo_path)
    ux.print_pull_result(result)
    if not result.success:
        raise click.Abort()


def run_publish(docs: str = "", platform: str = "", message: str = "docs: update documentation") -> None:
    paths = resolve_paths(None, docs or None, require=False)
    docs_path = docs or paths.docs_repo_path
    if not docs_path:
        docs_path = Prompt.ask("Docs repo path")
    try:
        result = publish_docs(
            docs_repo_path=docs_path,
            config=paths.config,
            platform=platform,
            message=message,
        )
    except Exception as exc:
        ux.print_error(str(exc))
        raise click.Abort()
    ux.print_publish_result(result)


def run_serve(docs: str = "", transport: str = "stdio", port: int = 8080) -> None:
    from docflow.mcp.server import create_mcp_server

    paths = resolve_paths(None, docs or None, require=False)
    docs_path = os.path.abspath(docs or paths.docs_repo_path or "./docs-repo")
    ux.print_header("Starting DocFlow MCP server")
    ux.console.print(f"  Docs repo:  [cyan]{docs_path}[/cyan]")
    ux.console.print(f"  Transport:  [cyan]{transport}[/cyan]")
    server = create_mcp_server(docs_path)
    if transport == "stdio":
        server.run(transport="stdio")
    else:
        ux.console.print(f"  Port:       [cyan]{port}[/cyan]")
        server.run(transport="sse", port=port)


def run_ui(repo: str = "", docs: str = "") -> None:
    try:
        from docflow.tui.app import run_tui
    except ImportError as exc:
        ux.print_error(
            "The visual UI needs the `textual` package. Install with: pip install 'docflow[ui]'"
        )
        ux.print_error(str(exc))
        raise click.Abort()
    run_tui(repo=repo, docs=docs)


def run_menu(repo: str = "", docs: str = "") -> None:
    repo, docs = pick_open_project(repo, docs)
    dash = get_dashboard(repo or None, docs or None)
    ux.print_dashboard(dash)
    ux.console.print("\n[bold]What do you want to do?[/bold]")
    if not dash.configured:
        ux.console.print("  [cyan]1[/cyan]. Set up this project  (init)")
        ux.console.print("  [cyan]2[/cyan]. Open visual UI")
        ux.console.print("  [cyan]q[/cyan]. Quit")
        choice = Prompt.ask("Choice", choices=["1", "2", "q"], default="1")
        if choice == "1":
            run_init(repo, docs)
        elif choice == "2":
            run_ui(repo, docs)
        return

    ux.console.print("  [cyan]1[/cyan]. Update docs from git changes")
    ux.console.print("  [cyan]2[/cyan]. git pull — fetch latest from the server")
    ux.console.print("  [cyan]3[/cyan]. Show status")
    ux.console.print("  [cyan]4[/cyan]. Publish docs (commit + PR)")
    ux.console.print("  [cyan]5[/cyan]. Start MCP server")
    ux.console.print("  [cyan]6[/cyan]. Open visual UI")
    ux.console.print("  [cyan]7[/cyan]. Import existing files (never overwrites)")
    ux.console.print("  [cyan]8[/cyan]. Switch docs project")
    ux.console.print("  [cyan]q[/cyan]. Quit")
    choice = Prompt.ask("Choice", choices=["1", "2", "3", "4", "5", "6", "7", "8", "q"], default="1")
    if choice == "1":
        run_generate(repo, docs, interactive_mode=True)
    elif choice == "2":
        run_pull(repo, docs)
    elif choice == "3":
        run_status(repo, docs)
    elif choice == "4":
        run_publish(docs)
    elif choice == "5":
        transport = Prompt.ask("Transport", choices=["stdio", "sse"], default="stdio")
        port = 8080
        if transport == "sse":
            port = int(Prompt.ask("Port", default="8080"))
        run_serve(docs, transport=transport, port=port)
    elif choice == "6":
        run_ui(repo, docs)
    elif choice == "7":
        run_import(docs)
    elif choice == "8":
        repo, docs = pick_open_project(repo, "", force_list=True)
        run_menu(repo, docs)
