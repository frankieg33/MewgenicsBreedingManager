"""Regression tests for the v5.4.8 auto-refresh crash.

When the game rewrote its save while the manager was open, the manager
crashed roughly 10% of the time.  The fixes in this branch close four
race / exception-handling gaps:

  F1/F6  SaveLoadWorker catches parse failures and retries partial
          writes, emitting `failed` instead of letting the QThread die.
  F3     QuickRoomRefreshWorker tags every signal with a generation
          token; MainWindow._on_room_patch drops stale signals.
  F4     load_save no longer calls QThread.terminate() on in-flight
          SaveLoadWorkers — a superseded worker's result is discarded
          by identity check in _on_save_loaded instead.
  F5     QFileSystemWatcher events are debounced with a 250 ms timer.

These tests verify each fix in isolation without needing a real save.
"""
from __future__ import annotations

import os
import sqlite3
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

_proj_root = Path(__file__).resolve().parents[1]
_src_dir = _proj_root / "src"
sys.path.insert(0, str(_src_dir))
sys.path.insert(0, str(_proj_root))

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication, QTableView

from mewgenics.workers.save_loader import SaveLoadWorker
from mewgenics.workers.room_refresh import QuickRoomRefreshWorker
from mewgenics.utils.retry import retry_transient
import mewgenics.main_window as main_window_module
import mewgenics.workers.save_loader as save_loader_module
import mewgenics.workers.room_refresh as room_refresh_module


@pytest.fixture(scope="module")
def qt_app():
    app = QApplication.instance() or QApplication([])
    return app


def _run_thread_to_completion(thread, timeout_ms: int = 5000):
    """Start a QThread, pump events until it finishes, then join."""
    loop = QEventLoop()
    thread.finished.connect(loop.quit)
    # Safety kill-switch in case the worker hangs.
    killer = QTimer()
    killer.setSingleShot(True)
    killer.timeout.connect(loop.quit)
    killer.start(timeout_ms)
    thread.start()
    loop.exec()
    assert thread.wait(timeout_ms), "worker did not finish in time"


# ─────────────────────────────────────────────────────────────────────
# retry_transient: only retries transient I/O errors, not real bugs
# ─────────────────────────────────────────────────────────────────────


class TestRetryTransient:
    def test_non_transient_exception_propagates_without_retry(self):
        """TypeError / ValueError / AttributeError etc. are real bugs
        or genuinely corrupted data — they must NOT trigger the retry
        loop, which would waste ~350 ms re-running a doomed operation
        (and for parse_save, re-allocate hundreds of MB)."""
        calls = {"n": 0}

        def always_type_error():
            calls["n"] += 1
            raise TypeError("real bug — should not retry")

        with pytest.raises(TypeError):
            retry_transient(always_type_error)

        assert calls["n"] == 1  # called once, never retried

    def test_transient_exception_retries(self):
        calls = {"n": 0}

        def flaky():
            calls["n"] += 1
            if calls["n"] < 3:
                raise sqlite3.OperationalError("database is locked")
            return "ok"

        assert retry_transient(flaky, delays_ms=(1, 1, 1)) == "ok"
        assert calls["n"] == 3


# ─────────────────────────────────────────────────────────────────────
# F1: SaveLoadWorker emits `failed` instead of dying silently
# ─────────────────────────────────────────────────────────────────────


