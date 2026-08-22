"""
Rich display helpers for the DocFlow CLI.
"""

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from docflow.core.operations import (
    Dashboard,
    GenerateResult,
    ImportResult,
    InitResult,
    PublishResult,
    PullResult,
)

console = Console()


def print_header(title: str) -> None:
    console.print(f"\n[bold green]{title}[/bold green]")


def print_error(message: str) -> None:
    console.print(f"[bold red]{message}[/bold red]")


def print_warning(message: str) -> None:
    console.print(f"[yellow]{message}[/yellow]")


def next_step(message: str) -> None:
    console.print(f"\n[dim]Next:[/dim] {message}")


def print_dashboard(dash: Dashboard) -> None:
    if not dash.configured:
        print_header("No DocFlow project is open")
        console.print("  Config lives in a docs folder. Nothing is written into the app repo.")
        if not dash.app_repo_path and not dash.docs_repo_path:
            next_step("`docflow init` or `docflow projects list` / `open`.")
            return
    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column("key", style="bold")
    table.add_column("value")
    table.add_row("Project", f"[cyan]{dash.project_name}[/cyan]")
    table.add_row(
        "App repo",
        f"[cyan]{dash.app_repo_path or 'not set'}[/cyan] "
        f"({'exists' if dash.app_exists else 'missing'})",
    )
    table.add_row(
        "App branch",
        f"[cyan]{getattr(dash, 'app_branch', '') or 'not set'}[/cyan]",
    )
    table.add_row(
        "Docs repo",
        f"[cyan]{dash.docs_repo_path or 'not set'}[/cyan] "
        f"({'exists' if dash.docs_exists else 'missing'})",
    )
    table.add_row(
        "Agent",
        f"[yellow]{dash.agent_name or dash.agent_mode}[/yellow]"
        + (
            f"  plan [cyan]{dash.plan_model}[/cyan]"
            if getattr(dash, "plan_model", "")
            else ""
        )
        + (
            f"  work [cyan]{dash.agent_model}[/cyan]"
            if getattr(dash, "agent_model", "")
            else ""
        )
        + f"  {dash.agent_command or 'manual'}",
    )
    table.add_row(
        "Parallel agents",
        f"[cyan]{getattr(dash, 'concurrency', 1)}[/cyan]  (1 is safest)",
    )
    table.add_row("Platform", dash.platform)
    if dash.doc_types:
        table.add_row("Doc types", ", ".join(dash.doc_types))
    if dash.last_documented:
        table.add_row(
            "Documented",
            f"[cyan]{dash.last_documented.short_sha}[/cyan]  {dash.last_documented.message}",
        )
    table.add_row(
        "New commits",
        f"[yellow]{len(dash.new_commits)}[/yellow]  "
        + (
            ", ".join(f"{c.short_sha} {c.message}" for c in dash.new_commits[:5])
            if dash.new_commits
            else "none — pull to fetch, or already up to date"
        ),
    )
    table.add_row(
        "Features",
        f"[yellow]{len(dash.features)}[/yellow]  {', '.join(dash.features) or 'none'}",
    )
    table.add_row(
        "Pending prompts",
        f"[yellow]{len(dash.pending)}[/yellow]  {', '.join(dash.pending) or 'none'}",
    )
    if dash.source_path:
        table.add_row("Config", f"[dim]{dash.source_path}[/dim]")
    if dash.wip_md:
        table.add_row("WIP", f"[cyan]{dash.wip_md}[/cyan]")
    console.print(Panel(table, title="DocFlow", border_style="green"))
    if dash.wip_error:
        print_warning(dash.wip_error)


def print_init_result(result: InitResult) -> None:
    print_header("Initializing DocFlow")
    console.print(f"  App repo:   [cyan]{result.app_repo_path}[/cyan]")
    console.print(f"  Docs repo:  [cyan]{result.docs_repo_path}[/cyan]")
    console.print(
        f"  Agent:      [yellow]{result.agent_mode}[/yellow] "
        f"({result.agent_command or 'manual prompt generation'})"
    )
    if result.docs_inside_app:
        print_warning(
            "Docs are inside the app repo. A separate docs repo is recommended."
        )
    if result.types:
        console.print(f"  Types:      [cyan]{', '.join(result.types)}[/cyan]")
    if result.imported_copied or result.imported_skipped:
        console.print(
            f"  Import:     [green]{len(result.imported_copied)} copied[/green], "
            f"[yellow]{len(result.imported_skipped)} skipped[/yellow] (existing files kept)"
        )
    done = [item for item in result.features if item.success]
    failed = [item for item in result.features if not item.success]
    if done:
        console.print("  [bold]Done[/bold]")
        for item in done:
            console.print(f"  [green]✓[/green] [{item.feature_name}] {item.prompt_file}")
    if failed:
        console.print("  [bold]Failed[/bold]")
        for item in failed:
            console.print(
                f"  [red]✗[/red] [{item.feature_name}] {item.error_message or 'failed'}"
            )
    console.print("  [green]✓[/green] Generated llms.txt & llms-full.txt")
    if result.agent_mode == "manual":
        console.print(
            f"\n[bold green]Done.[/bold green] Prompts are in [cyan]{result.pending_dir}[/cyan]"
        )
        next_step("Open those prompt files in your agent, then `docflow publish`.")
    else:
        console.print("\n[bold green]Done.[/bold green] Agent ran on each feature prompt.")
        next_step("`docflow status` to review, or `docflow publish` to open a PR.")


