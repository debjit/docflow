"""
Click CLI for DocFlow.
"""

from __future__ import annotations

import sys
from typing import Optional

import click

from docflow import __version__
from docflow.cli import menu, ux
from docflow.core.operations import (
    ConfigError,
    apply_agent_model,
    generate_docs,
    resolve_agent,
    resolve_paths,
)


def _from_ctx(ctx: click.Context, name: str, value: str) -> str:
    return value or (ctx.obj or {}).get(name) or ""


@click.group(invoke_without_command=True)
@click.version_option(version=__version__, prog_name="docflow")
@click.option("--repo", default="", help="Application source repository path.")
@click.option("--docs", default="", help="Documentation repository path.")
@click.pass_context
def cli(ctx: click.Context, repo: str, docs: str):
    """DocFlow — generate and serve dual-audience docs from git activity.

    Run with no command for an interactive menu. After `docflow init`, everyday
    commands read paths and the agent from `.docflow.yml`.
    """
    ctx.ensure_object(dict)
    ctx.obj["repo"] = repo
    ctx.obj["docs"] = docs
    if ctx.invoked_subcommand is None:
        if sys.stdout.isatty():
            menu.run_menu(repo, docs)
        else:
            click.echo(ctx.get_help())


@cli.command()
@click.pass_context
@click.option("--repo", default="", help="Path to application source repository.")
@click.option("--docs", default="", help="Path to dedicated documentation repository.")
@click.option("--agent", help="Coding agent (agy, opencode, cursor-agent, claude, cline, manual).")
@click.option("--model", default="", help="LLM model id for the agent (Cursor: agent models).")
@click.option("--import-existing/--fresh", default=None, help="Ask to import files vs start blank.")
@click.option("--import-from", default="", help="Path or folder to import (never overwrites).")
@click.option("--import-into", default="", help="Doc type folder to import into.")
@click.option("--doc-type", "doc_types", multiple=True, help="Repeatable type as name:description.")
@click.option("--mode", type=click.Choice(["shell", "manual"]), help="Agent execution mode (advanced).")
@click.option("--command", help="Custom shell command template (advanced).")
def init(
    ctx,
    repo: str,
    docs: str,
    agent: Optional[str],
    model: str,
    import_existing: Optional[bool],
    import_from: str,
    import_into: str,
    doc_types: tuple,
    mode: str,
    command: str,
):
    """Set up DocFlow in an empty docs folder. Types are folders you define."""
    menu.run_init(
        repo=_from_ctx(ctx, "repo", repo),
        docs=_from_ctx(ctx, "docs", docs),
        agent=agent,
        model=model,
        mode=mode,
        command=command,
        import_existing=import_existing,
        import_from=import_from,
        import_into=import_into,
        doc_types=doc_types,
    )


@cli.command("import")
@click.pass_context
@click.option("--docs", default="", help="Path to dedicated documentation repository.")
@click.option("--from", "import_from", default="", help="Path or folder to import.")
@click.option("--type", "type_name", default="", help="Destination doc type (folder name).")
def import_cmd(ctx, docs: str, import_from: str, type_name: str):
    """Copy existing files into a doc type folder. Never overwrites."""
    menu.run_import(
        docs=_from_ctx(ctx, "docs", docs),
        source=import_from,
        type_name=type_name,
    )