class TestSaveLoadWorkerFailedSignal:
    def test_nonexistent_path_emits_failed_not_finished(self, qt_app, tmp_path):
        """A missing .sav used to let parse_save raise, killing the
        QThread silently.  Now it must emit `failed` and leave
        `finished_load` quiet so MainWindow can recover.
        """
        worker = SaveLoadWorker(str(tmp_path / "nope.sav"))
        finished_results = []
        failed_events = []
        worker.finished_load.connect(finished_results.append)
        worker.failed.connect(lambda msg, transient: failed_events.append((msg, transient)))

        _run_thread_to_completion(worker)

        assert finished_results == []
        assert len(failed_events) == 1
        msg, is_transient = failed_events[0]
        assert msg  # non-empty repr
        # A missing file surfaces as OSError/FileNotFoundError → transient.
        assert is_transient is True

    def test_retry_with_backoff_recovers_from_transient_failure(
        self, qt_app, tmp_path, monkeypatch
    ):
        """Partial writes fail once then succeed.  With 3 retries the
        worker must recover automatically and emit `finished_load`.
        """
        path = tmp_path / "fake.sav"
        path.touch()

        attempts = {"n": 0}

        def flaky_parse(p):
            attempts["n"] += 1
            if attempts["n"] < 2:
                raise sqlite3.OperationalError("database is locked")
            # Pretend a successful parse returned the minimum shape the
            # worker unpacks + the attrs it forwards.
            cats = []
            save = SimpleNamespace(
                accessible_cats=set(),
                furniture=[],
                furniture_by_room={},
                pedigree_coi_memos={},
            )
            # The worker does `cats, errors, unlocked = save` so the
            # top-level return must be a 3-tuple but still expose the
            # extra attrs above.  Use a small shim class.
            class _Shim(tuple):
                def __new__(cls, items, extras):
                    self = super().__new__(cls, items)
                    self.__dict__.update(extras)
                    return self
            return _Shim((cats, [], []), save.__dict__)

        monkeypatch.setattr(save_loader_module, "parse_save", flaky_parse)
        # Stub out the sidecar-file loaders — they'd otherwise try to
        # open JSONs next to the fake save.
        monkeypatch.setattr(save_loader_module, "_load_blacklist", lambda *a, **k: None)
        monkeypatch.setattr(save_loader_module, "_load_must_breed", lambda *a, **k: None)
        monkeypatch.setattr(save_loader_module, "_load_pinned", lambda *a, **k: None)
        monkeypatch.setattr(save_loader_module, "_load_tags", lambda *a, **k: None)
        monkeypatch.setattr(
            save_loader_module, "_load_gender_overrides", lambda *a, **k: (None, [])
        )
        monkeypatch.setattr(
            save_loader_module, "_apply_calibration", lambda *a, **k: (False, None, [])
        )

        worker = SaveLoadWorker(str(path))
        finished = []
        failed = []
        worker.finished_load.connect(finished.append)
        worker.failed.connect(failed.append)

        _run_thread_to_completion(worker)

        assert failed == [], f"worker should have recovered, got failure {failed!r}"
        assert len(finished) == 1
        assert attempts["n"] == 2  # one failed, one succeeded

    def test_non_transient_parse_failure_is_flagged_not_transient(
        self, qt_app, tmp_path, monkeypatch
    ):
        """Corrupt saves / parser bugs raise TypeError/KeyError etc.
        Those must be reported with is_transient=False so the main window
        can skip the self-heal retry timer and avoid a busy-loop.
        """
        path = tmp_path / "fake.sav"
        path.touch()

        def busted_parse(p):
            raise KeyError("missing field — real bug, do not retry")

        monkeypatch.setattr(save_loader_module, "parse_save", busted_parse)

        worker = SaveLoadWorker(str(path))
        failed_events = []
        worker.failed.connect(lambda msg, transient: failed_events.append((msg, transient)))

        _run_thread_to_completion(worker)

        assert len(failed_events) == 1
        _, is_transient = failed_events[0]
        assert is_transient is False


# ─────────────────────────────────────────────────────────────────────
# P1: _on_save_load_failed caps retries so a permanently-broken save
#     cannot spin a self-heal loop forever.
# ─────────────────────────────────────────────────────────────────────


