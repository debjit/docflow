"""
CLI tests for DocFlow.
"""

from pathlib import Path

from click.testing import CliRunner
from git import Repo

from docflow.cli.main import cli
from docflow.config.settings import DocFlowConfig
from docflow.core.projects import last_project, register_project


def _user_dirs(tmp_path, monkeypatch):
    home = tmp_path / "home"
    xdg = tmp_path / "xdg"
    home.mkdir()
    xdg.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    return home, xdg


def _git_repo(path: Path) -> Repo:
    repo = Repo.init(path)
    readme = path / "README.md"
    readme.write_text("# Sample\n")
    repo.index.add(["README.md"])
    repo.index.commit("Initial commit")
    auth = path / "src" / "auth"
    auth.mkdir(parents=True)
    (auth / "login.py").write_text("def login():\n    return True\n")
    repo.index.add(["src/auth/login.py"])
    repo.index.commit("Add auth")
    return repo


def test_bare_docflow_non_tty_shows_help():
    runner = CliRunner()
    result = runner.invoke(cli, [])
    assert result.exit_code == 0
    assert "Usage:" in result.output
    assert "generate" in result.output
    assert "ui" in result.output


def test_help_lists_commands():
    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    for name in ("init", "generate", "status", "info", "publish", "serve", "ui", "import", "pull", "projects"):
        assert name in result.output


def test_init_does_not_write_home_config(tmp_path, monkeypatch):
    home, xdg = _user_dirs(tmp_path, monkeypatch)
    monkeypatch.chdir(tmp_path)

    app = tmp_path / "app"
    docs = tmp_path / "docs"
    app.mkdir()
    _git_repo(app)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["init", "--repo", str(app), "--docs", str(docs), "--agent", "manual", "--fresh"],
    )
    assert result.exit_code == 0, result.output
    assert not (app / ".docflow.yml").exists()
    assert (docs / ".docflow" / "config.yml").exists()
    assert not (home / ".docflow.yml").exists()
    cwd_config = tmp_path / ".docflow.yml"
    assert not cwd_config.exists()
    assert not (tmp_path / ".docflow" / "config.yml").exists()
    assert (xdg / "docflow" / "projects.yml").exists()


def test_generate_uses_config_without_prompting(tmp_path, monkeypatch):
    _user_dirs(tmp_path, monkeypatch)
    monkeypatch.chdir(tmp_path)
    app = tmp_path / "app"
    docs = tmp_path / "docs"
    app.mkdir()
    docs.mkdir()
    _git_repo(app)

    cfg = DocFlowConfig()
    cfg.project.name = "Sample"
    cfg.app.repo_path = str(app)
    cfg.docs.repo_path = str(docs)
    cfg.agent.mode = "manual"
    cfg.agent.command = ""
    cfg.save(str(docs))
    register_project(str(docs), str(app), "Sample")

    runner = CliRunner()
    result = runner.invoke(cli, ["generate", "--repo", str(app), "--agent", "manual"])
    assert result.exit_code == 0, result.output
    pending = list((docs / ".docflow" / "prompts" / "pending").glob("*.md"))
    assert pending, result.output


def test_status_and_info_alias(tmp_path, monkeypatch):
    _user_dirs(tmp_path, monkeypatch)
    monkeypatch.chdir(tmp_path)
    app = tmp_path / "app"
    docs = tmp_path / "docs"
    app.mkdir()
    docs.mkdir()
    _git_repo(app)
    cfg = DocFlowConfig()
    cfg.project.name = "Sample"
    cfg.app.repo_path = str(app)
    cfg.docs.repo_path = str(docs)
    cfg.save(str(docs))
    register_project(str(docs), str(app), "Sample")

    runner = CliRunner()
    status = runner.invoke(cli, ["status", "--repo", str(app)])
    info = runner.invoke(cli, ["info", "--repo", str(app)])
    assert status.exit_code == 0, status.output
    assert info.exit_code == 0, info.output
    assert "Sample" in status.output
    assert "Sample" in info.output
    assert not (docs / "status" / "wip.md").exists()


