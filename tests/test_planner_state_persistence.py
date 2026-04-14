"""Tests for cross-planner isolation in planner_state.json persistence.

These tests guard against a class of bugs where saving one planner's
state silently destroys the other planner's state in the same file.
The read-modify-write path has to be robust against:

  * interleaved saves from both planners (normal operation)
  * transient read failures when the file exists but is momentarily
    unreadable (must not overwrite with a fresh blob)
  * interrupted/truncated writes (atomic rename prevents this from
    ever being observed by a subsequent read)
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

_proj_root = Path(__file__).resolve().parents[1]
_src_dir = _proj_root / "src"
sys.path.insert(0, str(_src_dir))
sys.path.insert(0, str(_proj_root))

from mewgenics.utils import planner_state as ps
from mewgenics.utils.paths import _planner_state_path


@pytest.fixture
def save_path(tmp_path):
    return str(tmp_path / "fake.sav")


def _read_raw(save_path: str) -> dict:
    with open(_planner_state_path(save_path), "r", encoding="utf-8") as f:
        return json.load(f)


class TestCrossPlannerIsolation:
    def test_save_preserves_other_planner_state(self, save_path):
        """Writing one planner's state must not clobber the other's."""
        mutation_state = {"selected_mode": "melee", "selected_traits": [{"k": "amped"}]}
        perfect_state = {"min_stats": "35", "starter_pairs": 4}

        ps._save_planner_state_value("mutation_planner_state", mutation_state, save_path)
        ps._save_planner_state_value("perfect_planner_state", perfect_state, save_path)

        blob = _read_raw(save_path)
        assert blob["mutation_planner_state"] == mutation_state
        assert blob["perfect_planner_state"] == perfect_state

    def test_interleaved_saves_preserve_both(self, save_path):
        """Repeated interleaved saves must not lose either planner's data."""
        for i in range(5):
            ps._save_planner_state_value(
                "mutation_planner_state", {"iter": i, "tag": "mut"}, save_path
            )
            ps._save_planner_state_value(
                "perfect_planner_state", {"iter": i, "tag": "perf"}, save_path
            )
        blob = _read_raw(save_path)
        assert blob["mutation_planner_state"] == {"iter": 4, "tag": "mut"}
        assert blob["perfect_planner_state"] == {"iter": 4, "tag": "perf"}

    def test_load_returns_correct_planner_key(self, save_path):
        ps._save_planner_state_value("mutation_planner_state", {"k": "m"}, save_path)
        ps._save_planner_state_value("perfect_planner_state", {"k": "p"}, save_path)

        assert ps._load_planner_state_value(
            "mutation_planner_state", {}, save_path
        ) == {"k": "m"}
        assert ps._load_planner_state_value(
            "perfect_planner_state", {}, save_path
        ) == {"k": "p"}


class TestRoomPriorityConfigIsPerSave:
    """Regression guard for issue 68: room priority config used to bleed
    between saves because it was globally mirrored. It must now live only
    in the per-save sidecar."""

    def test_room_priority_config_is_not_globally_mirrored(self):
        assert "room_priority_config" not in ps._PLANNER_STATE_GLOBAL_MIRROR_KEYS

    def test_per_save_config_does_not_leak_to_other_save(self, tmp_path):
        save_a = str(tmp_path / "a.sav")
        save_b = str(tmp_path / "b.sav")

        config_a = [{"room": "Floor1_Large", "type": "best_pairs", "max_cats": 4}]
        config_b = [{"room": "Floor1_Large", "type": "fallback", "max_cats": 12}]

        ps._save_planner_state_value("room_priority_config", config_a, save_a)
        ps._save_planner_state_value("room_priority_config", config_b, save_b)

        assert ps._load_planner_state_value("room_priority_config", [], save_a) == config_a
        assert ps._load_planner_state_value("room_priority_config", [], save_b) == config_b

    def test_room_priority_config_save_does_not_touch_global_config(self, save_path, monkeypatch):
        """Saving a room_priority_config must only write the sidecar,
        not the global app config (which would leak to other saves)."""
        writes: list[dict] = []

        def fake_save_app_config(data):
            writes.append(dict(data))

        monkeypatch.setattr(ps, "_save_app_config", fake_save_app_config)

        ps._save_planner_state_value(
            "room_priority_config",
            [{"room": "Floor1_Large", "type": "best_pairs"}],
            save_path,
        )
        assert writes == [], (
            "room_priority_config should not touch the global app config — "
            "otherwise it will leak between saves"
        )


class TestCorruptedFileHandling:
    def test_save_refuses_to_clobber_unreadable_file(self, save_path, caplog):
        """If the existing file cannot be parsed, saving must bail out
        rather than overwrite with just the current key."""
        # Write a file with known-good data.
        ps._save_planner_state_value("mutation_planner_state", {"keep": True}, save_path)
        ps._save_planner_state_value("perfect_planner_state", {"keep": True}, save_path)

        # Corrupt the file so it parses as invalid JSON.
        path = _planner_state_path(save_path)
        with open(path, "w", encoding="utf-8") as f:
            f.write("{not valid json")

        # Saving perfect_planner_state must NOT wipe mutation_planner_state.
        ps._save_planner_state_value(
            "perfect_planner_state", {"new": True}, save_path
        )

        # File should still be the corrupted blob (save was refused).
        with open(path, "r", encoding="utf-8") as f:
            raw = f.read()
        assert raw == "{not valid json"

    def test_load_blob_distinguishes_missing_from_corrupted(self, save_path):
        """Missing file → empty dict; corrupted file → raises."""
        # Missing file returns {} without raising.
        assert ps._load_planner_state_blob(save_path) == {}

        # Corrupted file raises the sentinel so _save_planner_state_value
        # knows to refuse a destructive overwrite.
        path = _planner_state_path(save_path)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write("garbage")
        with pytest.raises(ps._PlannerStateReadError):
            ps._load_planner_state_blob(save_path)


class TestAtomicWrite:
    def test_save_is_atomic(self, save_path, tmp_path):
        """After _save_planner_state_blob the target file must contain
        fully-valid JSON — no partial writes ever observable."""
        blob = {
            "mutation_planner_state": {"a": list(range(100))},
            "perfect_planner_state": {"b": "x" * 1000},
        }
        ps._save_planner_state_blob(save_path, blob)

        with open(_planner_state_path(save_path), "r", encoding="utf-8") as f:
            loaded = json.load(f)
        assert loaded == blob

    def test_tmp_files_cleaned_up(self, save_path, tmp_path):
        """Atomic write must not leave stray .tmp files behind."""
        ps._save_planner_state_blob(save_path, {"k": "v"})
        save_dir = Path(_planner_state_path(save_path)).parent
        leftover = [p for p in save_dir.iterdir() if p.name.startswith(".planner_state.")]
        assert leftover == []