class TestSaveLoadFailureRetryPolicy:
    def _bare_window(self):
        window = main_window_module.MainWindow.__new__(main_window_module.MainWindow)
        window._save_load_worker = None
        window._save_load_retries = 0
        window._loading_overlay = SimpleNamespace(hide=lambda: None)
        window.statusBar = lambda: SimpleNamespace(showMessage=lambda *a, **k: None)
        return window

    def test_non_transient_failure_does_not_schedule_retry(self, qt_app):
        window = self._bare_window()
        scheduled = []
        with patch.object(main_window_module, "QTimer") as qt:
            qt.singleShot = lambda ms, fn: scheduled.append((ms, fn))
            main_window_module.MainWindow._on_save_load_failed(
                window, "KeyError('foo')", False
            )
        assert scheduled == []
        assert window._save_load_retries == 0  # no increment on permanent error

    def test_transient_failure_under_cap_schedules_retry(self, qt_app):
        window = self._bare_window()
        scheduled = []
        with patch.object(main_window_module, "QTimer") as qt:
            qt.singleShot = lambda ms, fn: scheduled.append((ms, fn))
            main_window_module.MainWindow._on_save_load_failed(
                window, "OperationalError('locked')", True
            )
        assert len(scheduled) == 1
        assert scheduled[0][0] == 500
        assert window._save_load_retries == 1

    def test_transient_failure_at_cap_stops_retrying(self, qt_app):
        window = self._bare_window()
        window._save_load_retries = main_window_module.MainWindow._SAVE_LOAD_RETRY_CAP
        scheduled = []
        with patch.object(main_window_module, "QTimer") as qt:
            qt.singleShot = lambda ms, fn: scheduled.append((ms, fn))
            main_window_module.MainWindow._on_save_load_failed(
                window, "OperationalError('locked')", True
            )
        assert scheduled == [], "retry cap must stop the self-heal loop"
        # Cap is sticky until a successful load resets it.
        assert window._save_load_retries == main_window_module.MainWindow._SAVE_LOAD_RETRY_CAP

    def test_superseded_worker_does_not_advance_retry_counter(self, qt_app):
        window = self._bare_window()
        window._save_load_worker = SimpleNamespace(name="current")
        stale = SimpleNamespace(name="stale")
        with patch.object(main_window_module, "QTimer") as qt:
            scheduled = []
            qt.singleShot = lambda ms, fn: scheduled.append((ms, fn))
            main_window_module.MainWindow._on_save_load_failed(
                window, "OperationalError('locked')", True, source_worker=stale
            )
        assert scheduled == []
        assert window._save_load_retries == 0
        assert window._save_load_worker is not None  # current worker untouched


# ─────────────────────────────────────────────────────────────────────
# F6: QuickRoomRefreshWorker retry on transient sqlite failure
# ─────────────────────────────────────────────────────────────────────


class TestQuickRefreshRetry:
    def test_sqlite_locked_once_then_succeeds(self, qt_app, tmp_path, monkeypatch):
        """If sqlite3.connect raises `database is locked` once, the
        worker must retry and recover rather than falling through to
        `needs_full_reload`.
        """
        path = tmp_path / "fake.sav"
        path.touch()

        attempts = {"n": 0}
        real_connect = sqlite3.connect

        class _FakeConn:
            def execute(self, _sql):
                return SimpleNamespace(fetchall=lambda: [(1,), (2,)])

            def close(self):
                pass

        def flaky_connect(*args, **kw):
            attempts["n"] += 1
            if attempts["n"] < 2:
                raise sqlite3.OperationalError("database is locked")
            return _FakeConn()

        monkeypatch.setattr(room_refresh_module.sqlite3, "connect", flaky_connect)
        monkeypatch.setattr(
            room_refresh_module, "_get_house_info", lambda conn: {1: "Attic", 2: "Floor1_Large"}
        )
        monkeypatch.setattr(
            room_refresh_module, "_get_adventure_keys", lambda conn: set()
        )

        worker = QuickRoomRefreshWorker(str(path), expected_keys={1, 2}, generation=7)
        patches = []
        fallbacks = []
        worker.room_patch.connect(lambda gen, patch: patches.append((gen, patch)))
        worker.needs_full_reload.connect(fallbacks.append)

        _run_thread_to_completion(worker)

        assert fallbacks == [], "transient failure should have recovered"
        assert len(patches) == 1
        gen, patch = patches[0]
        assert gen == 7  # generation token round-tripped
        assert patch == {1: ("Attic", "In House"), 2: ("Floor1_Large", "In House")}
        assert attempts["n"] == 2

    def test_exhausted_retries_fall_back_to_full_reload(self, qt_app, tmp_path, monkeypatch):
        path = tmp_path / "fake.sav"
        path.touch()

        def always_fail(uri, **kw):
            raise sqlite3.OperationalError("database is locked")

        monkeypatch.setattr(room_refresh_module.sqlite3, "connect", always_fail)

        worker = QuickRoomRefreshWorker(str(path), expected_keys=set(), generation=3)
        patches = []
        fallbacks = []
        worker.room_patch.connect(lambda gen, patch: patches.append((gen, patch)))
        worker.needs_full_reload.connect(fallbacks.append)

        _run_thread_to_completion(worker)

        assert patches == []
        assert fallbacks == [3]  # generation preserved on fallback too


