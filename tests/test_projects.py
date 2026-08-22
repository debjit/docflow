"""
Tests for the user-level docs project index.
"""

from pathlib import Path

from docflow.core.projects import (
    find_by_app,
    find_by_docs,
    index_path,
    last_project,
    load_index,
    open_project,
    prune_missing,
    register_project,
    unregister_project,
)


def _xdg(tmp_path: Path, monkeypatch) -> Path:
    home = tmp_path / "home"
    xdg = tmp_path / "xdg"
    home.mkdir()
    xdg.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    return xdg


def test_index_path_honors_xdg(tmp_path, monkeypatch):
    xdg = _xdg(tmp_path, monkeypatch)
    assert index_path() == str(xdg / "docflow" / "projects.yml")


def test_register_upsert_and_last_project(tmp_path, monkeypatch):
    _xdg(tmp_path, monkeypatch)
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / ".docflow.yml").write_text("project:\n  name: myapp\napp:\n  repo_path: /app\n")
    first = register_project(str(docs), "/app", "myapp")
    assert first.name == "myapp"
    assert first.docs_path == str(docs.resolve())
    again = register_project(str(docs), "/other-app", "")
    assert again.app_path == str(Path("/other-app").resolve())
    entries = load_index()
    assert len(entries) == 1
    assert last_project().docs_path == first.docs_path


def test_find_by_app_matches_inside(tmp_path, monkeypatch):
    _xdg(tmp_path, monkeypatch)
    app = tmp_path / "app"
    inner = app / "src"
    docs = tmp_path / "docs"
    inner.mkdir(parents=True)
    docs.mkdir()
    (docs / ".docflow.yml").write_text("project:\n  name: nested\n")
    register_project(str(docs), str(app), "nested")
    assert find_by_docs(str(docs)).name == "nested"
    assert find_by_app(str(app)).name == "nested"
    assert find_by_app(str(inner)).name == "nested"


def test_open_switches_last_and_remove_keeps_files(tmp_path, monkeypatch):
    _xdg(tmp_path, monkeypatch)
    docs_a = tmp_path / "a-docs"
    docs_b = tmp_path / "b-docs"
    for folder, name in ((docs_a, "alpha"), (docs_b, "beta")):
        folder.mkdir()
        (folder / ".docflow.yml").write_text(f"project:\n  name: {name}\n")
        (folder / "keep.md").write_text("stay\n")
        register_project(str(folder), "", name)
    opened = open_project(str(docs_b))
    assert last_project().docs_path == opened.docs_path
    assert last_project().name == "beta"
    assert unregister_project(str(docs_b))
    assert find_by_docs(str(docs_b)) is None
    assert (docs_b / ".docflow.yml").exists()
    assert (docs_b / "keep.md").read_text() == "stay\n"


def test_remove_project_can_delete_docs_folder(tmp_path, monkeypatch):
    from docflow.core.projects import remove_project

    _xdg(tmp_path, monkeypatch)
    docs = tmp_path / "stale-docs"
    docs.mkdir()
    (docs / ".docflow.yml").write_text("project:\n  name: stale\n")
    (docs / "keep.md").write_text("docs\n")
    register_project(str(docs), "/app", "stale")
    removed, note = remove_project(str(docs), delete_docs=True)
    assert removed is True
    assert "deleted" in note
    assert find_by_docs(str(docs)) is None
    assert not docs.exists()


def test_prune_missing(tmp_path, monkeypatch):
    _xdg(tmp_path, monkeypatch)
    gone = tmp_path / "gone"
    gone.mkdir()
    (gone / ".docflow.yml").write_text("project:\n  name: gone\n")
    register_project(str(gone), "", "gone")
    stale = tmp_path / "stale"
    stale.mkdir()
    register_project(str(stale), "", "stale")
    keep = tmp_path / "keep"
    keep.mkdir()
    (keep / ".docflow.yml").write_text("project:\n  name: keep\n")
    register_project(str(keep), "", "keep")
    gone_path = str(gone.resolve())
    import shutil

    shutil.rmtree(gone)
    kept = prune_missing()
    paths = {entry.docs_path for entry in kept}
    assert str(keep.resolve()) in paths
    assert gone_path not in paths
    assert str(stale.resolve()) not in paths


def test_last_usable_project_skips_missing_docs(tmp_path, monkeypatch):
    from docflow.core.projects import last_usable_project

    _xdg(tmp_path, monkeypatch)
    gone = tmp_path / "gone-docs"
    gone.mkdir()
    (gone / ".docflow.yml").write_text("project:\n  name: gone\n")
    register_project(str(gone), "/app", "gone")
    keep = tmp_path / "keep-docs"
    keep.mkdir()
    (keep / ".docflow" / "config.yml").parent.mkdir(parents=True)
    (keep / ".docflow" / "config.yml").write_text(
        "project:\n  name: keep\napp:\n  repo_path: /app\n"
    )
    register_project(str(keep), "/app", "keep")
    import shutil

    shutil.rmtree(gone)
    usable = last_usable_project()
    assert usable is not None
    assert usable.docs_path == str(keep.resolve())