@cli.command()
@click.pass_context
@click.option("--repo", default="", help="Path to application source repository.")
@click.option("--docs", default="", help="Path to dedicated documentation repository.")
@click.option("--agent", help="Coding agent (agy, opencode, cursor-agent, claude, cline, manual).")
@click.option("--model", default="", help="LLM model id for the agent (Cursor: agent models).")
@click.option("--branch", default="", help="Branch or tip to read commits from (default: current HEAD).")
@click.option("--from", "from_ref", default="", help="Base commit/branch (advanced).")
@click.option("--to", "to_ref", default="", help="Head commit/branch (advanced).")
@click.option("--commits", "commit_count", type=int, default=None, help="Include the last N commits from HEAD or --branch. No upper limit.")
@click.option("--feature", default="", help="Target a specific feature directory.")
@click.option("--full", is_flag=True, help="Full feature doc regeneration.")
@click.option("--mode", type=click.Choice(["shell", "manual"]), help="Agent execution mode (advanced).")
@click.option("--command", help="Custom shell command template (advanced).")
def generate(
    ctx,
    repo: str,
    docs: str,
    agent: Optional[str],
    model: str,
    branch: str,
    from_ref: str,
    to_ref: str,
    commit_count: Optional[int],
    feature: str,
    full: bool,
    mode: str,
    command: str,
):
    """Update docs from new commits since last update, last N, a branch, or --full."""
    repo = _from_ctx(ctx, "repo", repo)
    docs = _from_ctx(ctx, "docs", docs)
    try:
        paths = resolve_paths(repo or None, docs or None, require=True)
    except ConfigError:
        if sys.stdout.isatty():
            menu.run_generate(
                repo=repo,
                docs=docs,
                agent=agent,
                model=model,
                mode=mode,
                command=command,
                branch=branch,
                from_ref=from_ref,
                to_ref=to_ref,
                feature=feature,
                full=full,
                commit_count=commit_count,
            )
            return
        ux.print_error("Application or docs repo is not set. Run `docflow init` or pass --repo / --docs.")
        raise click.Abort()
    spec = resolve_agent(agent=agent, mode=mode, command=command, config=paths.config, model=model)
    if spec is None:
        if sys.stdout.isatty():
            spec = menu.pick_agent()
            if model:
                spec = apply_agent_model(spec, model)
        else:
            spec = resolve_agent(agent="manual")
    if (
        sys.stdout.isatty()
        and not from_ref
        and not to_ref
        and not branch
        and not full
        and commit_count is None
    ):
        menu.run_generate(
            repo=repo,
            docs=docs,
            agent=agent,
            model=model,
            mode=mode,
            command=command,
            branch=branch,
            from_ref=from_ref,
            to_ref=to_ref,
            feature=feature,
            full=full,
            interactive_mode=True,
            commit_count=commit_count,
        )
        return
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
            full=full,
            commit_count=commit_count,
        )
    except Exception as exc:
        ux.print_error(str(exc))
        raise click.Abort()
    ux.print_generate_result(result)
    menu.maybe_regen_last_docs(paths, spec, result, feature=feature)


@cli.command()
@click.pass_context
@click.option("--repo", default="", help="Path to application source repository.")
@click.option("--docs", default="", help="Path to dedicated documentation repository.")
def status(ctx, repo: str, docs: str):
    """Show project dashboard: types, last documented commit, and new commits."""
    menu.run_status(_from_ctx(ctx, "repo", repo), _from_ctx(ctx, "docs", docs))


@cli.command()
@click.pass_context
@click.option("--repo", default="", help="Path to application source repository.")
@click.option("--docs", default="", help="Path to dedicated documentation repository.")
def info(ctx, repo: str, docs: str):
    """Alias for `status`."""
    menu.run_status(_from_ctx(ctx, "repo", repo), _from_ctx(ctx, "docs", docs))


@cli.command()
@click.pass_context
@click.option("--docs", default="", help="Path to dedicated documentation repository.")
@click.option("--platform", default="", help="Platform (github, gitlab, generic).")
@click.option("--message", default="docs: update documentation", help="Commit message.")
def publish(ctx, docs: str, platform: str, message: str):
    """Commit doc updates, push a branch, and open a pull/merge request."""
    menu.run_publish(_from_ctx(ctx, "docs", docs), platform=platform, message=message)


@cli.command()
@click.pass_context
@click.option("--docs", default="", help="Path to dedicated documentation repository.")
@click.option("--transport", default=None, type=click.Choice(["stdio", "sse"]), help="MCP transport.")
@click.option("--mode", default=None, type=click.Choice(["stdio", "sse"]), help="Deprecated alias for --transport.")
@click.option("--port", default=8080, help="Port for SSE transport.")
def serve(ctx, docs: str, transport: Optional[str], mode: Optional[str], port: int):
    """Start the MCP server so agents can read generated docs."""
    chosen = transport or mode or "stdio"
    menu.run_serve(_from_ctx(ctx, "docs", docs), transport=chosen, port=port)


@cli.command()
@click.pass_context
@click.option("--repo", default="", help="Path to application source repository.")
@click.option("--docs", default="", help="Path to dedicated documentation repository.")
def pull(ctx, repo: str, docs: str):
    """Run git pull on the app repo, then show commits not yet in the docs."""
    menu.run_pull(_from_ctx(ctx, "repo", repo), _from_ctx(ctx, "docs", docs))


@cli.command()
def ui():
    """Open the full-screen DocFlow UI."""
    menu.run_ui()


if __name__ == "__main__":
    cli()
