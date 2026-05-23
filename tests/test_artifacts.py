"""Tests for the_architect.core.artifacts — inter-task artifact storage.

Covers:
- Artifact Pydantic model validation and defaults
- ArtifactStore serialization/deserialization round-trip
- load_artifact_store() — file exists, missing, corrupted, invalid JSON
- save_artifact_store() — creates directory, writes valid JSON, atomic write
- store_task_artifact() — text, JSON, and file artifact types
- load_task_artifacts() — filter by task_id, missing task returns empty
- load_upstream_artifacts() — dependency-aware loading, no deps, partial deps
- list_artifacts() — all artifacts, empty store
- clear_task_artifacts() — clear specific task, others remain
- format_upstream_artifacts() — empty list, grouping, truncation at max_chars,
  truncation at line boundary
- Edge cases — large content, special characters, multiple artifacts per task
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from the_architect.core.artifacts import (
    _ARTIFACT_INJECTION_MAX_CHARS,
    ARTIFACTS_FILE,
    Artifact,
    ArtifactStore,
    _now_iso,
    _store_path,
    clear_task_artifacts,
    format_upstream_artifacts,
    list_artifacts,
    load_artifact_store,
    load_task_artifacts,
    load_upstream_artifacts,
    save_artifact_store,
    store_task_artifact,
)
from the_architect.core.tasks import Task

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _make_task(prefix: str, depends_on: list[str] | None = None) -> Task:
    """Create a minimal Task for testing."""
    return Task(
        name=f"{prefix}_test",
        prefix=prefix,
        number=int(prefix[1:]),
        path=Path(f"/tmp/{prefix}.md"),
        depends_on=depends_on or [],
    )


def _write_raw_store(project: Path, artifacts: list[dict]) -> None:
    """Write a raw artifacts JSON file directly to disk."""
    store_path = project / ARTIFACTS_FILE
    store_path.parent.mkdir(parents=True, exist_ok=True)
    store_path.write_text(
        json.dumps({"artifacts": artifacts}),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Artifact model
# ---------------------------------------------------------------------------


class TestArtifactModel:
    """Tests for the Artifact Pydantic model."""

    def test_create_with_all_fields(self) -> None:
        artifact = Artifact(
            task_id="T01",
            name="schema",
            content='{"field": "value"}',
            artifact_type="json",
            created_at="2026-05-20T10:00:00+00:00",
        )
        assert artifact.task_id == "T01"
        assert artifact.name == "schema"
        assert artifact.content == '{"field": "value"}'
        assert artifact.artifact_type == "json"
        assert artifact.created_at == "2026-05-20T10:00:00+00:00"

    def test_default_artifact_type_is_text(self) -> None:
        artifact = Artifact(
            task_id="T02",
            name="notes",
            content="some notes",
            created_at="2026-05-20T10:00:00+00:00",
        )
        assert artifact.artifact_type == "text"

    def test_model_dump_roundtrip(self) -> None:
        original = Artifact(
            task_id="T03",
            name="output",
            content="result data",
            artifact_type="file",
            created_at="2026-05-20T10:00:00+00:00",
        )
        dump = original.model_dump()
        restored = Artifact.model_validate(dump)
        assert restored.task_id == original.task_id
        assert restored.name == original.name
        assert restored.content == original.content
        assert restored.artifact_type == original.artifact_type
        assert restored.created_at == original.created_at

    def test_content_can_be_multiline(self) -> None:
        content = "line one\nline two\nline three"
        artifact = Artifact(
            task_id="T01",
            name="multiline",
            content=content,
            created_at="2026-05-20T10:00:00+00:00",
        )
        assert artifact.content == content

    def test_content_can_contain_special_characters(self) -> None:
        content = 'tabs\there\n"quotes" and <html> & symbols'
        artifact = Artifact(
            task_id="T01",
            name="special",
            content=content,
            created_at="2026-05-20T10:00:00+00:00",
        )
        assert artifact.content == content

    def test_empty_content_allowed(self) -> None:
        artifact = Artifact(
            task_id="T01",
            name="empty",
            content="",
            created_at="2026-05-20T10:00:00+00:00",
        )
        assert artifact.content == ""


# ---------------------------------------------------------------------------
# ArtifactStore model
# ---------------------------------------------------------------------------


class TestArtifactStoreModel:
    """Tests for the ArtifactStore Pydantic model."""

    def test_default_empty_store(self) -> None:
        store = ArtifactStore()
        assert store.artifacts == []

    def test_store_with_artifacts(self) -> None:
        arts = [
            Artifact(
                task_id="T01",
                name="a",
                content="c1",
                created_at="2026-05-20T10:00:00+00:00",
            ),
            Artifact(
                task_id="T02",
                name="b",
                content="c2",
                created_at="2026-05-20T11:00:00+00:00",
            ),
        ]
        store = ArtifactStore(artifacts=arts)
        assert len(store.artifacts) == 2

    def test_model_dump_roundtrip(self) -> None:
        arts = [
            Artifact(
                task_id="T01",
                name="x",
                content="y",
                artifact_type="json",
                created_at="2026-05-20T10:00:00+00:00",
            )
        ]
        store = ArtifactStore(artifacts=arts)
        dump = store.model_dump()
        assert dump == {
            "artifacts": [
                {
                    "task_id": "T01",
                    "name": "x",
                    "content": "y",
                    "artifact_type": "json",
                    "created_at": "2026-05-20T10:00:00+00:00",
                }
            ]
        }
        restored = ArtifactStore.model_validate(dump)
        assert len(restored.artifacts) == 1
        assert restored.artifacts[0].task_id == "T01"

    def test_model_validate_from_empty_dict(self) -> None:
        """Validating with {'artifacts': []} produces an empty store."""
        store = ArtifactStore.model_validate({"artifacts": []})
        assert store.artifacts == []

    def test_mixed_artifact_types(self) -> None:
        arts = [
            Artifact(
                task_id="T01",
                name="text_art",
                content="plain text",
                artifact_type="text",
                created_at="2026-05-20T10:00:00+00:00",
            ),
            Artifact(
                task_id="T01",
                name="json_art",
                content='{"key": "val"}',
                artifact_type="json",
                created_at="2026-05-20T10:00:00+00:00",
            ),
            Artifact(
                task_id="T01",
                name="file_art",
                content="/path/to/file.py",
                artifact_type="file",
                created_at="2026-05-20T10:00:00+00:00",
            ),
        ]
        store = ArtifactStore(artifacts=arts)
        assert len(store.artifacts) == 3
        types = [a.artifact_type for a in store.artifacts]
        assert set(types) == {"text", "json", "file"}


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


class TestHelpers:
    """Tests for internal helper functions."""

    def test_now_iso_returns_iso_string(self) -> None:
        result = _now_iso()
        # Should parse as valid ISO datetime with timezone
        dt = datetime.fromisoformat(result)
        assert dt.tzinfo is not None

    def test_now_iso_contains_timezone_info(self) -> None:
        result = _now_iso()
        # UTC timestamps end with +00:00
        assert "+00:00" in result or "Z" in result

    def test_store_path_returns_correct_path(self, tmp_path: Path) -> None:
        result = _store_path(tmp_path)
        assert result == tmp_path / ARTIFACTS_FILE

    def test_store_path_creates_nested_path(self, tmp_path: Path) -> None:
        result = _store_path(tmp_path)
        assert result.parent == tmp_path / ".architect" / "artifacts"


# ---------------------------------------------------------------------------
# load_artifact_store()
# ---------------------------------------------------------------------------


class TestLoadArtifactStore:
    """Tests for load_artifact_store()."""

    def test_returns_empty_store_when_file_missing(self, tmp_path: Path) -> None:
        store = load_artifact_store(tmp_path)
        assert isinstance(store, ArtifactStore)
        assert store.artifacts == []

    def test_returns_empty_store_when_dir_missing(self, tmp_path: Path) -> None:
        assert not (tmp_path / ".architect").exists()
        store = load_artifact_store(tmp_path)
        assert store.artifacts == []

    def test_loads_valid_store(self, tmp_path: Path) -> None:
        _write_raw_store(
            tmp_path,
            [
                {
                    "task_id": "T01",
                    "name": "schema",
                    "content": "user_id: int",
                    "artifact_type": "text",
                    "created_at": "2026-05-20T10:00:00+00:00",
                }
            ],
        )
        store = load_artifact_store(tmp_path)
        assert len(store.artifacts) == 1
        assert store.artifacts[0].task_id == "T01"
        assert store.artifacts[0].name == "schema"

    def test_loads_multiple_artifacts(self, tmp_path: Path) -> None:
        _write_raw_store(
            tmp_path,
            [
                {
                    "task_id": "T01",
                    "name": "a1",
                    "content": "c1",
                    "created_at": "2026-05-20T10:00:00+00:00",
                },
                {
                    "task_id": "T02",
                    "name": "a2",
                    "content": "c2",
                    "created_at": "2026-05-20T11:00:00+00:00",
                },
            ],
        )
        store = load_artifact_store(tmp_path)
        assert len(store.artifacts) == 2

    def test_returns_empty_store_on_invalid_json(self, tmp_path: Path) -> None:
        store_path = tmp_path / ARTIFACTS_FILE
        store_path.parent.mkdir(parents=True, exist_ok=True)
        store_path.write_text("not valid json {{{{", encoding="utf-8")
        store = load_artifact_store(tmp_path)
        assert store.artifacts == []

    def test_returns_empty_store_on_corrupted_data(self, tmp_path: Path) -> None:
        store_path = tmp_path / ARTIFACTS_FILE
        store_path.parent.mkdir(parents=True, exist_ok=True)
        store_path.write_text("CORRUPTED DATA", encoding="utf-8")
        store = load_artifact_store(tmp_path)
        assert store.artifacts == []

    def test_returns_empty_store_on_empty_file(self, tmp_path: Path) -> None:
        store_path = tmp_path / ARTIFACTS_FILE
        store_path.parent.mkdir(parents=True, exist_ok=True)
        store_path.write_text("", encoding="utf-8")
        store = load_artifact_store(tmp_path)
        assert store.artifacts == []

    def test_returns_empty_store_on_os_error(self, tmp_path: Path) -> None:
        store_path = tmp_path / ARTIFACTS_FILE
        store_path.parent.mkdir(parents=True, exist_ok=True)
        store_path.write_text("{}", encoding="utf-8")
        with patch.object(Path, "read_text", side_effect=OSError("permission denied")):
            store = load_artifact_store(tmp_path)
        assert store.artifacts == []

    def test_returns_empty_store_on_validation_error(self, tmp_path: Path) -> None:
        """JSON that is valid but fails Pydantic validation returns empty store."""
        _write_raw_store(
            tmp_path,
            [
                {
                    "task_id": "T01",
                    "name": "x",
                    "content": "y",
                    # missing required created_at field
                }
            ],
        )
        store = load_artifact_store(tmp_path)
        assert store.artifacts == []


# ---------------------------------------------------------------------------
# save_artifact_store()
# ---------------------------------------------------------------------------


class TestSaveArtifactStore:
    """Tests for save_artifact_store()."""

    def test_creates_directory_if_missing(self, tmp_path: Path) -> None:
        assert not (tmp_path / ".architect").exists()
        store = ArtifactStore()
        save_artifact_store(tmp_path, store)
        assert (tmp_path / ".architect" / "artifacts").exists()

    def test_writes_valid_json(self, tmp_path: Path) -> None:
        store = ArtifactStore(
            artifacts=[
                Artifact(
                    task_id="T01",
                    name="test",
                    content="data",
                    created_at="2026-05-20T10:00:00+00:00",
                )
            ]
        )
        save_artifact_store(tmp_path, store)
        raw = (tmp_path / ARTIFACTS_FILE).read_text(encoding="utf-8")
        data = json.loads(raw)
        assert "artifacts" in data
        assert len(data["artifacts"]) == 1
        assert data["artifacts"][0]["task_id"] == "T01"

    def test_atomic_write_no_temp_files_left(self, tmp_path: Path) -> None:
        store = ArtifactStore()
        save_artifact_store(tmp_path, store)
        temp_files = list((tmp_path / ".architect" / "artifacts").glob(".artifacts_tmp_*"))
        assert temp_files == []

    def test_empty_store_saves_cleanly(self, tmp_path: Path) -> None:
        store = ArtifactStore()
        save_artifact_store(tmp_path, store)
        raw = (tmp_path / ARTIFACTS_FILE).read_text(encoding="utf-8")
        data = json.loads(raw)
        assert data == {"artifacts": []}

    def test_overwrites_existing_store(self, tmp_path: Path) -> None:
        first = ArtifactStore(
            artifacts=[
                Artifact(
                    task_id="T01",
                    name="first",
                    content="c1",
                    created_at="2026-05-20T10:00:00+00:00",
                )
            ]
        )
        save_artifact_store(tmp_path, first)
        second = ArtifactStore(
            artifacts=[
                Artifact(
                    task_id="T02",
                    name="second",
                    content="c2",
                    created_at="2026-05-20T11:00:00+00:00",
                )
            ]
        )
        save_artifact_store(tmp_path, second)
        loaded = load_artifact_store(tmp_path)
        assert len(loaded.artifacts) == 1
        assert loaded.artifacts[0].task_id == "T02"


# ---------------------------------------------------------------------------
# store_task_artifact()
# ---------------------------------------------------------------------------


class TestStoreTaskArtifact:
    """Tests for store_task_artifact()."""

    def test_stores_text_artifact(self, tmp_path: Path) -> None:
        result = store_task_artifact(tmp_path, "T01", "notes", "some notes")
        assert result.task_id == "T01"
        assert result.name == "notes"
        assert result.content == "some notes"
        assert result.artifact_type == "text"
        # Verify persisted
        loaded = load_task_artifacts(tmp_path, "T01")
        assert len(loaded) == 1
        assert loaded[0].name == "notes"

    def test_stores_json_artifact(self, tmp_path: Path) -> None:
        result = store_task_artifact(
            tmp_path, "T01", "schema", '{"field": "val"}', artifact_type="json"
        )
        assert result.artifact_type == "json"
        loaded = load_task_artifacts(tmp_path, "T01")
        assert loaded[0].artifact_type == "json"

    def test_stores_file_artifact(self, tmp_path: Path) -> None:
        result = store_task_artifact(
            tmp_path, "T01", "output", "/path/to/file.py", artifact_type="file"
        )
        assert result.artifact_type == "file"

    def test_sets_iso_timestamp(self, tmp_path: Path) -> None:
        result = store_task_artifact(tmp_path, "T01", "test", "data")
        dt = datetime.fromisoformat(result.created_at)
        assert dt.tzinfo is not None

    def test_appends_to_existing_store(self, tmp_path: Path) -> None:
        store_task_artifact(tmp_path, "T01", "first", "c1")
        store_task_artifact(tmp_path, "T01", "second", "c2")
        loaded = load_task_artifacts(tmp_path, "T01")
        assert len(loaded) == 2

    def test_multiple_tasks_share_store(self, tmp_path: Path) -> None:
        store_task_artifact(tmp_path, "T01", "a", "c1")
        store_task_artifact(tmp_path, "T02", "b", "c2")
        all_arts = list_artifacts(tmp_path)
        assert len(all_arts) == 2
        task_ids = {a.task_id for a in all_arts}
        assert task_ids == {"T01", "T02"}


# ---------------------------------------------------------------------------
# load_task_artifacts()
# ---------------------------------------------------------------------------


class TestLoadTaskArtifacts:
    """Tests for load_task_artifacts()."""

    def test_loads_artifacts_for_task(self, tmp_path: Path) -> None:
        store_task_artifact(tmp_path, "T01", "a", "c1")
        store_task_artifact(tmp_path, "T01", "b", "c2")
        result = load_task_artifacts(tmp_path, "T01")
        assert len(result) == 2
        assert all(a.task_id == "T01" for a in result)

    def test_returns_empty_for_missing_task(self, tmp_path: Path) -> None:
        store_task_artifact(tmp_path, "T01", "a", "c1")
        result = load_task_artifacts(tmp_path, "T99")
        assert result == []

    def test_returns_empty_for_empty_store(self, tmp_path: Path) -> None:
        result = load_task_artifacts(tmp_path, "T01")
        assert result == []

    def test_filters_correctly_across_tasks(self, tmp_path: Path) -> None:
        store_task_artifact(tmp_path, "T01", "a", "c1")
        store_task_artifact(tmp_path, "T02", "b", "c2")
        store_task_artifact(tmp_path, "T01", "c", "c3")
        t01 = load_task_artifacts(tmp_path, "T01")
        t02 = load_task_artifacts(tmp_path, "T02")
        assert len(t01) == 2
        assert len(t02) == 1


# ---------------------------------------------------------------------------
# load_upstream_artifacts()
# ---------------------------------------------------------------------------


class TestLoadUpstreamArtifacts:
    """Tests for load_upstream_artifacts()."""

    def test_returns_artifacts_from_dependencies(self, tmp_path: Path) -> None:
        store_task_artifact(tmp_path, "T01", "schema", "user_id: int")
        store_task_artifact(tmp_path, "T02", "config", "port: 8080")
        all_tasks = [
            _make_task("T01"),
            _make_task("T02"),
            _make_task("T03", depends_on=["T01", "T02"]),
        ]
        result = load_upstream_artifacts(tmp_path, "T03", all_tasks)
        assert len(result) == 2
        task_ids = {a.task_id for a in result}
        assert task_ids == {"T01", "T02"}

    def test_returns_empty_when_no_deps(self, tmp_path: Path) -> None:
        store_task_artifact(tmp_path, "T01", "a", "c1")
        all_tasks = [_make_task("T02")]  # T02 has no depends_on
        result = load_upstream_artifacts(tmp_path, "T02", all_tasks)
        assert result == []

    def test_returns_empty_when_task_not_found(self, tmp_path: Path) -> None:
        all_tasks = [_make_task("T01")]
        result = load_upstream_artifacts(tmp_path, "T99", all_tasks)
        assert result == []

    def test_returns_empty_when_deps_have_no_artifacts(self, tmp_path: Path) -> None:
        all_tasks = [
            _make_task("T01"),
            _make_task("T02", depends_on=["T01"]),
        ]
        result = load_upstream_artifacts(tmp_path, "T02", all_tasks)
        assert result == []

    def test_partial_deps_works(self, tmp_path: Path) -> None:
        """Only T01 has artifacts, T02 doesn't — still returns T01's artifacts."""
        store_task_artifact(tmp_path, "T01", "a", "c1")
        # T02 has no artifacts
        all_tasks = [
            _make_task("T01"),
            _make_task("T02"),
            _make_task("T03", depends_on=["T01", "T02"]),
        ]
        result = load_upstream_artifacts(tmp_path, "T03", all_tasks)
        assert len(result) == 1
        assert result[0].task_id == "T01"

    def test_results_sorted_by_task_id_then_name(self, tmp_path: Path) -> None:
        store_task_artifact(tmp_path, "T02", "beta", "c2")
        store_task_artifact(tmp_path, "T01", "alpha", "c1")
        store_task_artifact(tmp_path, "T02", "alpha", "c3")
        all_tasks = [
            _make_task("T01"),
            _make_task("T02"),
            _make_task("T03", depends_on=["T01", "T02"]),
        ]
        result = load_upstream_artifacts(tmp_path, "T03", all_tasks)
        assert len(result) == 3
        # Expected order: T01/alpha, T02/alpha, T02/beta
        assert result[0].task_id == "T01"
        assert result[0].name == "alpha"
        assert result[1].task_id == "T02"
        assert result[1].name == "alpha"
        assert result[2].task_id == "T02"
        assert result[2].name == "beta"

    def test_does_not_include_own_artifacts(self, tmp_path: Path) -> None:
        """T02's own artifacts should not appear in its upstream load."""
        store_task_artifact(tmp_path, "T01", "a", "c1")
        store_task_artifact(tmp_path, "T02", "b", "c2")
        all_tasks = [
            _make_task("T01"),
            _make_task("T02", depends_on=["T01"]),
        ]
        result = load_upstream_artifacts(tmp_path, "T02", all_tasks)
        assert len(result) == 1
        assert result[0].task_id == "T01"

    def test_empty_task_list_returns_empty(self, tmp_path: Path) -> None:
        result = load_upstream_artifacts(tmp_path, "T01", [])
        assert result == []