# ─────────────────────────────────────────────────────────────────────
# F3: MainWindow._on_room_patch drops stale-generation signals
# ─────────────────────────────────────────────────────────────────────


class TestQuickRefreshGenerationGuard:
    """_on_room_patch must ignore signals from superseded workers.

    Reproduces "Vector 2" from the fix plan: a fileChanged burst
    spawns worker A, then worker B before A has finished; both
    eventually emit room_patch.  Only B's patch should be applied.
    """

    def _bare_window(self):
        """Construct a MainWindow stand-in with just the attributes
        `_on_room_patch` touches.  We can't spin up a real MainWindow
        here (it touches GPAKs, locales, config files, …) so we mock
        out everything that isn't on the race-critical path.
        """
        window = main_window_module.MainWindow.__new__(main_window_module.MainWindow)
        window._quick_refresh_generation = 5
        window._quick_refresh_worker = object()  # sentinel
        window._cats = [SimpleNamespace(db_key=1, room="Attic", status="In House")]
        def _apply_room_patch(patch):
            changed = False
            for cat in window._cats:
                entry = patch.get(cat.db_key)
                if entry is None:
                    continue
                room, status = entry
                if cat.room == room and cat.status == status:
                    continue
                cat.room = room
                cat.status = status
                changed = True
            return changed
        # Everything below is only touched in the non-stale branch; we
        # want the stale branch to return before reaching them so these
        # can all be bare stubs / None.
        window._source_model = SimpleNamespace(apply_room_patch=_apply_room_patch)
        window._proxy_model = SimpleNamespace(invalidate=lambda: None)
        window._rebuild_room_buttons = lambda cats: None
        window._refresh_filter_button_counts = lambda: None
        window._bump_cats_generation = lambda: None
        window._furniture_view = None
        window._tree_view = None
        window._safe_breeding_view = None
        window._breeding_partners_view = None
        window._room_optimizer_view = None
        window._perfect_planner_view = None
        window._calibration_view = None
        window._cats_generation = 0
        window._view_generation = {}
        window._furniture = []
        window._furniture_data = None
        window._available_house_rooms = []
        window._current_save = ""
        window.statusBar = lambda: SimpleNamespace(showMessage=lambda *a, **k: None)
        return window

    @staticmethod
    def _make_cat(db_key: int, name: str, room: str = "Attic", status: str = "In House"):
        cat = SimpleNamespace()
        cat.db_key = db_key
        cat.name = name
        cat.status = status
        cat.room = room
        cat.gender_display = "M"
        cat.age = db_key
        cat.base_stats = {stat: 5 for stat in main_window_module.STAT_NAMES}
        cat.total_stats = dict(cat.base_stats)
        cat.is_blacklisted = False
        cat.must_breed = False
        cat.is_pinned = False
        cat.abilities = []
        cat.passive_abilities = []
        cat.passive_tiers = {}
        cat.disorders = []
        cat.mutations = []
        cat.defects = []
        cat.mutation_chip_items = []
        cat.defect_chip_items = []
        cat.lovers = []
        cat.haters = []
        cat.generation = 0
        cat.aggression = 0.1
        cat.libido = 0.2
        cat.inbredness = 0.0
        cat.parsed_inbredness = 0.0
        cat.parent_a = None
        cat.parent_b = None
        cat.sexuality = "bi"
        cat.tags = []
        cat.uid = db_key
        cat.death_day = None
        cat.stat_mods = {}
        cat.has_adventured = False
        return cat

    def test_stale_generation_is_dropped(self, qt_app):
        window = self._bare_window()
        pre_cats = window._cats
        pre_worker = window._quick_refresh_worker

        # Signal from a worker with an older generation — must be dropped
        # without touching state.
        main_window_module.MainWindow._on_room_patch(
            window, 4, {1: ("Floor1_Large", "In House")}
        )

        assert window._cats is pre_cats
        assert window._cats[0].room == "Attic"  # unchanged
        assert window._quick_refresh_worker is pre_worker  # not reset

    def test_current_generation_is_applied(self, qt_app):
        window = self._bare_window()

        main_window_module.MainWindow._on_room_patch(
            window, 5, {1: ("Floor1_Large", "In House")}
        )

        assert window._cats[0].room == "Floor1_Large"
        assert window._quick_refresh_worker is None

    def test_exception_in_body_falls_back_without_crashing(self, qt_app):
        """If something inside the body raises (e.g. a deleted Qt
        object), the slot must swallow the exception and schedule a
        reload rather than aborting the event loop.
        """
        window = self._bare_window()

        # Blow up partway through by making invalidate() throw.
        def boom():
            raise RuntimeError("Internal C++ object already deleted")
        window._proxy_model.invalidate = boom

        reloads = []
        window._reload = lambda: reloads.append(True)
        # Monkeypatch QTimer.singleShot for the fallback so we can assert
        # it was scheduled without actually deferring.
        with patch.object(main_window_module, "QTimer") as qtimer_mock:
            qtimer_mock.singleShot = lambda ms, fn: fn()
            # The try/except body does not crash the process:
            main_window_module.MainWindow._on_room_patch(
                window, 5, {1: ("Floor1_Large", "In House")}
            )

        assert reloads == [True]

    def test_current_generation_emits_layout_change_and_preserves_selection(self, qt_app):
        """A quick room patch applied to an already-sorted roster must
        signal a layout change (not a full reset) so the proxy refilters
        and resorts without dropping the user's current selection or
        crashing on the next header click.
        """
        window = self._bare_window()
        cats = [
            self._make_cat(1, "Alpha", room="Attic"),
            self._make_cat(2, "Bravo", room="Floor1_Large"),
        ]
        window._cats = cats
        window._source_model = main_window_module.CatTableModel()
        window._proxy_model = main_window_module.RoomFilterModel()
        window._proxy_model.setSourceModel(window._source_model)
        window._proxy_model.set_accessible_cats(set())
        window._proxy_model.set_room(None)
        window._table = QTableView()
        window._table.setModel(window._proxy_model)
        window._table.setSortingEnabled(True)
        window._table.sortByColumn(main_window_module.COL_NAME, main_window_module.Qt.AscendingOrder)
        window._source_model.load(cats, accessible_cats=set())
        qt_app.processEvents()

        # Select Alpha (the row that won't be filtered out) so we can
        # confirm selection survives the patch.
        alpha_proxy_index = window._proxy_model.index(0, main_window_module.COL_NAME)
        window._table.selectionModel().setCurrentIndex(
            alpha_proxy_index,
            window._table.selectionModel().SelectionFlag.ClearAndSelect | window._table.selectionModel().SelectionFlag.Rows,
        )
        selected_key_before = cats[window._proxy_model.mapToSource(alpha_proxy_index).row()].db_key

        resets = []
        layouts = []
        window._source_model.modelReset.connect(lambda: resets.append(True))
        window._source_model.layoutChanged.connect(lambda: layouts.append(True))

        main_window_module.MainWindow._on_room_patch(
            window, 5, {2: ("", "Gone")}
        )
        qt_app.processEvents()

        assert resets == []
        assert layouts == [True]
        assert window._proxy_model.rowCount() == 1

        # Selection survived — current index still points at Alpha.
        current = window._table.selectionModel().currentIndex()
        assert current.isValid()
        selected_key_after = cats[window._proxy_model.mapToSource(current).row()].db_key
        assert selected_key_after == selected_key_before

        # Sorting after the patch must not crash.
        window._table.sortByColumn(main_window_module.COL_NAME, main_window_module.Qt.DescendingOrder)
        qt_app.processEvents()
        assert window._proxy_model.rowCount() == 1


