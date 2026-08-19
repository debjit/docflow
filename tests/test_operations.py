"""
Tests for shared operations helpers.
"""

from docflow.config.settings import DocFlowConfig, DocTypeSettings
from docflow.core.agent_runner import AGENT_PRESETS
from docflow.core.operations import (
    AlreadyInitialized,
    ConfigError,
    apply_agent_model,
    assert_can_init,
    catalog_agy_models,
    catalog_cursor_models,
    default_cursor_model,
    generate_docs,
    generate_section_names,
    import_docs,
    init_docs,
    load_generate_cursor,
    mark_repo_documented,
    new_commits_since,
    parse_agy_model_list,
    parse_cursor_model_list,
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


def test_resolve_agent_cursor_cli():
    spec = resolve_agent(agent="cursor-agent")
    assert spec is not None
    assert spec.mode == "shell"
    assert spec.command == AGENT_PRESETS["cursor-agent"]
    assert spec.command.startswith("agent ")
    assert "-p" in spec.command
    editor = resolve_agent(agent="cursor")
    assert editor is not None
    assert editor.command == spec.command


def test_parse_cursor_model_list():
    rows = parse_cursor_model_list(
        "Available models\n\n"
        "auto - Auto (current, default)\n"
        "composer-2.5 - Composer 2.5\n"
        "cursor-grok-4.6-high - Cursor Grok 4.6\n"
        "\nTip: use --model <id>\n"
    )
    assert rows[0] == ("auto", "Auto (current, default)")
    assert ("composer-2.5", "Composer 2.5") in rows
    assert ("cursor-grok-4.6-high", "Cursor Grok 4.6") in rows


def test_catalog_cursor_models_groups_and_labels_normal():
    catalog = catalog_cursor_models(
        [
            ("auto", "Auto (current, default)"),
            ("gpt-5.2", "GPT-5.2"),
            ("composer-2.5", "Composer 2.5"),
            ("composer-2.5-fast", "Composer 2.5 Fast"),
            ("cursor-grok-4.6-high", "Cursor Grok 4.6"),
            ("claude-opus-5-high", "Opus 5 1M"),
        ]
    )
    current = [c for c in catalog if c.group == "current"]
    third = [c for c in catalog if c.group == "third_party"]
    assert [c.key for c in current][:3] == ["auto", "composer-2.5", "composer-2.5-fast"]
    assert default_cursor_model(catalog) == "composer-2.5"
    assert {c.group_label for c in current} == {"Cursor included usage"}
    assert {c.group_label for c in third} == {"Third-party API usage"}
    assert "Composer 2.5 (normal)" in {c.label for c in current}
    assert {c.key for c in third} == {"gpt-5.2", "claude-opus-5-high"}
    assert all(c.group == "third_party" for c in third)


def test_default_cursor_model_without_composer_25():
    catalog = catalog_cursor_models(
        [
            ("auto", "Auto"),
            ("composer-2", "Composer 2"),
            ("gpt-5.2", "GPT-5.2"),
        ]
    )
    assert default_cursor_model(catalog) == "composer-2"
    no_composer = catalog_cursor_models([("auto", "Auto"), ("gpt-5.2", "GPT-5.2")])
    assert default_cursor_model(no_composer) == "auto"


def test_parse_and_catalog_agy_models():
    rows = parse_agy_model_list(
        "Fetching available models...\n"
        "gemini-3.7-flash-high\tGemini 3.7 Flash (High)\n"
        "claude-sonnet-4-6\tClaude Sonnet 4.6 (Thinking)\n"
        "gpt-oss-120b-medium\tGPT-OSS 120B (Medium)\n"
    )
    assert rows[0] == ("gemini-3.7-flash-high", "Gemini 3.7 Flash (High)")
    catalog = catalog_agy_models(rows)
    current = [c for c in catalog if c.group == "current"]
    third = [c for c in catalog if c.group == "third_party"]
    gemini = next(c for c in current if c.key == "gemini-3.7-flash-high")
    assert gemini.value == "gemini-3.7-flash-high"
    assert gemini.label == "Gemini 3.7 Flash (High)"
    assert {c.label for c in third} == {"Claude Sonnet 4.6 (Thinking)", "GPT-OSS 120B (Medium)"}
    assert all(c.value == c.key for c in third)


def test_apply_agent_model_cursor():
    spec = resolve_agent(agent="cursor-agent")
    updated = apply_agent_model(spec, "composer-2.5")
    assert updated.command.startswith("agent --model composer-2.5 ")
    cleared = apply_agent_model(updated, "auto")
    assert "--model" not in cleared.command
    flagged = resolve_agent(agent="cursor-agent", model="gpt-5.2")
    assert "--model gpt-5.2" in flagged.command
    agy = apply_agent_model(resolve_agent(agent="agy"), "gemini-3.7-flash-high")
    assert agy.command.startswith("agy --model gemini-3.7-flash-high ")
    assert agy.command.index("--model") < agy.command.index(" -p ")


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


def test_init_docs_custom_type_and_refuses_rerun(tmp_path, monkeypatch):
    from git import Repo

    from docflow.core.operations import AgentSpec

    xdg = tmp_path / "xdg"
    xdg.mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()

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
    assert not (app / ".docflow.yml").exists()
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


def test_generate_section_names_groups_wrappers_and_features():
    from docflow.core.models import FileChange

    files = [
        FileChange(path="src/auth/login.py", change_type="modified"),
        FileChange(path="src/billing/stripe.py", change_type="added"),
        FileChange(path="ui/header.tsx", change_type="modified"),
        FileChange(path="auth/tokens.py", change_type="modified"),
    ]
    assert generate_section_names(files) == ["auth", "billing", "ui"]
    assert generate_section_names(files, feature="billing") == ["billing"]
    scaffold = files + [
        FileChange(path="bootstrap/app.php", change_type="modified"),
        FileChange(path="vendor/laravel/framework/src/Application.php", change_type="modified"),
    ]
    skip = {"bootstrap", "vendor", "public"}
    assert generate_section_names(scaffold, skip_as_feature=skip) == ["auth", "billing", "ui"]


def test_generate_docs_writes_prompts_for_each_feature(tmp_path):
    from git import Repo

    from docflow.core.operations import AgentSpec

    app = tmp_path / "app"
    docs = tmp_path / "docs"
    app.mkdir()
    repo = Repo.init(app)
    (app / "README.md").write_text("# App\n")
    repo.index.add(["README.md"])
    repo.index.commit("init")
    (app / "auth").mkdir()
    (app / "billing").mkdir()
    (app / "auth" / "login.py").write_text("def login(): pass\n")
    (app / "billing" / "stripe.py").write_text("def charge(): pass\n")
    repo.index.add(["auth/login.py", "billing/stripe.py"])
    repo.index.commit("add auth and billing")

    spec = AgentSpec(mode="manual", command="", name="manual")
    result = generate_docs(
        app_repo_path=str(app),
        docs_repo_path=str(docs),
        agent=spec,
        config=DocFlowConfig(),
        from_ref="HEAD~1",
        to_ref="HEAD",
    )
    names = [item.feature_name for item in result.features]
    assert names == ["auth", "billing"]
    assert (docs / "prompts" / "pending" / "update-auth.md").exists()
    assert (docs / "prompts" / "pending" / "update-billing.md").exists()
    cursor = load_generate_cursor(str(docs))
    assert cursor is not None


def test_generate_docs_pulls_when_remote_is_ahead(tmp_path):
    from git import Repo

    from docflow.core.operations import AgentSpec

    remote = tmp_path / "remote.git"
    app = tmp_path / "app"
    other = tmp_path / "other"
    docs = tmp_path / "docs"
    Repo.init(remote, bare=True)
    repo = Repo.init(app)
    (app / "README.md").write_text("# App\n")
    repo.index.add(["README.md"])
    first = repo.index.commit("init")
    branch = repo.active_branch.name
    origin = repo.create_remote("origin", str(remote))
    origin.push(branch)
    repo.git.branch(f"--set-upstream-to=origin/{branch}")
    mark_repo_documented(str(app), str(docs))

    Repo.clone_from(str(remote), other)
    other_repo = Repo(other)
    (other / "auth").mkdir()
    (other / "auth" / "login.py").write_text("def login(): pass\n")
    other_repo.index.add(["auth/login.py"])
    other_repo.index.commit("add auth")
    other_repo.remotes.origin.push()

    spec = AgentSpec(mode="manual", command="", name="manual")
    result = generate_docs(
        app_repo_path=str(app),
        docs_repo_path=str(docs),
        agent=spec,
        config=DocFlowConfig(),
    )
    assert result.synced_remote
    assert not result.already_current
    names = [item.feature_name for item in result.features]
    assert "auth" in names
    assert (docs / "prompts" / "pending" / "update-auth.md").exists()
    assert Repo(app).head.commit.hexsha != first.hexsha


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