# ---------------------------------------------------------------------------
# list_artifacts()
# ---------------------------------------------------------------------------


class TestListArtifacts:
    """Tests for list_artifacts()."""

    def test_returns_all_artifacts(self, tmp_path: Path) -> None:
        store_task_artifact(tmp_path, "T01", "a", "c1")
        store_task_artifact(tmp_path, "T02", "b", "c2")
        result = list_artifacts(tmp_path)
        assert len(result) == 2

    def test_returns_empty_for_missing_store(self, tmp_path: Path) -> None:
        result = list_artifacts(tmp_path)
        assert result == []

    def test_returns_copy_of_artifacts(self, tmp_path: Path) -> None:
        """Modifying the returned list should not affect the store."""
        store_task_artifact(tmp_path, "T01", "a", "c1")
        result = list_artifacts(tmp_path)
        result.clear()
        assert len(list_artifacts(tmp_path)) == 1


# ---------------------------------------------------------------------------
# clear_task_artifacts()
# ---------------------------------------------------------------------------


class TestClearTaskArtifacts:
    """Tests for clear_task_artifacts()."""

    def test_clears_specific_task(self, tmp_path: Path) -> None:
        store_task_artifact(tmp_path, "T01", "a", "c1")
        store_task_artifact(tmp_path, "T02", "b", "c2")
        clear_task_artifacts(tmp_path, "T01")
        assert load_task_artifacts(tmp_path, "T01") == []
        assert len(load_task_artifacts(tmp_path, "T02")) == 1

    def test_clears_all_artifacts_for_task(self, tmp_path: Path) -> None:
        store_task_artifact(tmp_path, "T01", "a1", "c1")
        store_task_artifact(tmp_path, "T01", "a2", "c2")
        clear_task_artifacts(tmp_path, "T01")
        assert load_task_artifacts(tmp_path, "T01") == []
        assert list_artifacts(tmp_path) == []

    def test_no_error_when_no_artifacts(self, tmp_path: Path) -> None:
        clear_task_artifacts(tmp_path, "T99")  # should not raise

    def test_no_error_when_store_missing(self, tmp_path: Path) -> None:
        clear_task_artifacts(tmp_path, "T01")  # should not raise

    def test_preserves_other_tasks(self, tmp_path: Path) -> None:
        store_task_artifact(tmp_path, "T01", "a", "c1")
        store_task_artifact(tmp_path, "T02", "b", "c2")
        store_task_artifact(tmp_path, "T03", "c", "c3")
        clear_task_artifacts(tmp_path, "T02")
        remaining = list_artifacts(tmp_path)
        assert len(remaining) == 2
        task_ids = {a.task_id for a in remaining}
        assert task_ids == {"T01", "T03"}