def test_generate_without_config_aborts(tmp_path, monkeypatch):
    _user_dirs(tmp_path, monkeypatch)
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    result = runner.invoke(cli, ["generate"])
    assert result.exit_code != 0
    assert "init" in result.output.lower() or "projects" in result.output.lower() or "aborted" in result.output.lower()


def test_init_refuses_second_run_and_nonempty(tmp_path, monkeypatch):
    _user_dirs(tmp_path, monkeypatch)
    monkeypatch.chdir(tmp_path)
    app = tmp_path / "app"
    docs = tmp_path / "docs"
    app.mkdir()
    _git_repo(app)
    runner = CliRunner()
    first = runner.invoke(
        cli,
        ["init", "--repo", str(app), "--docs", str(docs), "--agent", "manual", "--fresh"],
    )
    assert first.exit_code == 0, first.output
    second = runner.invoke(
        cli,
        ["init", "--repo", str(app), "--docs", str(docs), "--agent", "manual", "--fresh"],
    )
    assert second.exit_code != 0
    assert "already initialized" in second.output.lower()

    other = tmp_path / "other-docs"
    other.mkdir()
    (other / "keep.md").write_text("nope\n")
    nonempty = runner.invoke(
        cli,
        ["init", "--repo", str(app), "--docs", str(other), "--agent", "manual", "--fresh"],
    )
    assert nonempty.exit_code != 0
    assert "not empty" in nonempty.output.lower()


def test_init_custom_doc_type(tmp_path, monkeypatch):
    _user_dirs(tmp_path, monkeypatch)
    monkeypatch.chdir(tmp_path)
    app = tmp_path / "app"
    docs = tmp_path / "docs"
    app.mkdir()
    _git_repo(app)
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "init",
            "--repo", str(app),
            "--docs", str(docs),
            "--agent", "manual",
            "--fresh",
            "--doc-type", "front-end: React UI docs",
        ],
    )
    assert result.exit_code == 0, result.output
    assert (docs / "front-end").is_dir()
    cfg = DocFlowConfig.load(str(docs))
    names = [t.name for t in cfg.docs.types]
    assert names == ["front-end"]


def test_import_command_skips_existing(tmp_path, monkeypatch):
    _user_dirs(tmp_path, monkeypatch)
    monkeypatch.chdir(tmp_path)
    docs = tmp_path / "docs"
    docs.mkdir()
    src = tmp_path / "old"
    src.mkdir()
    (src / "guide.md").write_text("first\n")
    runner = CliRunner()
    first = runner.invoke(
        cli,
        ["import", "--docs", str(docs), "--from", str(src), "--type", "front-end"],
    )
    assert first.exit_code == 0, first.output
    assert (docs / "front-end" / "guide.md").read_text() == "first\n"
    (src / "guide.md").write_text("changed\n")
    second = runner.invoke(
        cli,
        ["import", "--docs", str(docs), "--from", str(src), "--type", "front-end"],
    )
    assert second.exit_code == 0, second.output
    assert "skip" in second.output.lower() or "skipped" in second.output.lower()
    assert (docs / "front-end" / "guide.md").read_text() == "first\n"


def test_generate_skips_when_already_documented(tmp_path, monkeypatch):
    _user_dirs(tmp_path, monkeypatch)
    monkeypatch.chdir(tmp_path)
    app = tmp_path / "app"
    docs = tmp_path / "docs"
    app.mkdir()
    _git_repo(app)
    runner = CliRunner()
    init = runner.invoke(
        cli,
        ["init", "--repo", str(app), "--docs", str(docs), "--agent", "manual", "--fresh"],
    )
    assert init.exit_code == 0, init.output
    assert (docs / ".docflow" / "state.json").exists()
    first = runner.invoke(cli, ["generate", "--repo", str(app), "--agent", "manual"])
    assert first.exit_code == 0, first.output
    assert "already" in first.output.lower()
    pending_before = list((docs / ".docflow" / "prompts" / "pending").glob("update-*.md"))
    second = runner.invoke(cli, ["generate", "--repo", str(app), "--agent", "manual"])
    assert second.exit_code == 0, second.output
    pending_after = list((docs / ".docflow" / "prompts" / "pending").glob("update-*.md"))
    assert pending_after == pending_before