# ─────────────────────────────────────────────────────────────────────
# F4: _on_save_loaded drops results from superseded workers
# ─────────────────────────────────────────────────────────────────────


class TestSaveLoadSupersededGuard:
    def test_stale_save_load_result_is_discarded(self, qt_app):
        """A superseded SaveLoadWorker (one replaced by a newer
        load_save call) must have its finished_load result dropped
        instead of overwriting fresh state.
        """
        window = main_window_module.MainWindow.__new__(main_window_module.MainWindow)

        # Simulate state after load_save started a new worker B.
        current_worker = SimpleNamespace(name="B")
        window._save_load_worker = current_worker
        # Tripwires: these must NOT be touched if the stale-worker guard
        # fires correctly.
        window._loading_overlay = SimpleNamespace(
            hide=lambda: pytest.fail("stale result must not hide the overlay")
        )
        window._save_view_disabled = False

        stale_worker = SimpleNamespace(name="A")
        # Deliberately a malformed result — if we got past the guard
        # the test would crash loudly, which is exactly what we want.
        bogus_result = {}

        main_window_module.MainWindow._on_save_loaded(
            window, bogus_result, False, source_worker=stale_worker
        )

        # Current worker reference must still point at B.
        assert window._save_load_worker is current_worker


# ─────────────────────────────────────────────────────────────────────
# F5: file watcher debounces bursts into a single refresh
# ─────────────────────────────────────────────────────────────────────