# ---------------------------------------------------------------------------
# format_upstream_artifacts()
# ---------------------------------------------------------------------------


class TestFormatUpstreamArtifacts:
    """Tests for format_upstream_artifacts()."""

    def test_empty_list_returns_empty_string(self) -> None:
        result = format_upstream_artifacts([])
        assert result == ""

    def test_formats_single_artifact(self) -> None:
        arts = [
            Artifact(
                task_id="T01",
                name="schema",
                content="user_id: int",
                artifact_type="text",
                created_at="2026-05-20T10:00:00+00:00",
            )
        ]
        result = format_upstream_artifacts(arts)
        assert "=== UPSTREAM ARTIFACTS ===" in result
        assert "--- Artifacts from T01 ---" in result
        assert "Name: schema" in result
        assert "Type: text" in result
        assert "Content:" in result
        assert "user_id: int" in result

    def test_groups_by_task_id(self) -> None:
        arts = [
            Artifact(
                task_id="T01",
                name="a",
                content="c1",
                created_at="2026-05-20T10:00:00+00:00",
            ),
            Artifact(
                task_id="T02",
                name="b",
                content="c2",
                created_at="2026-05-20T11:00:00+00:00",
            ),
            Artifact(
                task_id="T01",
                name="c",
                content="c3",
                created_at="2026-05-20T10:30:00+00:00",
            ),
        ]
        result = format_upstream_artifacts(arts)
        assert "--- Artifacts from T01 ---" in result
        assert "--- Artifacts from T02 ---" in result
        # T01 section should appear before T02 (sorted)
        t01_pos = result.index("--- Artifacts from T01 ---")
        t02_pos = result.index("--- Artifacts from T02 ---")
        assert t01_pos < t02_pos

    def test_multiple_artifacts_per_task(self) -> None:
        arts = [
            Artifact(
                task_id="T01",
                name="first",
                content="c1",
                created_at="2026-05-20T10:00:00+00:00",
            ),
            Artifact(
                task_id="T01",
                name="second",
                content="c2",
                created_at="2026-05-20T10:30:00+00:00",
            ),
        ]
        result = format_upstream_artifacts(arts)
        assert "Name: first" in result
        assert "Name: second" in result
        # Should only have one T01 section
        assert result.count("--- Artifacts from T01 ---") == 1

    def test_truncation_at_max_chars(self) -> None:
        """Content exceeding max_chars triggers truncation with warning."""
        big_content = "x" * 5000
        arts = [
            Artifact(
                task_id="T01",
                name="big1",
                content=big_content,
                created_at="2026-05-20T10:00:00+00:00",
            ),
            Artifact(
                task_id="T01",
                name="big2",
                content=big_content,
                created_at="2026-05-20T10:30:00+00:00",
            ),
        ]
        result = format_upstream_artifacts(arts, max_chars=1000)
        assert "[WARNING: Artifact content truncated" in result
        assert len(result) <= 1000 + 80  # max_chars + warning text

    def test_truncation_at_line_boundary(self) -> None:
        """Truncation cuts at the last newline, not mid-line."""
        lines_content = "\n".join([f"line{i}" for i in range(200)])
        arts = [
            Artifact(
                task_id="T01",
                name="lines",
                content=lines_content,
                created_at="2026-05-20T10:00:00+00:00",
            )
        ]
        result = format_upstream_artifacts(arts, max_chars=500)
        # After truncation, the last line before the warning should be complete
        if "[WARNING:" in result:
            before_warning = result.split("[WARNING:")[0]
            # The truncated text should not end mid-line (should end with \n or be clean)
            assert before_warning.endswith("\n") or "\n" not in before_warning.split("\n")[-1]

    def test_custom_max_chars(self) -> None:
        """A very large max_chars should not truncate."""
        arts = [
            Artifact(
                task_id="T01",
                name="small",
                content="hello",
                created_at="2026-05-20T10:00:00+00:00",
            )
        ]
        result = format_upstream_artifacts(arts, max_chars=100000)
        assert "[WARNING:" not in result
        assert "hello" in result

    def test_default_max_chars_is_10000(self) -> None:
        assert _ARTIFACT_INJECTION_MAX_CHARS == 10_000

    def test_includes_header_and_description(self) -> None:
        arts = [
            Artifact(
                task_id="T01",
                name="test",
                content="data",
                created_at="2026-05-20T10:00:00+00:00",
            )
        ]
        result = format_upstream_artifacts(arts)
        assert "=== UPSTREAM ARTIFACTS ===" in result
        assert "depends on" in result
        assert "context" in result

    def test_artifact_type_displayed(self) -> None:
        arts = [
            Artifact(
                task_id="T01",
                name="json_data",
                content='{"k": "v"}',
                artifact_type="json",
                created_at="2026-05-20T10:00:00+00:00",
            )
        ]
        result = format_upstream_artifacts(arts)
        assert "Type: json" in result