def test_pull_without_remote_fails_cleanly(tmp_path, monkeypatch):
    _user_dirs(tmp_path, monkeypatch)
    monkeypatch.chdir(tmp_path)
    app = tmp_path / "app"
    docs = tmp_path / "docs"
    app.mkdir()
    docs.mkdir()
    _git_repo(app)
    cfg = DocFlowConfig()
    cfg.project.name = "Sample"
    cfg.app.repo_path = str(app)
    cfg.docs.repo_path = str(docs)
    cfg.save(str(docs))
    register_project(str(docs), str(app), "Sample")
    runner = CliRunner()
    result = runner.invoke(cli, ["pull", "--repo", str(app)])
    assert result.exit_code != 0
    assert "pull" in result.output.lower()


def test_status_from_third_dir_uses_last_opened(tmp_path, monkeypatch):
    _user_dirs(tmp_path, monkeypatch)
    app = tmp_path / "app"
    docs = tmp_path / "docs"
    app.mkdir()
    _git_repo(app)
    runner = CliRunner()
    init = runner.invoke(
        cli,
        ["init", "--repo", str(app), "--docs", str(docs), "--agent", "manual", "--fresh"],
    )
    assert init.exit_code == 0, init.output
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    status = runner.invoke(cli, ["status"])
    assert status.exit_code == 0, status.output
    assert last_project() is not None
    assert str(docs.resolve()) in status.output or "app" in status.output.lower()


def test_status_docs_flag_loads_docs_folder(tmp_path, monkeypatch):
    _user_dirs(tmp_path, monkeypatch)
    monkeypatch.chdir(tmp_path)
    app = tmp_path / "app"
    docs = tmp_path / "docs"
    app.mkdir()
    docs.mkdir()
    _git_repo(app)
    cfg = DocFlowConfig()
    cfg.project.name = "FromDocs"
    cfg.app.repo_path = str(app)
    cfg.docs.repo_path = str(docs)
    cfg.save(str(docs))
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    runner = CliRunner()
    status = runner.invoke(cli, ["status", "--docs", str(docs)])
    assert status.exit_code == 0, status.output
    assert "FromDocs" in status.output


def test_projects_crud_via_cli(tmp_path, monkeypatch):
    _user_dirs(tmp_path, monkeypatch)
    monkeypatch.chdir(tmp_path)
    app = tmp_path / "app"
    docs = tmp_path / "docs"
    other = tmp_path / "other-docs"
    app.mkdir()
    other.mkdir()
    (other / ".docflow.yml").write_text(
        "project:\n  name: other\napp:\n  repo_path: /tmp/other-app\n"
    )
    _git_repo(app)
    runner = CliRunner()
    init = runner.invoke(
        cli,
        ["init", "--repo", str(app), "--docs", str(docs), "--agent", "manual", "--fresh"],
    )
    assert init.exit_code == 0, init.output
    listed = runner.invoke(cli, ["projects", "list"])
    assert listed.exit_code == 0, listed.output
    assert "app" in listed.output or str(docs) in listed.output
    added = runner.invoke(cli, ["projects", "add", "--docs", str(other)])
    assert added.exit_code == 0, added.output
    opened = runner.invoke(cli, ["projects", "open", "other"])
    assert opened.exit_code == 0, opened.output
    listed2 = runner.invoke(cli, ["projects", "list"])
    assert listed2.exit_code == 0, listed2.output
    assert "*" in listed2.output
    assert last_project().name == "other"
    removed = runner.invoke(cli, ["projects", "remove", str(other)])
    assert removed.exit_code == 0, removed.output
    assert (other / ".docflow.yml").exists()
    listed3 = runner.invoke(cli, ["projects", "list"])
    assert listed3.exit_code == 0, listed3.output
    assert str(other) not in listed3.output

