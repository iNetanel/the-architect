"""Tests for the Textual ConfigApp."""

from __future__ import annotations

from pathlib import Path

import pytest
from textual.widgets import DataTable

from the_architect.config import ArchitectConfig
from the_architect.tui.screens.config import ConfigApp


@pytest.mark.asyncio
async def test_config_screen_shows_all_rows() -> None:
    config = ArchitectConfig()
    app = ConfigApp(config=config, toml_path=Path("/tmp/architect.toml"), has_toml=False)
    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.query_one(DataTable)
        assert table.row_count == 20  # known row count; update when adding new fields


@pytest.mark.asyncio
async def test_config_screen_renders_integrity_field() -> None:
    config = ArchitectConfig(integrity=False)
    app = ConfigApp(config=config, toml_path=Path("/tmp/architect.toml"), has_toml=True)
    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.query_one(DataTable)
        # Find the row with "integrity" key.
        rows = [
            (str(table.get_cell_at((i, 0))), str(table.get_cell_at((i, 1))))
            for i in range(table.row_count)
        ]
        row_map = dict(rows)
        assert row_map["integrity"] == "False"


@pytest.mark.asyncio
async def test_config_screen_quits_on_q() -> None:
    config = ArchitectConfig()
    app = ConfigApp(config=config, toml_path=Path("/tmp/architect.toml"), has_toml=False)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("q")
        await pilot.pause()
    # App exits cleanly; no assertion needed beyond no-exception.