# ---------------------------------------------------------------------------
# Edge cases and integration
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Edge-case and integration tests for the artifacts module."""

    def test_large_content_roundtrip(self, tmp_path: Path) -> None:
        """Very large artifact content can be stored and loaded."""
        big = "x" * 100_000
        store_task_artifact(tmp_path, "T01", "huge", big)
        loaded = load_task_artifacts(tmp_path, "T01")
        assert len(loaded) == 1
        assert loaded[0].content == big

    def test_unicode_content(self, tmp_path: Path) -> None:
        """Unicode content survives round-trip."""
        content = "日本語テスト 🚀 émojis 中文"
        store_task_artifact(tmp_path, "T01", "unicode", content)
        loaded = load_task_artifacts(tmp_path, "T01")
        assert loaded[0].content == content

    def test_content_with_newlines_preserved(self, tmp_path: Path) -> None:
        """Multiline content preserves newlines through store/load."""
        content = "line1\nline2\nline3\n"
        store_task_artifact(tmp_path, "T01", "multiline", content)
        loaded = load_task_artifacts(tmp_path, "T01")
        assert loaded[0].content == content

    def test_artifact_store_and_load_roundtrip(self, tmp_path: Path) -> None:
        """Full roundtrip: store -> load -> verify."""
        store_task_artifact(tmp_path, "T01", "schema", '{"id": 1}', "json")
        store_task_artifact(tmp_path, "T02", "config", "port=8080", "text")

        all_arts = list_artifacts(tmp_path)
        assert len(all_arts) == 2

        t01_arts = load_task_artifacts(tmp_path, "T01")
        assert len(t01_arts) == 1
        assert t01_arts[0].artifact_type == "json"

        t02_arts = load_task_artifacts(tmp_path, "T02")
        assert len(t02_arts) == 1
        assert t02_arts[0].artifact_type == "text"

    def test_clear_then_store_recreates(self, tmp_path: Path) -> None:
        """After clearing, storing a new artifact works."""
        store_task_artifact(tmp_path, "T01", "a", "c1")
        clear_task_artifacts(tmp_path, "T01")
        store_task_artifact(tmp_path, "T01", "b", "c2")
        loaded = load_task_artifacts(tmp_path, "T01")
        assert len(loaded) == 1
        assert loaded[0].name == "b"

    def test_format_with_no_artifacts_returns_empty(self) -> None:
        """format_upstream_artifacts([]) returns empty string."""
        assert format_upstream_artifacts([]) == ""

    def test_concurrent_style_access(self, tmp_path: Path) -> None:
        """Simulate rapid store/load/clear operations."""
        for i in range(5):
            store_task_artifact(tmp_path, "T01", f"iter{i}", f"data{i}")
        arts = list_artifacts(tmp_path)
        assert len(arts) == 5
        clear_task_artifacts(tmp_path, "T01")
        assert len(list_artifacts(tmp_path)) == 0

    def test_store_path_is_relative(self, tmp_path: Path) -> None:
        """The store path is always relative to the project root."""
        path = _store_path(tmp_path)
        assert path.is_relative_to(tmp_path)

    def test_artifacts_from_non_dep_tasks_not_injected(self, tmp_path: Path) -> None:
        """Artifacts from tasks NOT in depends_on are excluded."""
        store_task_artifact(tmp_path, "T01", "a", "c1")
        store_task_artifact(tmp_path, "T02", "b", "c2")
        store_task_artifact(tmp_path, "T03", "c", "c3")
        # T04 only depends on T01 — should NOT see T02 or T03 artifacts
        all_tasks = [
            _make_task("T01"),
            _make_task("T02"),
            _make_task("T03"),
            _make_task("T04", depends_on=["T01"]),
        ]
        result = load_upstream_artifacts(tmp_path, "T04", all_tasks)
        assert len(result) == 1
        assert result[0].task_id == "T01"

    def test_multiple_artifacts_same_task_formatted(self) -> None:
        """Multiple artifacts from the same task are all shown."""
        arts = [
            Artifact(
                task_id="T01",
                name="first",
                content="content1",
                created_at="2026-05-20T10:00:00+00:00",
            ),
            Artifact(
                task_id="T01",
                name="second",
                content="content2",
                created_at="2026-05-20T10:30:00+00:00",
            ),
        ]
        result = format_upstream_artifacts(arts)
        assert result.count("Name: first") == 1
        assert result.count("Name: second") == 1
        assert result.count("content1") == 1
        assert result.count("content2") == 1

    def test_truncation_warning_text(self) -> None:
        """Truncation warning has the expected text."""
        big = "x" * 20000
        arts = [
            Artifact(
                task_id="T01",
                name="big",
                content=big,
                created_at="2026-05-20T10:00:00+00:00",
            )
        ]
        result = format_upstream_artifacts(arts, max_chars=100)
        assert "exceeded maximum size limit" in result

    def test_artifact_store_model_validate_empty_artifacts_key(self) -> None:
        """ArtifactStore validates with an empty artifacts list."""
        store = ArtifactStore.model_validate({"artifacts": []})
        assert store.artifacts == []