def print_generate_result(result: GenerateResult) -> None:
    print_header("DocFlow generate")
    if result.is_full:
        console.print("  Mode:       [cyan]full regeneration[/cyan]")
    elif result.used_cursor and result.commits:
        label = "new commit" if result.commit_count == 1 else "new commits"
        console.print(f"  Commits:    [cyan]{result.commit_count}[/cyan] {label} since last update")
        for commit in result.commits:
            console.print(f"              [cyan]{commit.short_sha}[/cyan]  {commit.message}")
    elif result.commits:
        label = "commit" if result.commit_count == 1 else "commits"
        console.print(f"  Commits:    last [cyan]{result.commit_count}[/cyan] {label}")
        for commit in result.commits:
            console.print(f"              [cyan]{commit.short_sha}[/cyan]  {commit.message}")
    elif result.base_ref == result.head_ref:
        console.print(f"  Snapshot:   [cyan]{result.head_ref}[/cyan]")
    else:
        console.print(f"  Comparing:  [cyan]{result.base_ref}[/cyan] … [cyan]{result.head_ref}[/cyan]")
    console.print(f"  App repo:   [cyan]{result.app_repo_path}[/cyan]")
    console.print(f"  Docs repo:  [cyan]{result.docs_repo_path}[/cyan]")
    console.print(
        f"  Agent:      [yellow]{result.agent_mode}[/yellow] "
        f"({result.agent_command or 'manual prompt generation'})"
    )
    if result.synced_remote:
        console.print("  Remote:     [green]pulled latest commits before generate[/green]")
    if result.already_current:
        print_warning(
            "Docs already cover the current HEAD. Nothing new to generate."
        )
        next_step("`docflow generate --commits N` or `--full` to regenerate.")
        return
    if result.watermark_stale:
        print_warning("Last documented commit is no longer on this branch; used the latest commit instead.")
    if result.no_changes:
        print_warning("No changed files in that commit range.")
        next_step("`docflow pull` for new commits, or `--full` to regenerate existing docs.")
        return
    if result.features:
        done = [item for item in result.features if item.success]
        failed = [item for item in result.features if not item.success]
        if done:
            console.print("  [bold]Done[/bold]")
            for item in done:
                console.print(f"  [green]✓[/green] [{item.feature_name}] {item.prompt_file}")
        if failed:
            console.print("  [bold]Failed[/bold]")
            for item in failed:
                console.print(
                    f"  [red]✗[/red] [{item.feature_name}] {item.error_message or 'failed'}"
                )
        if failed:
            print_error("One or more feature generations failed; watermark was not advanced.")
        else:
            next_step("`docflow publish` when the docs look right.")
        return
    run = result.run
    if run and run.success:
        if result.agent_mode == "shell":
            console.print("\n[bold green]✓ Agent finished.[/bold green]")
            if run.output_log:
                console.print(run.output_log)
        else:
            console.print(f"  [green]✓[/green] Prompt written: [cyan]{result.prompt_file}[/cyan]")
        next_step("`docflow publish` when the docs look right.")
    elif run:
        print_error(f"Agent failed: {run.error_message}")


def print_publish_result(result: PublishResult) -> None:
    console.print(
        f"[bold green]Committed docs[/bold green] ({result.commit}) "
        f"on [cyan]{result.branch}[/cyan]"
    )
    if not result.auto_mr:
        next_step("Push the docs branch yourself, or set platform.auto_mr: true.")
        return
    if result.mr_success:
        console.print("[bold green]✓ Pull/merge request created[/bold green]")
        if result.mr_url:
            console.print(f"  URL: [cyan]{result.mr_url}[/cyan]")
    elif result.mr_message:
        print_warning(result.mr_message)


def print_import_result(result: ImportResult) -> None:
    print_header("Importing documentation")
    console.print(f"  Source:     [cyan]{result.source}[/cyan]")
    console.print(f"  Into:       [cyan]{result.dest_type}/[/cyan]")
    console.print(
        f"  Copied:     [green]{len(result.copied)}[/green]  "
        f"Skipped: [yellow]{len(result.skipped)}[/yellow] (existing files kept)"
    )
    for rel in result.copied[:20]:
        console.print(f"  [green]✓[/green] {result.dest_type}/{rel}")
    for rel in result.skipped[:20]:
        console.print(f"  [yellow]·[/yellow] skip {result.dest_type}/{rel}")
    leftover = len(result.copied) + len(result.skipped) - 40
    if leftover > 0:
        console.print(f"  [dim]… {leftover} more[/dim]")
    if result.type_added:
        console.print(f"  [green]✓[/green] Added type [cyan]{result.dest_type}[/cyan] to config")
    next_step("`docflow generate` to update existing docs from new commits.")


def print_pull_result(result: PullResult) -> None:
    print_header("git pull")
    console.print(f"  App repo:   [cyan]{result.app_repo_path}[/cyan]")
    if result.output:
        console.print(result.output)
    if not result.success:
        print_error("git pull failed.")
        return
    if result.already_up_to_date and not result.new_commits:
        print_warning("Already up to date with the remote.")
    if result.last_documented:
        console.print(
            f"  Documented: [cyan]{result.last_documented.short_sha}[/cyan]  "
            f"{result.last_documented.message}"
        )
    if result.new_commits:
        console.print(f"  New:        [green]{len(result.new_commits)}[/green] commit(s) not yet in docs")
        for commit in result.new_commits[:15]:
            console.print(f"              [cyan]{commit.short_sha}[/cyan]  {commit.message}")
        next_step("`docflow generate` to update current docs from those commits.")
    else:
        next_step("No new commits since the last docs update.")
