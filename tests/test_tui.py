"""
Smoke test for the Textual UI.
"""

import pytest

from docflow.tui.app import DocFlowApp


@pytest.mark.asyncio
async def test_tui_composes():
    app = DocFlowApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.query_one("#summary")
        assert app.query_one("#log")
        assert app.query_one("#btn-generate")


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
