"""
Smoke test for the Textual UI.
"""

import pytest

from docflow.tui.app import DocFlowApp, _agent_select_options


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
        assert app.query_one("#log")
        assert app.query_one("#btn-generate")


@pytest.mark.asyncio
async def test_update_docs_modal_has_agent_select():
    app = DocFlowApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.click("#btn-generate")
        await pilot.pause()
        generate = next(
            screen for screen in app.screen_stack if screen.__class__.__name__ == "GenerateScreen"
        )
        assert generate.query_one("#agent")
        assert generate.query_one("#model-picker")
        assert generate.query_one("#model-list")


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
async def test_setup_and_publish_open_modals():
    app = DocFlowApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.click("#btn-setup")
        await pilot.pause()
        assert any(
            screen.__class__.__name__ in ("SetupScreen", "ImportScreen")
            for screen in app.screen_stack
        )
        await pilot.press("escape")
        await pilot.pause()
        await pilot.click("#btn-publish")
        await pilot.pause()
        assert any(screen.__class__.__name__ == "PublishScreen" for screen in app.screen_stack)


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
        listing = app.query_one("#model-list")
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
async def test_run_clears_log_and_shows_latest_first():
    app = DocFlowApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        app._log("stale line from a previous run")
        app._begin_run("Updating docs…")
        assert app.sub_title == "Updating docs…"
        assert list(app._lines) == []
        app._progress("step one")
        app._progress("step two")
        app._finish_run("Update finished: update / auth")
        lines = list(app._lines)
        assert lines[0].startswith("Update finished")
        assert "step two" in lines[1]
        assert "stale line from a previous run" not in lines
        assert app.sub_title.startswith("Update finished")
