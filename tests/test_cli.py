"""
CLI tests for DocFlow.
"""

from pathlib import Path

from click.testing import CliRunner
from git import Repo

from docflow.cli.main import cli
from docflow.config.settings import DocFlowConfig


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
    for name in ("init", "generate", "status", "info", "publish", "serve", "ui", "import", "pull"):
        assert name in result.output


def test_init_does_not_write_home_config(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
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
    assert (app / ".docflow.yml").exists()
    assert (docs / ".docflow.yml").exists()
    assert not (home / ".docflow.yml").exists()
    cwd_config = tmp_path / ".docflow.yml"
    assert not cwd_config.exists()


def test_generate_uses_config_without_prompting(tmp_path, monkeypatch):
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
    cfg.save(str(app))

    runner = CliRunner()
    result = runner.invoke(cli, ["generate", "--repo", str(app), "--agent", "manual"])
    assert result.exit_code == 0, result.output
    pending = list((docs / "prompts" / "pending").glob("*.md"))
    assert pending, result.output


def test_status_and_info_alias(tmp_path, monkeypatch):
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
    cfg.save(str(app))

    runner = CliRunner()
    status = runner.invoke(cli, ["status", "--repo", str(app)])
    info = runner.invoke(cli, ["info", "--repo", str(app)])
    assert status.exit_code == 0, status.output
    assert info.exit_code == 0, info.output
    assert "Sample" in status.output
    assert "Sample" in info.output
    assert not (docs / "status" / "wip.md").exists()


def test_generate_without_config_aborts(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    result = runner.invoke(cli, ["generate"])
    assert result.exit_code != 0
    assert "not set" in result.output.lower() or "aborted" in result.output.lower()


def test_init_refuses_second_run_and_nonempty(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
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
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
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
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
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
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
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
    assert (docs / ".docflow-state.json").exists()
    first = runner.invoke(cli, ["generate", "--repo", str(app), "--agent", "manual"])
    assert first.exit_code == 0, first.output
    assert "already" in first.output.lower()
    pending_before = list((docs / "prompts" / "pending").glob("update-*.md"))
    second = runner.invoke(cli, ["generate", "--repo", str(app), "--agent", "manual"])
    assert second.exit_code == 0, second.output
    pending_after = list((docs / "prompts" / "pending").glob("update-*.md"))
    assert pending_after == pending_before


def test_pull_without_remote_fails_cleanly(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
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
    cfg.save(str(app))
    runner = CliRunner()
    result = runner.invoke(cli, ["pull", "--repo", str(app)])
    assert result.exit_code != 0
    assert "pull" in result.output.lower()
