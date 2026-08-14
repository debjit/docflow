"""
Tests for shared operations helpers.
"""

from docflow.config.settings import DocFlowConfig, DocTypeSettings
from docflow.core.agent_runner import AGENT_PRESETS
from docflow.core.operations import (
    AlreadyInitialized,
    ConfigError,
    assert_can_init,
    import_docs,
    init_docs,
    load_generate_cursor,
    mark_repo_documented,
    new_commits_since,
    parse_doc_type,
    parse_doc_types_text,
    pull_app_repo,
    refs_for_last_n_commits,
    resolve_agent,
    slug_type_name,
)


def test_resolve_agent_flags_win():
    spec = resolve_agent(agent="claude", config=DocFlowConfig())
    assert spec is not None
    assert spec.mode == "shell"
    assert spec.command == AGENT_PRESETS["claude"]


def test_resolve_agent_manual():
    spec = resolve_agent(agent="manual")
    assert spec is not None
    assert spec.mode == "manual"
    assert spec.command == ""


def test_resolve_agent_custom_command():
    spec = resolve_agent(command="echo {prompt_file}")
    assert spec is not None
    assert spec.mode == "shell"
    assert spec.command == "echo {prompt_file}"


def test_resolve_agent_uses_saved_config():
    cfg = DocFlowConfig()
    cfg.source_path = "/tmp/.docflow.yml"
    cfg.agent.mode = "shell"
    cfg.agent.command = AGENT_PRESETS["opencode"]
    spec = resolve_agent(config=cfg)
    assert spec is not None
    assert spec.command == AGENT_PRESETS["opencode"]


def test_resolve_agent_ignores_defaults_without_file():
    spec = resolve_agent(config=DocFlowConfig())
    assert spec is None


def test_refs_for_last_n_commits():
    assert refs_for_last_n_commits(1) == ("HEAD~1", "HEAD")
    assert refs_for_last_n_commits(3) == ("HEAD~3", "HEAD")
    assert refs_for_last_n_commits(0) == ("HEAD~1", "HEAD")


def test_parse_doc_type_and_slug():
    parsed = parse_doc_type("front-end: React UI docs")
    assert parsed.name == "front-end"
    assert parsed.description == "React UI docs"
    assert slug_type_name("Front End") == "front-end"
    types = parse_doc_types_text("architecture: layout\nfront-end: React\n# skip\nfront-end: dup")
    assert [t.name for t in types] == ["architecture", "front-end"]


def test_assert_can_init_refuses_non_empty_and_initialized(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    assert_can_init(str(empty))

    leftover = tmp_path / "used"
    leftover.mkdir()
    (leftover / "README.md").write_text("keep me\n")
    try:
        assert_can_init(str(leftover))
        assert False, "expected ConfigError"
    except ConfigError as exc:
        assert "not empty" in str(exc)

    initialized = tmp_path / "docs"
    initialized.mkdir()
    (initialized / ".docflow.yml").write_text("project:\n  name: Demo\n")
    try:
        assert_can_init(str(initialized))
        assert False, "expected AlreadyInitialized"
    except AlreadyInitialized:
        pass


def test_import_docs_never_overwrites(tmp_path):
    src = tmp_path / "old"
    src.mkdir()
    (src / "guide.md").write_text("first\n")
    dest = tmp_path / "docs"
    dest.mkdir()
    first = import_docs(str(src), str(dest), "front-end")
    assert first.copied == ["guide.md"]
    assert first.skipped == []
    written = dest / "front-end" / "guide.md"
    assert written.read_text() == "first\n"
    (src / "guide.md").write_text("second\n")
    (src / "extra.md").write_text("new\n")
    again = import_docs(str(src), str(dest), "front-end")
    assert "guide.md" in again.skipped
    assert "extra.md" in again.copied
    assert written.read_text() == "first\n"


def test_init_docs_custom_type_and_refuses_rerun(tmp_path):
    from git import Repo

    from docflow.core.operations import AgentSpec

    app = tmp_path / "app"
    docs = tmp_path / "docs"
    app.mkdir()
    repo = Repo.init(app)
    (app / "README.md").write_text("# App\n")
    repo.index.add(["README.md"])
    repo.index.commit("init")
    spec = AgentSpec(mode="manual", command="", name="manual")
    result = init_docs(
        app_repo_path=str(app),
        docs_repo_path=str(docs),
        agent=spec,
        types=[DocTypeSettings(name="front-end", description="React UI docs")],
    )
    assert "front-end" in result.types
    assert (docs / "front-end").is_dir()
    assert (docs / ".docflow.yml").exists()
    try:
        init_docs(str(app), str(docs), spec)
        assert False, "expected AlreadyInitialized"
    except AlreadyInitialized:
        pass


def test_generate_cursor_tracks_new_commits_only(tmp_path):
    from git import Repo

    app = tmp_path / "app"
    docs = tmp_path / "docs"
    app.mkdir()
    docs.mkdir()
    repo = Repo.init(app)
    (app / "README.md").write_text("# App\n")
    repo.index.add(["README.md"])
    first = repo.index.commit("init")
    mark_repo_documented(str(app), str(docs))
    cursor = load_generate_cursor(str(docs))
    assert cursor is not None
    assert cursor.head_sha == first.hexsha
    _, new_commits, stale = new_commits_since(str(app), str(docs))
    assert not stale
    assert new_commits == []
    (app / "extra.md").write_text("more\n")
    repo.index.add(["extra.md"])
    second = repo.index.commit("add extra")
    _, new_commits, stale = new_commits_since(str(app), str(docs))
    assert not stale
    assert [c.sha for c in new_commits] == [second.hexsha]


def test_pull_without_origin_reports_failure(tmp_path):
    from git import Repo

    app = tmp_path / "app"
    app.mkdir()
    repo = Repo.init(app)
    (app / "README.md").write_text("# App\n")
    repo.index.add(["README.md"])
    repo.index.commit("init")
    result = pull_app_repo(str(app), str(tmp_path / "docs"))
    assert result.success is False
    assert result.output
