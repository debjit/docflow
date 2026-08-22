"""
Smoke test for the Textual UI.
"""

import os
import subprocess

import pytest

from textual.widgets import OptionList, Static

from docflow.tui.app import DocFlowApp, ModelPicker, _agent_select_options


def _make_project(tmp_path):
    """Tiny git app repo plus a docs repo whose config points at it."""
    app_repo = tmp_path / "app"
    docs = tmp_path / "docs"
    app_repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=app_repo, check=False)
    (docs / ".docflow").mkdir(parents=True)
    (docs / ".docflow" / "config.yml").write_text(
        "project:\n"
        "  name: t\n"
        "app:\n"
        f'  repo_path: "{app_repo}"\n'
        "agent:\n"
        "  mode: manual\n"
        "  name: manual\n"
        "  command: manual\n"
    )
    return str(app_repo), str(docs)


def _configured_app(tmp_path):
    app_repo, docs = _make_project(tmp_path)
    return DocFlowApp(repo=app_repo, docs=docs)


def test_agent_select_includes_cursor_agent():
    keys = [key for _label, key in _agent_select_options()]
    assert "cursor-agent" in keys
    assert "cursor-interactive" in keys


@pytest.mark.asyncio
async def test_tui_composes():
    app = DocFlowApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.query_one("#summary")
        assert app.query_one("#progress")
        assert app.query_one("#log")
        assert app.query_one("#btn-pause")
        assert app.query_one("#btn-generate")


@pytest.mark.asyncio
async def test_update_docs_modal_has_agent_select(tmp_path):
    app = _configured_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("u")
        await pilot.pause()
        generate = next(
            screen for screen in app.screen_stack if screen.__class__.__name__ == "GenerateScreen"
        )
        assert generate.query_one("#agent")
        assert generate.query_one("#model-row")
        assert generate.query_one("#model-label")
        assert generate.query_one("#change-model")
        assert generate.query_one("#jobs")
        assert generate.query_one("#app-branch")