class TestFileWatcherDebounce:
    def test_burst_events_collapse_to_one_refresh(self, qt_app):
        """Five fileChanged events in quick succession must produce
        exactly one _start_quick_room_refresh call after the debounce
        window closes.
        """
        window = main_window_module.MainWindow.__new__(main_window_module.MainWindow)

        # Minimum attrs needed by _on_file_changed_raw and
        # _on_file_changed_debounced.
        window._current_save = "/fake/save.sav"
        window._watcher = SimpleNamespace(
            files=lambda: ["/fake/save.sav"],  # already watched
            addPath=lambda p: None,
            removePaths=lambda ps: None,
        )
        window._pending_changed_path = None
        window._cats = [SimpleNamespace(db_key=1)]
        window._save_load_worker = None

        refreshes = []
        window._start_quick_room_refresh = lambda: refreshes.append(True)
        window._reload = lambda: refreshes.append("reload")

        window._file_change_timer = QTimer()
        window._file_change_timer.setSingleShot(True)
        window._file_change_timer.setInterval(50)  # shorter for test
        window._file_change_timer.timeout.connect(
            lambda: main_window_module.MainWindow._on_file_changed_debounced(window)
        )

        # Five rapid events — each restarts the timer.
        for _ in range(5):
            main_window_module.MainWindow._on_file_changed_raw(window, "/fake/save.sav")
            time.sleep(0.005)

        # Pump until the timer fires.
        deadline = time.time() + 1.0
        while not refreshes and time.time() < deadline:
            qt_app.processEvents()
            time.sleep(0.01)

        assert refreshes == [True], (
            f"expected exactly one quick refresh, got {refreshes!r}"
        )