@pytest.mark.asyncio
async def test_update_docs_modal_uses_two_pane_layout(tmp_path):
    from textual.containers import Vertical

    app = _configured_app(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await pilot.press("u")
        await pilot.pause()
        generate = next(
            screen for screen in app.screen_stack if screen.__class__.__name__ == "GenerateScreen"
        )
        columns = generate.query_one("#generate-columns")
        left = generate.query_one(".setup-left", Vertical)
        right = generate.query_one(".setup-right", Vertical)
        assert right.query_one("#agent")
        assert right.query_one("#jobs")
        assert left.query_one("#app-branch")
        assert left.query_one("#source")
        assert not left.query("#agent")


@pytest.mark.asyncio
async def test_change_model_opens_model_select_modal(tmp_path):
    from textual.widgets import Button

    app = _configured_app(tmp_path)
    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.pause()
        await pilot.press("u")
        await pilot.pause()
        generate = next(
            screen for screen in app.screen_stack if screen.__class__.__name__ == "GenerateScreen"
        )
        generate.query_one("#change-model", Button).press()
        await pilot.pause()
        model_select = next(
            screen for screen in app.screen_stack if screen.__class__.__name__ == "ModelSelectScreen"
        )
        assert model_select.query_one("#work-model-picker")
        assert model_select.query_one("#plan-model-picker")
        assert model_select.query_one("#ok")
        assert model_select.query_one("#cancel")
        await pilot.press("escape")
        await pilot.pause()
        assert not any(s.__class__.__name__ == "ModelSelectScreen" for s in app.screen_stack)


@pytest.mark.asyncio
async def test_regen_last_modal_has_agent_and_exit():
    from docflow.tui.app import RegenLastScreen

    app = DocFlowApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        await app.push_screen(RegenLastScreen())
        await pilot.pause()
        regen = next(
            screen for screen in app.screen_stack if screen.__class__.__name__ == "RegenLastScreen"
        )
        assert regen.query_one("#agent")
        assert regen.query_one("#ok")
        assert regen.query_one("#cancel")


@pytest.mark.asyncio
async def test_section_picker_lists_candidates_and_add_input():
    from docflow.core.operations import SectionCandidate
    from docflow.tui.app import SectionPickerScreen

    app = DocFlowApp()
    candidates = [
        SectionCandidate(doc_type="architecture", name="architecture", included=True),
        SectionCandidate(doc_type="features", name="auth", file_paths=["src/auth/login.py"], included=True),
        SectionCandidate(doc_type="features", name="git", included=False),
    ]
    async with app.run_test() as pilot:
        await pilot.pause()
        await app.push_screen(SectionPickerScreen(candidates))
        await pilot.pause()
        picker = next(
            screen for screen in app.screen_stack if screen.__class__.__name__ == "SectionPickerScreen"
        )
        assert picker.query_one("#section-list")
        listing = picker.query_one("#section-list")
        values = [opt.value for opt in listing._options]
        assert "g-architecture" in values
        assert "s0" in values
        assert picker.query_one("#add-path")
        assert picker.query_one("#ok")
        assert picker.query_one("#add")


@pytest.mark.asyncio
async def test_setup_and_publish_open_modals(tmp_path):
    app = _configured_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("i")
        await pilot.pause()
        setup = next(
            (
                screen
                for screen in app.screen_stack
                if screen.__class__.__name__
                in ("SettingsScreen", "SetupWizardScreen", "ImportScreen")
            ),
            None,
        )
        assert setup is not None
        if setup.__class__.__name__ == "SetupScreen":
            assert setup.query_one("#app-branch")
        if setup.__class__.__name__ == "SettingsScreen":
            listing = setup.query_one("#settings-list")
            ids = {opt.id for opt in listing._options}
            assert {"setup", "import", "switch", "export", "pull"} <= ids
            import_option = next(opt for opt in listing._options if opt.id == "import")
            assert not import_option.disabled
        await pilot.press("escape")
        await pilot.pause()
        await pilot.press("p")
        await pilot.pause()
        assert any(screen.__class__.__name__ == "PublishScreen" for screen in app.screen_stack)


@pytest.mark.asyncio
async def test_switch_modal_has_delete():
    from docflow.tui.app import ProjectPickerScreen

    app = DocFlowApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        await app.push_screen(ProjectPickerScreen())
        await pilot.pause()
        picker = next(
            screen for screen in app.screen_stack if screen.__class__.__name__ == "ProjectPickerScreen"
        )
        assert picker.query_one("#delete")
        assert picker.query_one("#ok")
        assert picker.query_one("#project-list")


@pytest.mark.asyncio
async def test_model_picker_cursor_headers_and_default():
    from textual.app import App, ComposeResult

    from docflow.core.operations import catalog_cursor_models
    from docflow.tui.app import ModelPicker

    class PickerApp(App):
        def compose(self) -> ComposeResult:
            yield ModelPicker(id="model-picker")

    catalog = catalog_cursor_models(
        [
            ("auto", "Auto"),
            ("composer-2.5", "Composer 2.5"),
            ("gpt-5.2", "GPT-5.2"),
        ]
    )
    app = PickerApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        picker = app.query_one("#model-picker", ModelPicker)
        picker.set_choices(catalog)
        assert picker.selected_value() == "composer-2.5"
        listing = picker.query_one(OptionList)
        prompts = [
            str(listing.get_option_at_index(i).prompt)
            for i in range(listing.option_count)
            if not getattr(listing.get_option_at_index(i), "_divider", False)
        ]
        assert "Cursor included usage" in prompts
        assert "Third-party API usage" in prompts
        assert "Current" not in prompts
        assert "Third-party" not in prompts


@pytest.mark.asyncio
async def test_work_picker_defaults_to_fast_when_listed():
    from textual.app import App, ComposeResult

    from docflow.core.operations import catalog_cursor_models

    class PickerApp(App):
        def compose(self) -> ComposeResult:
            yield ModelPicker(role="work", id="model-picker")

    catalog = catalog_cursor_models(
        [
            ("auto", "Auto"),
            ("composer-2.5", "Composer 2.5"),
            ("composer-2.5-fast", "Composer 2.5 Fast"),
        ]
    )
    app = PickerApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        picker = app.query_one("#model-picker", ModelPicker)
        picker.set_choices(catalog)
        assert picker.selected_value() == "composer-2.5-fast"


@pytest.mark.asyncio
async def test_switch_new_clears_summary_header():
    app = DocFlowApp(repo="/tmp/old-app", docs="/tmp/old-docs")
    async with app.run_test() as pilot:
        await pilot.pause()
        app._begin_new_project()
        await pilot.pause()
        assert app._blank_session
        assert app._repo == ""
        assert app._docs == ""
        assert app.title == "DocFlow"
        assert app.sub_title == "New project"
        summary = str(app.query_one("#summary", Static).render())
        assert "not set" in summary
        extra = str(app.query_one("#summary-extra", Static).render())
        assert "New project" in extra


@pytest.mark.asyncio
async def test_summary_keeps_core_visible_and_details_collapsed(tmp_path):
    from textual.widgets import Collapsible

    app_repo, docs = _make_project(tmp_path)
    app = DocFlowApp(repo=app_repo, docs=docs)
    async with app.run_test() as pilot:
        await pilot.pause()
        summary = str(app.query_one("#summary", Static).render())
        for key in ("Project", "App", "Docs", "Branch"):
            assert key in summary
        assert "Pending prompts" not in summary
        assert "Features (" not in summary

        collapsible = app.query_one("#summary-details", Collapsible)
        assert collapsible.collapsed is True
        extra = str(app.query_one("#summary-extra", Static).render())
        assert "Pending prompts" in extra
        assert "Features (" in extra


@pytest.mark.asyncio
async def test_run_splits_progress_and_logs_and_can_pause():
    from docflow.tui.app import is_status_line

    assert is_status_line("Scanning app repo… 50 files")
    assert is_status_line("[2/4 done] features/auth")
    assert not is_status_line("I will now inspect composer.json and package.json")

    app = DocFlowApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        app._log("stale line from a previous run")
        app._begin_run("Updating docs…")
        assert app.sub_title == "Updating docs…"
        assert list(app._progress_lines) == ["Updating docs…"]
        assert list(app._log_lines) == []
        app._progress("Writing update prompt for auth…")
        app._progress("agent tool output that is noisy")
        app._progress("[1/2 done] auth")
        assert "Writing update prompt for auth…" in list(app._progress_lines)
        assert "agent tool output that is noisy" in list(app._log_lines)
        assert "stale line from a previous run" not in list(app._log_lines)
        app.action_toggle_pause()
        assert app._run_control.paused
        app._progress("hidden while paused")
        assert "hidden while paused" in app._paused_logs
        app.action_toggle_pause()
        assert not app._run_control.paused
        app._finish_run("Update finished: update / auth")
        assert list(app._progress_lines)[-1].startswith("Update finished")
        assert app.sub_title.startswith("Update finished")


@pytest.mark.asyncio
async def test_wizard_starts_on_welcome_and_validates_app_step(tmp_path):
    from textual.widgets import Button, Input

    from docflow.tui.app import SetupWizardScreen

    app_repo, docs = _make_project(tmp_path)
    app = DocFlowApp(repo=app_repo, docs=docs)
    async with app.run_test() as pilot:
        await pilot.pause()
        results = []
        await app.push_screen(SetupWizardScreen(), results.append)
        await pilot.pause()
        wizard = next(
            screen for screen in app.screen_stack if isinstance(screen, SetupWizardScreen)
        )
        assert wizard.query_one("#wizard-welcome").has_class("wizard-active")
        assert wizard.query_one("#wizard-back", Button).disabled is True

        wizard.query_one("#wizard-next", Button).press()
        await pilot.pause()
        assert wizard.query_one("#wizard-app").has_class("wizard-active")

        missing = str(tmp_path / "nope")
        wizard.query_one("#app-path", Input).value = missing
        await pilot.pause()
        wizard.query_one("#wizard-next", Button).press()
        await pilot.pause()
        error_label = wizard.query_one("#wizard-error")
        assert error_label.display
        assert wizard.query_one("#wizard-app").has_class("wizard-active")

        wizard.query_one("#app-path", Input).value = app_repo
        await pilot.pause()
        wizard.query_one("#wizard-next", Button).press()
        await pilot.pause()
        assert wizard.query_one("#wizard-docs").has_class("wizard-active")

        wizard.query_one("#wizard-back", Button).press()
        await pilot.pause()
        assert wizard.query_one("#wizard-app").has_class("wizard-active")
        assert not results


@pytest.mark.asyncio
async def test_wizard_docs_step_rejects_nonempty_folder(tmp_path):
    from textual.widgets import Button, Input

    from docflow.tui.app import SetupWizardScreen

    app_repo, docs = _make_project(tmp_path)
    app = DocFlowApp(repo=app_repo, docs=docs)
    async with app.run_test() as pilot:
        await pilot.pause()
        await app.push_screen(SetupWizardScreen())
        await pilot.pause()
        wizard = next(
            screen for screen in app.screen_stack if isinstance(screen, SetupWizardScreen)
        )
        busy = tmp_path / "busy-docs"
        busy.mkdir()
        (busy / "README.md").write_text("occupied")
        wizard.query_one("#docs-path", Input).value = str(busy)

        wizard._show_step(2)
        await pilot.pause()
        wizard.query_one("#wizard-next", Button).press()
        await pilot.pause()
        assert "not empty" in str(wizard.query_one("#wizard-error").render())

        empty = tmp_path / "empty-docs"
        empty.mkdir()
        wizard.query_one("#docs-path", Input).value = str(empty)
        wizard.query_one("#wizard-next", Button).press()
        await pilot.pause()
        assert wizard.query_one("#wizard-agent").has_class("wizard-active")


@pytest.mark.asyncio
async def test_wizard_manual_fallback_dismisses_sentinel(tmp_path):
    from textual.widgets import Button

    from docflow.tui.app import SetupWizardScreen

    app_repo, docs = _make_project(tmp_path)
    app = DocFlowApp(repo=app_repo, docs=docs)
    async with app.run_test() as pilot:
        await pilot.pause()
        results = []
        await app.push_screen(SetupWizardScreen(), results.append)
        await pilot.pause()
        wizard = next(
            screen for screen in app.screen_stack if isinstance(screen, SetupWizardScreen)
        )
        wizard.query_one("#wizard-manual", Button).press()
        await pilot.pause()
        assert results == [{"manual": True}]


@pytest.mark.asyncio
async def test_wizard_full_walk_collects_setup_contract(tmp_path):
    from textual.widgets import Button, Input, Select

    from docflow.tui.app import SetupWizardScreen

    expected_keys = {
        "app", "docs", "agent", "model", "plan_model",
        "types", "import_from", "import_into", "jobs", "branch",
    }

    app_repo, docs = _make_project(tmp_path)
    app = DocFlowApp(repo=app_repo, docs=docs)
    async with app.run_test() as pilot:
        await pilot.pause()
        results = []
        await app.push_screen(SetupWizardScreen(), results.append)
        await pilot.pause()
        wizard = next(
            screen for screen in app.screen_stack if isinstance(screen, SetupWizardScreen)
        )
        wizard.query_one("#agent", Select).value = "manual"
        await pilot.pause()

        for _ in range(5):
            wizard.query_one("#wizard-next", Button).press()
            await pilot.pause()

        assert wizard.query_one("#wizard-review").has_class("wizard-active")
        summary = str(wizard.query_one("#wizard-summary").render())
        assert app_repo in summary
        assert "manual" in summary

        wizard.query_one("#wizard-next", Button).press()
        await pilot.pause()
        assert len(results) == 1
        data = results[0]
        assert set(data.keys()) == expected_keys
        assert data["app"] == app_repo
        assert data["agent"] == "manual"
        assert data["jobs"] >= 1


@pytest.mark.asyncio
async def test_export_button_opens_export_modal(tmp_path):
    app = _configured_app(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await pilot.press("e")
        await pilot.pause()
        export_screen = next(
            screen for screen in app.screen_stack if screen.__class__.__name__ == "ExportScreen"
        )
        assert export_screen.query_one("#export-format")
        assert export_screen.query_one("#out-path")
        assert export_screen.query_one("#ok")
        assert export_screen.query_one("#cancel")


@pytest.mark.asyncio
async def test_export_modal_rejects_path_inside_docs_repo(tmp_path):
    from textual.widgets import Input

    app_repo, docs = _make_project(tmp_path)
    app = DocFlowApp(repo=app_repo, docs=docs)
    async with app.run_test() as pilot:
        await pilot.pause()
        results = []
        from docflow.tui.app import ExportScreen

        await app.push_screen(ExportScreen(), results.append)
        await pilot.pause()
        screen = next(
            screen for screen in app.screen_stack if isinstance(screen, ExportScreen)
        )
        inside = os.path.join(docs, "site-export")
        screen.query_one("#out-path", Input).value = inside
        await pilot.pause()
        screen.query_one("#ok").press()
        await pilot.pause()
        assert screen.query_one("#export-error").display
        assert results == []


@pytest.mark.asyncio
async def test_export_run_reports_summary(tmp_path, monkeypatch):
    from types import SimpleNamespace

    app_repo, docs = _make_project(tmp_path)
    calls = {}

    def fake_export_site(docs_repo_path, out_dir, fmt="docusaurus", **kwargs):
        calls["args"] = (docs_repo_path, out_dir, fmt)
        return SimpleNamespace(pages=2, out_dir=out_dir, files=[])

    monkeypatch.setattr("docflow.tui.app.export_site", fake_export_site)
    app = DocFlowApp(repo=app_repo, docs=docs)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.action_export()
        await pilot.pause()
        await pilot.pause()
        from docflow.tui.app import ExportScreen

        screen = next(
            screen for screen in app.screen_stack if isinstance(screen, ExportScreen)
        )
        out_path = str(tmp_path / "out" / "docusaurus")
        screen.query_one("#out-path").value = out_path
        await pilot.pause()
        screen.query_one("#ok").press()
        for _ in range(3):
            await pilot.pause()
        assert calls["args"][1] == out_path
        assert any("Exported 2 page(s)" in line for line in app._progress_lines)


@pytest.mark.asyncio
async def test_main_bar_is_minimal_with_settings_hub(tmp_path):
    from textual.widgets import Button

    app = _configured_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        ids = {b.id for b in app.query("#actions Button")}
        assert ids == {
            "btn-generate",
            "btn-publish",
            "btn-mcp",
            "btn-refresh",
            "btn-settings",
            "btn-pause",
        }
        assert "#btn-setup" not in str(ids)
        assert app.query_one("#btn-settings", Button).disabled is False


@pytest.mark.asyncio
async def test_settings_hub_routes_to_export_screen(tmp_path):
    from textual.widgets import OptionList

    app = _configured_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("i")
        await pilot.pause()
        settings = next(
            screen for screen in app.screen_stack if screen.__class__.__name__ == "SettingsScreen"
        )
        listing = settings.query_one("#settings-list")
        export_option = next(opt for opt in listing._options if opt.id == "export")
        index = next(
            i for i, opt in enumerate(listing._options) if opt.id == "export"
        )
        listing.post_message(
            OptionList.OptionSelected(listing, export_option, index)
        )
        await pilot.pause()
        await pilot.pause()
        assert any(
            screen.__class__.__name__ == "ExportScreen" for screen in app.screen_stack
        )


@pytest.mark.asyncio
async def test_settings_import_disabled_until_configured(tmp_path):
    from docflow.tui.app import SettingsScreen

    app = DocFlowApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        await app.push_screen(SettingsScreen())
        await pilot.pause()
        settings = next(
            screen for screen in app.screen_stack if isinstance(screen, SettingsScreen)
        )
        listing = settings.query_one("#settings-list")
        import_option = next(opt for opt in listing._options if opt.id == "import")
        export_option = next(opt for opt in listing._options if opt.id == "export")
        assert import_option.disabled
        assert export_option.disabled


@pytest.mark.asyncio
async def test_action_buttons_underline_shortcut_letters(tmp_path):
    from textual.widgets import Button

    app = _configured_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        expected = {
            "btn-generate": "Update docs",
            "btn-publish": "Publish",
            "btn-mcp": "MCP (SSE)",
            "btn-refresh": "Refresh",
            "btn-settings": "Settings",
        }
        for button_id, plain in expected.items():
            label = app.query_one(f"#{button_id}", Button).label
            assert getattr(label, "plain", str(label)) == plain
            assert any(
                getattr(span, "style", "") == "underline" and span.start == 0
                for span in getattr(label, "spans", [])
            ), f"{button_id} missing underline on first letter"


@pytest.mark.asyncio
async def test_mnemonic_keys_trigger_actions(tmp_path):
    app = _configured_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("s")
        await pilot.pause()
        assert any(
            screen.__class__.__name__ == "SettingsScreen"
            for screen in app.screen_stack
        )
        await pilot.press("escape")
        await pilot.pause()
        await pilot.press("n")
        for _ in range(3):
            await pilot.pause()
        from docflow.tui.app import SetupWizardScreen

        assert any(
            isinstance(screen, SetupWizardScreen) for screen in app.screen_stack
        )
