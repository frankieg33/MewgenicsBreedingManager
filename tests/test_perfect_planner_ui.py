import os
import json
import sys
import uuid
import shutil
from types import SimpleNamespace
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_proj_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_src_dir = os.path.join(_proj_root, "src")
sys.path.insert(0, _src_dir)
sys.path.insert(0, _proj_root)

from PySide6.QtCore import Qt, QThread, QItemSelectionModel
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication, QAbstractItemView, QBoxLayout, QFrame, QHeaderView, QSplitter, QTableWidget, QTableWidgetItem

import mewgenics_manager as mm
import mewgenics.main_window as main_window_module
from mewgenics.utils import config as config_utils
from mewgenics.utils import paths as path_utils
import mewgenics.views.perfect_planner as perfect_planner_module
import mewgenics.views.room_optimizer as room_optimizer_view_module
from save_parser import STAT_NAMES


def _make_cat(
    db_key: int,
    *,
    unique_id: str,
    gender_display: str,
    name: str,
    room: str = "1st FL L",
    room_display: str = "1st FL L",
    status: str = "In House",
    age: int = 3,
    generation: int = 0,
    aggression: float = 0.2,
    libido: float = 0.8,
    inbredness: float = 0.1,
    lovers=None,
    mutations=None,
    passive_abilities=None,
    disorders=None,
    defects=None,
    abilities=None,
):
    class _HashableNamespace(SimpleNamespace):
        def __hash__(self):
            return hash(self.db_key)

    return _HashableNamespace(
        db_key=db_key,
        unique_id=unique_id,
        name=name,
        gender=gender_display,
        gender_display=gender_display,
        status=status,
        room=room,
        room_display=room_display,
        age=age,
        generation=generation,
        must_breed=False,
        is_blacklisted=False,
        parent_a=None,
        parent_b=None,
        haters=[],
        base_stats={stat: 6 for stat in STAT_NAMES},
        aggression=aggression,
        libido=libido,
        inbredness=inbredness,
        lovers=list(lovers or []),
        mutations=list(mutations or []),
        passive_abilities=list(passive_abilities or []),
        disorders=list(disorders or []),
        defects=list(defects or []),
        abilities=list(abilities or []),
        tags=[],
    )


def _make_tracker_row(pair_index: int, cat_a, cat_b, children: list):
    projection = {
        "stat_ranges": {stat: (4, 6) for stat in STAT_NAMES},
        "expected_stats": {stat: 5.5 for stat in STAT_NAMES},
        "sum_range": (34, 42),
    }
    return {
        "pair_index": pair_index,
        "cat_a": cat_a,
        "cat_b": cat_b,
        "known_offspring": children,
        "projection": projection,
        "risk": 2.0,
        "coi": 0.0,
        "shared": (0, 0),
        "source": "using",
        "slot_index": 0,
    }


def _trait_core_list(traits):
    keys = ("category", "key", "display", "weight")
    return [{key: trait.get(key) for key in keys} for trait in traits]


def _trait_identity_list(traits):
    keys = ("category", "key", "weight")
    return [{key: trait.get(key) for key in keys} for trait in traits]


def _patch_config_paths(monkeypatch, root: Path, config_path: Path):
    monkeypatch.setattr(mm, "APPDATA_CONFIG_DIR", str(root))
    monkeypatch.setattr(mm, "APP_CONFIG_PATH", str(config_path))
    monkeypatch.setattr(path_utils, "APPDATA_CONFIG_DIR", str(root))
    monkeypatch.setattr(path_utils, "APP_CONFIG_PATH", str(config_path))
    monkeypatch.setattr(config_utils, "APPDATA_CONFIG_DIR", str(root))
    monkeypatch.setattr(config_utils, "APP_CONFIG_PATH", str(config_path))


@pytest.fixture(scope="module")
def qt_app():
    app = QApplication.instance() or QApplication([])
    return app


@pytest.fixture()
def planner_config(monkeypatch):
    scratch_root = Path(_proj_root) / "tmp" / f"_codex_test_write_{uuid.uuid4().hex}"
    scratch_root.mkdir(parents=True, exist_ok=True)
    config_path = scratch_root / "planner-settings.json"
    _patch_config_paths(monkeypatch, scratch_root, config_path)
    yield config_path
    shutil.rmtree(scratch_root, ignore_errors=True)


def test_selected_offspring_config_round_trip(planner_config):
    state = {"uid-a|uid-b": "uid-child"}
    mm._save_perfect_planner_selected_offspring(state)
    assert mm._load_perfect_planner_selected_offspring() == state


def test_furniture_view_shows_actual_items_and_remembers_splitters(qt_app, planner_config):
    furniture_data = {
        "angry_cat_bobble": mm.FurnitureDefinition(
            item_name="angry_cat_bobble",
            display_name="Angry Cat Bobble",
            description="A tiny bobble that likes attention.",
            effects={"Appeal": 1.0, "Comfort": 1.0, "Stimulation": -1.0, "special": 1.0, "FoodStorage": 40.0},
        ),
        "branch_vase": mm.FurnitureDefinition(
            item_name="branch_vase",
            display_name="Branch Vase",
            description="A simple vase with a branch in it.",
            effects={"Appeal": 2.0},
        ),
    }
    furniture = [
        mm.FurnitureItem(
            key=1,
            version=1,
            item_name="angry_cat_bobble",
            room="Attic",
            header_fields=(1, 2, 3, 4),
            placement_fields=(),
        ),
        mm.FurnitureItem(
            key=2,
            version=1,
            item_name="branch_vase",
            room="Floor2_Large",
            header_fields=(1, 2, 3, 4),
            placement_fields=(),
        ),
    ]
    cats = [
        _make_cat(1, unique_id="uid-a", gender_display="M", name="Alpha", room="Attic"),
        _make_cat(2, unique_id="uid-b", gender_display="F", name="Bravo", room="Floor2_Large"),
        _make_cat(3, unique_id="uid-c", gender_display="M", name="Charlie", room="Floor2_Large"),
    ]

    view1 = mm.FurnitureView()
    view1.resize(800, 600)
    view1.show()
    view1.set_context(cats, furniture, furniture_data, ["Attic", "Floor2_Large"])
    qt_app.processEvents()

    assert view1._table.horizontalHeaderItem(0).text() == "#"
    assert view1._table.horizontalHeaderItem(1).text() == "Room"
    assert view1._table.horizontalHeaderItem(2).text() == "Pieces"
    assert view1._table.horizontalHeaderItem(3).text() == "Cats"
    assert view1._table.horizontalHeaderItem(4).text() == "APP"
    assert view1._table.horizontalHeaderItem(5).text() == "COMF Raw"
    assert view1._table.horizontalHeaderItem(7).text() == "COMF"
    assert view1._table.horizontalHeaderItem(8).text() == "STIM"
    assert view1._table.horizontalHeaderItem(9).text() == "HEA"
    assert view1._table.horizontalHeaderItem(10).text() == "MUT"
    assert view1._item_table.horizontalHeaderItem(1).text() == "Pin"
    assert view1._item_table.horizontalHeaderItem(2).text() == "Item"
    assert view1._item_table.horizontalHeaderItem(3).text() == "APP"
    assert view1._item_table.horizontalHeaderItem(4).text() == "COMF"
    assert view1._item_table.horizontalHeaderItem(5).text() == "STIM"
    assert view1._item_table.horizontalHeaderItem(6).text() == "HEA"
    assert view1._item_table.horizontalHeaderItem(7).text() == "MUT"

    view1._table.sortItems(0, Qt.SortOrder.AscendingOrder)
    view1._item_table.sortItems(3, Qt.SortOrder.AscendingOrder)
    qt_app.processEvents()

    assert view1._table.item(0, 0).text() == "1"
    assert view1._table.item(0, 1).text() == "Whole Home"
    assert view1._table.item(0, 2).text() == "2"
    assert view1._table.item(0, 3).text() == "3"
    assert view1._item_table.item(0, 2).text() == "Branch Vase"
    assert view1._item_table.item(0, 3).text() == "+2"

    assert view1._item_title.text() == "Whole Home"
    assert view1._item_table.rowCount() == 2
    assert not view1._item_table.isColumnHidden(2)
    assert not view1._item_table.item(0, 1).icon().isNull()
    assert view1._item_table.item(0, 2).text() == "Branch Vase"
    assert view1._item_table.item(0, 3).text() == "+2"
    assert view1._item_table.item(1, 2).text() == "Angry Cat Bobble"
    assert view1._item_table.item(1, 3).text() == "+1"
    assert view1._item_table.item(1, 4).text() == "+1"
    assert view1._item_table.item(1, 5).text() == "-1"
    assert view1._item_table.item(1, 8).text() == "FoodStorage +40, special"
    assert view1._table.item(0, 0).text() == "1"
    assert view1._table.item(0, 1).text() == "Whole Home"
    assert view1._table.item(0, 1).font().bold()
    assert view1._table.item(1, 0).text() == "2"
    assert view1._table.item(1, 1).text() == "Attic"
    assert view1._table.item(1, 2).text() == "1"
    assert view1._table.item(1, 3).text() == "1"

    view1._table.selectRow(0)
    qt_app.processEvents()
    assert view1._item_title.text() == "Whole Home"
    assert view1._item_table.rowCount() == 2

    pin_item = view1._item_table.item(0, 1)
    view1._item_table.itemClicked.emit(pin_item)
    qt_app.processEvents()
    assert not view1._item_table.item(0, 1).icon().isNull()

    view1._search.setText("branch")
    qt_app.processEvents()
    assert view1._item_table.rowCount() == 1
    assert view1._item_table.item(0, 2).text() == "Branch Vase"
    view1._search.clear()
    qt_app.processEvents()
    assert view1._item_table.rowCount() == 2

    view1._pin_only_check.setChecked(True)
    qt_app.processEvents()
    assert view1._item_table.rowCount() == 1
    assert view1._item_table.item(0, 2).text() == "Branch Vase"
    view1._pin_only_check.setChecked(False)
    qt_app.processEvents()
    assert view1._item_table.rowCount() == 2

    view1._item_table.setColumnWidth(2, 108)
    view1._item_table.setColumnWidth(8, 164)
    view1._layout_splitter.setSizes([140, 660])
    view1._splitter.setSizes([110, 290])
    qt_app.processEvents()
    view1._save_session_state()

    saved = mm._load_ui_state("furniture_state")
    assert saved["layout_splitter_sizes"] == view1._layout_splitter.sizes()
    assert saved["splitter_sizes"] == view1._splitter.sizes()
    assert isinstance(saved.get("item_header_state"), str) and saved["item_header_state"]

    view2 = mm.FurnitureView()
    view2.resize(800, 600)
    view2.show()
    view2.set_context(cats, furniture, furniture_data, ["Attic", "Floor2_Large"])
    qt_app.processEvents()

    assert view2._layout_splitter.sizes() == saved["layout_splitter_sizes"]
    assert view2._splitter.sizes() == saved["splitter_sizes"]
    assert not view2._item_table.item(0, 1).icon().isNull()
    assert view2._item_table.item(0, 2).text() == "Branch Vase"
    assert view2._item_table.columnWidth(2) == 108
    assert view2._item_table.columnWidth(8) == 164


def test_room_optimizer_room_column_starts_wide_enough(qt_app, planner_config):
    view = mm.RoomOptimizerView()
    qt_app.processEvents()

    header = view._table.horizontalHeader()
    assert header.sectionResizeMode(0) == QHeaderView.Interactive
    assert view._table.columnWidth(0) >= 140


def test_room_optimizer_configure_rooms_stacks_vertically(qt_app):
    panel = mm.RoomPriorityPanel()
    panel._clear_slots()
    panel._add_slot("Floor1_Large", "breeding", emit=False)
    qt_app.processEvents()

    assert panel._slots_layout.direction() == QBoxLayout.TopToBottom
    assert panel._slots[0]["up_btn"].text() == "↑"
    assert panel._slots[0]["dn_btn"].text() == "↓"
    assert panel._slots[0]["cap_spin"].minimumWidth() >= 66
    assert panel._slots[0]["stim_spin"].minimumWidth() >= 78


def test_room_optimizer_configure_rooms_prevents_duplicate_room_selection(qt_app):
    panel = mm.RoomPriorityPanel()
    panel._clear_slots()
    panel._add_slot("Floor1_Large", "breeding", emit=False)
    panel._add_slot("Floor2_Large", "fallback", emit=False)
    qt_app.processEvents()

    first_choices = [panel._slots[0]["combo"].itemData(i) for i in range(panel._slots[0]["combo"].count())]
    second_choices = [panel._slots[1]["combo"].itemData(i) for i in range(panel._slots[1]["combo"].count())]
    assert "Floor1_Large" in first_choices
    assert "Floor2_Large" in second_choices
    assert "Floor2_Large" not in first_choices
    assert "Floor1_Large" not in second_choices

    panel._slots[1]["combo"].setCurrentIndex(panel._slots[1]["combo"].findData("Attic"))
    qt_app.processEvents()

    refreshed_first_choices = [panel._slots[0]["combo"].itemData(i) for i in range(panel._slots[0]["combo"].count())]
    assert "Attic" not in refreshed_first_choices
    assert panel._slots[0]["combo"].currentData() == "Floor1_Large"


def test_room_optimizer_breeding_rooms_default_to_six_capacity(qt_app):
    panel = mm.RoomPriorityPanel()
    panel._clear_slots()
    panel.set_config([
        {"room": "Floor1_Large", "type": "breeding", "max_cats": None, "base_stim": 50},
        {"room": "Attic", "type": "fallback", "max_cats": None, "base_stim": 50},
    ])
    qt_app.processEvents()

    assert panel._slots[0]["cap_spin"].value() == 6
    assert panel._slots[1]["cap_spin"].value() == 0


def test_room_optimizer_default_room_order_matches_optimizer_layout():
    default_config = mm._default_room_priority_config()
    assert [slot["room"] for slot in default_config] == [
        "Floor1_Large",
        "Floor1_Small",
        "Floor2_Small",
        "Floor2_Large",
        "Attic",
    ]
    assert [slot["type"] for slot in default_config] == [
        "best_pairs",
        "best_pairs",
        "best_pairs",
        "best_pairs",
        "fallback",
    ]
    assert [slot["max_cats"] for slot in default_config] == [10, 10, 10, 10, None]


def test_room_optimizer_places_setup_between_rooms_and_pairs(qt_app, planner_config):
    view = mm.RoomOptimizerView()
    qt_app.processEvents()

    assert view._bottom_tabs.count() == 5
    assert view._bottom_tabs.widget(0) is view._configure_rooms_tab
    assert view._bottom_tabs.widget(1) is view._stat_priorities_tab
    assert view._bottom_tabs.widget(2) is view._setup_tab
    assert view._bottom_tabs.widget(3) is view._details_pane
    assert view._bottom_tabs.widget(4) is view._cat_locator
    assert view._setup_splitter.orientation() == Qt.Horizontal
    assert view._deep_optimize_btn.text().startswith("More Depth")


def test_room_optimizer_setup_controls_stack_and_help_panel(qt_app, planner_config):
    view = mm.RoomOptimizerView()
    qt_app.processEvents()

    assert view._top_actions_layout.itemAt(0).widget() is view._optimize_btn
    assert view._top_actions_layout.itemAt(1).widget() is view._deep_optimize_btn
    assert view._top_actions_layout.itemAt(2).widget() is view._import_planner_btn
    assert view._setup_controls_layout.itemAt(0).widget() is view._setup_stats_row
    assert view._setup_controls_layout.itemAt(1).widget() is view._shared_search_note
    assert view._setup_controls_layout.itemAt(2).widget() is view._mode_toggle_btn
    assert view._setup_controls_layout.itemAt(3).widget() is view._minimize_variance_checkbox
    assert view._min_stats_box_layout.direction() == QBoxLayout.LeftToRight
    assert view._max_risk_box_layout.direction() == QBoxLayout.LeftToRight
    assert view._min_stats_box_layout.itemAt(0).widget() is view._min_stats_label
    assert view._min_stats_box_layout.itemAt(1).widget() is view._min_stats_input
    assert view._max_risk_box_layout.itemAt(0).widget() is view._max_risk_label
    assert view._max_risk_box_layout.itemAt(1).widget() is view._max_risk_input
    assert "Settings" in view._shared_search_note.text()
    assert view._deep_optimize_btn.toolTip() == "Use simulated annealing for a slower, deeper search."
    assert view._optimize_btn.toolTip() == "Run the optimizer once using the current room and scoring settings."
    assert view._setup_info_title.text() == "Optimizer Setup Guide"
    setup_text = view._setup_info_browser.toPlainText()
    assert "Optimizer options" in setup_text
    assert "Description" in setup_text
    assert "Import Breeding Planner" in setup_text
    assert "Run the optimizer once" in setup_text


def test_room_optimizer_result_table_preserves_room_cat_mapping_with_sorting(qt_app, planner_config):
    view = mm.RoomOptimizerView()
    qt_app.processEvents()

    # Reproduce the state that used to scramble the table during result fill.
    view._table.setSortingEnabled(True)
    view._table.sortByColumn(0, Qt.AscendingOrder)

    result = {
        "room_rows": [
            {
                "room": "RoomB",
                "room_label": "Room B",
                "capacity": 2,
                "base_stim": 50.0,
                "cat_names": ["Beta"],
                "cat_keys": [2],
                "pairs": [],
                "avg_stats": 0.0,
                "avg_risk": 0.0,
                "is_fallback": False,
            },
            {
                "room": "RoomA",
                "room_label": "Room A",
                "capacity": 2,
                "base_stim": 50.0,
                "cat_names": ["Alpha"],
                "cat_keys": [1],
                "pairs": [],
                "avg_stats": 0.0,
                "avg_risk": 0.0,
                "is_fallback": False,
            },
            {
                "room": "RoomC",
                "room_label": "Room C",
                "capacity": 2,
                "base_stim": 50.0,
                "cat_names": ["Gamma"],
                "cat_keys": [3],
                "pairs": [],
                "avg_stats": 0.0,
                "avg_risk": 0.0,
                "is_fallback": True,
            },
        ],
        "locator_data": [],
        "excluded_rows": [],
        "mode_family": False,
        "min_stats": 0,
        "max_risk": 50.0,
        "minimize_variance": False,
        "avoid_lovers": True,
        "prefer_low_aggression": True,
        "prefer_high_libido": True,
        "maximize_throughput": False,
        "sa_temperature": 8.0,
        "sa_neighbors": 120,
        "use_sa": True,
    }

    view._on_optimizer_result(result)
    qt_app.processEvents()

    assert view._table.columnCount() == 7
    assert view._table.item(0, 1).text() == "Best Pairs"
    assert view._table.item(2, 1).text() == "Fallback"

    rows = {
        view._table.item(row, 0).text(): view._table.item(row, 2).text()
        for row in range(view._table.rowCount())
    }

    assert rows == {
        "Room A": "Alpha",
        "Room B": "Beta",
        "Room C": "Gamma",
    }


def test_cat_locator_marks_cats_with_lovers(qt_app):
    locator = mm.RoomOptimizerCatLocator()
    locator.show_assignments([
        {
            "name": "Meryl",
            "gender_display": "F",
            "db_key": 1,
            "has_lover": True,
            "tags": [],
            "age": 6,
            "current_room": "1st FL R",
            "assigned_room": "Pair 1",
            "room_order": 0,
            "needs_move": True,
        },
        {
            "name": "Didou",
            "gender_display": "M",
            "db_key": 2,
            "has_lover": False,
            "tags": [],
            "age": 21,
            "current_room": "Attic",
            "assigned_room": "Pair 2",
            "room_order": 1,
            "needs_move": False,
        },
    ])
    qt_app.processEvents()

    assert "♥" in locator._table.item(0, locator.COL_CAT).text()
    assert "♥" not in locator._table.item(1, locator.COL_CAT).text()


def test_breeding_partners_view_stays_stable_with_sorting_enabled(qt_app):
    view = mm.BreedingPartnersView()
    view._table.setSortingEnabled(True)

    alpha = _make_cat(
        1,
        unique_id="uid-alpha",
        gender_display="M",
        name="Alpha",
        room="Floor1_Large",
        room_display="1st FL L",
    )
    beta = _make_cat(
        2,
        unique_id="uid-beta",
        gender_display="F",
        name="Beta",
        room="Floor1_Large",
        room_display="1st FL L",
    )
    gamma = _make_cat(
        3,
        unique_id="uid-gamma",
        gender_display="M",
        name="Gamma",
        room="Attic",
        room_display="Attic",
    )
    delta = _make_cat(
        4,
        unique_id="uid-delta",
        gender_display="F",
        name="Delta",
        room="Attic",
        room_display="Attic",
    )
    omega = _make_cat(
        5,
        unique_id="uid-omega",
        gender_display="M",
        name="Omega",
        room="Floor2_Small",
        room_display="2F Left",
        status="Gone",
    )

    alpha_stub = SimpleNamespace(
        db_key=alpha.db_key,
        name=alpha.name,
        gender_display=alpha.gender_display,
        status=alpha.status,
        room=alpha.room,
        room_display=alpha.room_display,
        lovers=[],
    )
    beta_stub = SimpleNamespace(
        db_key=beta.db_key,
        name=beta.name,
        gender_display=beta.gender_display,
        status=beta.status,
        room=beta.room,
        room_display=beta.room_display,
        lovers=[],
    )
    gamma_stub = SimpleNamespace(
        db_key=gamma.db_key,
        name=gamma.name,
        gender_display=gamma.gender_display,
        status=gamma.status,
        room=gamma.room,
        room_display=gamma.room_display,
        lovers=[],
    )
    delta_stub = SimpleNamespace(
        db_key=delta.db_key,
        name=delta.name,
        gender_display=delta.gender_display,
        status=delta.status,
        room=delta.room,
        room_display=delta.room_display,
        lovers=[],
    )
    omega_stub = SimpleNamespace(
        db_key=omega.db_key,
        name=omega.name,
        gender_display=omega.gender_display,
        status=omega.status,
        room=omega.room,
        room_display=omega.room_display,
        lovers=[],
    )

    alpha.lovers = [beta_stub]
    beta.lovers = [alpha_stub]
    gamma.lovers = [delta_stub]
    delta.lovers = [gamma_stub]
    omega.lovers = [alpha_stub]
    alpha_stub.lovers = [beta]
    beta_stub.lovers = [alpha]
    gamma_stub.lovers = [delta]
    delta_stub.lovers = [gamma]
    omega_stub.lovers = [alpha]

    cats = [alpha, beta, gamma, delta, omega]

    view.set_cats(cats)
    qt_app.processEvents()

    first_rows = [
        [view._table.item(row, col).text() for col in range(view._table.columnCount())]
        for row in range(view._table.rowCount())
    ]

    view.set_cats(cats)
    qt_app.processEvents()

    second_rows = [
        [view._table.item(row, col).text() for col in range(view._table.columnCount())]
        for row in range(view._table.rowCount())
    ]

    assert view._table.isSortingEnabled()
    assert first_rows == second_rows
    assert view._table.columnCount() == 6
    assert [row[:5] for row in first_rows] == [
        ["Mutual", "Alpha (M)", "Beta (F)", "1st FL L", "1st FL L"],
        ["Mutual", "Gamma (M)", "Delta (F)", "Attic", "Attic"],
    ]
    assert first_rows[0][5] == "Alpha <-> Beta"
    assert first_rows[1][5] == "Gamma <-> Delta"
    assert view._table.rowCount() == 2

    view._table.sortByColumn(view.COL_CAT_B, Qt.AscendingOrder)
    qt_app.processEvents()
    sorted_rows = [
        [view._table.item(row, col).text() for col in range(view._table.columnCount())]
        for row in range(view._table.rowCount())
    ]
    assert [row[view.COL_CAT_B] for row in sorted_rows] == ["Beta (F)", "Delta (F)"]


def test_family_tree_filter_buttons_show_counts(qt_app):
    view = mm.FamilyTreeBrowserView()
    cats = [
        _make_cat(1, unique_id="uid-a", gender_display="F", name="Alpha", status="In House"),
        _make_cat(2, unique_id="uid-b", gender_display="M", name="Bravo", status="Gone"),
        _make_cat(3, unique_id="uid-c", gender_display="F", name="Charlie", status="Adventure"),
    ]
    view.set_cats(cats)
    qt_app.processEvents()

    assert view._all_btn.text().endswith("(3)")
    assert view._alive_btn.text().endswith("(2)")

    view.set_cats(cats[:1])
    qt_app.processEvents()

    assert view._all_btn.text().endswith("(1)")
    assert view._alive_btn.text().endswith("(1)")


def test_furniture_view_includes_whole_home_row_excluding_unplaced(qt_app, planner_config):
    furniture_data = {
        "angry_cat_bobble": mm.FurnitureDefinition(
            item_name="angry_cat_bobble",
            display_name="Angry Cat Bobble",
            description="A tiny bobble that likes attention.",
            effects={"Appeal": 1.0, "special": 1.0, "FoodStorage": 40.0},
        ),
        "floor_sticker": mm.FurnitureDefinition(
            item_name="floor_sticker",
            display_name="Floor Sticker",
            description="This one is still waiting for a room.",
            effects={"Appeal": 2.0},
        ),
    }
    furniture = [
        mm.FurnitureItem(
            key=1,
            version=1,
            item_name="angry_cat_bobble",
            room="Attic",
            header_fields=(1, 2, 3, 4),
            placement_fields=(),
        ),
        mm.FurnitureItem(
            key=2,
            version=1,
            item_name="floor_sticker",
            room="",
            header_fields=(1, 2, 3, 4),
            placement_fields=(),
        ),
    ]
    cats = [
        _make_cat(1, unique_id="uid-a", gender_display="M", name="Alpha", room="Attic"),
        _make_cat(2, unique_id="uid-b", gender_display="F", name="Bravo", room=""),
    ]

    view = mm.FurnitureView()
    view.set_context(cats, furniture, furniture_data, ["Attic"])
    qt_app.processEvents()

    assert view._table.item(0, 0).text() == "1"
    assert view._table.item(0, 1).text() == "Whole Home"
    assert view._table.item(0, 1).font().bold()
    assert view._table.item(1, 0).text() == "2"
    assert view._table.item(1, 1).text() == "Attic"
    assert view._table.item(1, 2).text() == "1"
    assert view._table.item(2, 0).text() == "7"
    assert view._table.item(2, 1).text() == "Unplaced"
    assert view._table.item(2, 1).foreground().color() == QColor(160, 160, 175)
    assert "Whole Home" in view._browser.toPlainText()
    assert view._item_table.rowCount() == 1
    assert view._item_table.selectionMode() == QAbstractItemView.ExtendedSelection
    assert view._item_table.selectionBehavior() == QAbstractItemView.SelectRows
    assert view._pin_toggle_btn.icon().isNull()
    assert not view._pin_only_check.icon().isNull()
    assert not view._item_table.isColumnHidden(2)
    assert view._item_table.item(0, 2).text() == "Angry Cat Bobble"
    assert view._item_table.item(0, 3).text() == "+1"
    assert view._item_table.item(0, 8).text() == "FoodStorage +40, special"


def test_furniture_item_table_allows_multi_row_selection(qt_app, planner_config):
    furniture_data = {
        "angry_cat_bobble": mm.FurnitureDefinition(
            item_name="angry_cat_bobble",
            display_name="Angry Cat Bobble",
            description="A tiny bobble that likes attention.",
            effects={"Appeal": 1.0},
        ),
        "branch_vase": mm.FurnitureDefinition(
            item_name="branch_vase",
            display_name="Branch Vase",
            description="A decorative vase.",
            effects={"Appeal": 2.0},
        ),
    }
    furniture = [
        mm.FurnitureItem(
            key=1,
            version=1,
            item_name="angry_cat_bobble",
            room="Attic",
            header_fields=(1, 2, 3, 4),
            placement_fields=(),
        ),
        mm.FurnitureItem(
            key=2,
            version=1,
            item_name="branch_vase",
            room="Attic",
            header_fields=(1, 2, 3, 4),
            placement_fields=(),
        ),
    ]
    cats = [
        _make_cat(1, unique_id="uid-a", gender_display="M", name="Alpha", room="Attic"),
    ]

    view = mm.FurnitureView()
    view.set_context(cats, furniture, furniture_data, ["Attic"])
    qt_app.processEvents()

    selection = view._item_table.selectionModel()
    selection.select(view._item_table.model().index(0, 0), QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows)
    selection.select(view._item_table.model().index(1, 0), QItemSelectionModel.Select | QItemSelectionModel.Rows)
    assert [idx.row() for idx in selection.selectedRows()] == [0, 1]
    assert view._pin_toggle_btn.text() == "Toggle Pin"

    view._pin_toggle_btn.click()
    qt_app.processEvents()

    assert view._pinned_item_keys == {1, 2}
    assert [idx.row() for idx in view._item_table.selectionModel().selectedRows()] == [0, 1]
    assert view._item_table.item(0, 1).icon().isNull() is False
    assert view._item_table.item(1, 1).icon().isNull() is False


def test_furniture_view_sorts_blank_stat_cells_last(qt_app, planner_config):
    furniture_data = {
        "plus_item": mm.FurnitureDefinition(
            item_name="plus_item",
            display_name="Plus Item",
            description="Positive appeal.",
            effects={"Appeal": 5.0},
        ),
        "minus_item": mm.FurnitureDefinition(
            item_name="minus_item",
            display_name="Minus Item",
            description="Negative appeal.",
            effects={"Appeal": -2.0},
        ),
        "blank_item": mm.FurnitureDefinition(
            item_name="blank_item",
            display_name="Blank Item",
            description="No appeal listed.",
            effects={},
        ),
    }
    furniture = [
        mm.FurnitureItem(
            key=1,
            version=1,
            item_name="plus_item",
            room="Attic",
            header_fields=(1, 2, 3, 4),
            placement_fields=(),
        ),
        mm.FurnitureItem(
            key=2,
            version=1,
            item_name="minus_item",
            room="Attic",
            header_fields=(1, 2, 3, 4),
            placement_fields=(),
        ),
        mm.FurnitureItem(
            key=3,
            version=1,
            item_name="blank_item",
            room="Attic",
            header_fields=(1, 2, 3, 4),
            placement_fields=(),
        ),
    ]
    cats = [
        _make_cat(1, unique_id="uid-a", gender_display="M", name="Alpha", room="Attic"),
    ]

    view = mm.FurnitureView()
    view.set_context(cats, furniture, furniture_data, ["Attic"])
    qt_app.processEvents()

    view._table.selectRow(1)
    qt_app.processEvents()
    view._item_table.sortItems(3, Qt.SortOrder.AscendingOrder)
    qt_app.processEvents()

    assert view._item_table.item(0, 2).text() == "Plus Item"
    assert view._item_table.item(0, 3).text() == "+5"
    assert view._item_table.item(1, 2).text() == "Minus Item"
    assert view._item_table.item(1, 3).text() == "-2"
    assert view._item_table.item(2, 2).text() == "Blank Item"
    assert view._item_table.item(2, 3).text() == "—"


def test_furniture_view_saves_layout_on_hide(qt_app, planner_config):
    furniture_data = {
        "angry_cat_bobble": mm.FurnitureDefinition(
            item_name="angry_cat_bobble",
            display_name="Angry Cat Bobble",
            description="A tiny bobble that likes attention.",
            effects={"Appeal": 1.0},
        ),
    }
    furniture = [
        mm.FurnitureItem(
            key=1,
            version=1,
            item_name="angry_cat_bobble",
            room="Attic",
            header_fields=(1, 2, 3, 4),
            placement_fields=(),
        ),
    ]
    cats = [
        _make_cat(1, unique_id="uid-a", gender_display="M", name="Alpha", room="Attic"),
    ]

    view = mm.FurnitureView()
    view.show()
    view.set_context(cats, furniture, furniture_data, ["Attic"])
    qt_app.processEvents()

    view._layout_splitter.setSizes([500, 820])
    view._splitter.setSizes([260, 360])
    qt_app.processEvents()
    view.hide()
    qt_app.processEvents()

    saved = mm._load_ui_state("furniture_state")
    assert saved["layout_splitter_sizes"] == view._layout_splitter.sizes()
    assert saved["splitter_sizes"] == view._splitter.sizes()


def test_foundation_pairs_config_round_trip(planner_config):
    config = [
        {"cat_a_uid": "uid-a", "cat_b_uid": "uid-b", "using": True},
        {"cat_a_uid": "uid-c", "cat_b_uid": "uid-d", "using": False},
        {"cat_a_uid": "", "cat_b_uid": "", "using": False},
        {"cat_a_uid": "uid-e", "cat_b_uid": "uid-f", "using": True},
        {"cat_a_uid": "uid-g", "cat_b_uid": "uid-h", "using": False},
        {"cat_a_uid": "uid-i", "cat_b_uid": "uid-j", "using": True},
    ]
    mm._save_perfect_planner_foundation_pairs(config)
    loaded = mm._load_perfect_planner_foundation_pairs()

    assert len(loaded) == 6
    assert loaded[0] == {"cat_a_uid": "uid-a", "cat_b_uid": "uid-b", "using": True}
    assert loaded[5] == {"cat_a_uid": "uid-i", "cat_b_uid": "uid-j", "using": True}


def test_planner_trait_summary_for_cat_reflects_selected_weights():
    cat = _make_cat(1, unique_id="uid-a", gender_display="M", name="Alpha", mutations=["Spotted"], abilities=["Fireball"], disorders=["Glitch"])
    summary = mm._planner_trait_summary_for_cat(
        cat,
        [
            {"category": "mutation", "key": "spotted", "display": "[Mutation] Spotted", "weight": 8},
            {"category": "ability", "key": "fireball", "display": "[Ability] Fireball", "weight": -4},
            {"category": "disorder", "key": "glitch", "display": "[Passive/Disorder] Glitch", "weight": -3},
        ],
    )

    assert summary["matches"] == ["Spotted"]
    assert summary["penalties"] == ["Fireball", "Glitch"]
    assert summary["score"] == pytest.approx(1.0)
    assert summary["ratio"] > 0


def test_planner_trait_summary_for_pair_rewards_shared_carriers():
    cat_a = _make_cat(1, unique_id="uid-a", gender_display="M", name="Alpha", mutations=["Spotted"])
    cat_b = _make_cat(2, unique_id="uid-b", gender_display="F", name="Bravo", mutations=["Spotted"])
    cat_c = _make_cat(3, unique_id="uid-c", gender_display="F", name="Charlie", mutations=[])
    traits = [{"category": "mutation", "key": "spotted", "display": "[Mutation] Spotted", "weight": 8}]

    shared = mm._planner_trait_summary_for_pair(cat_a, cat_b, traits)
    solo = mm._planner_trait_summary_for_pair(cat_a, cat_c, traits)

    assert shared["matches"] == ["Spotted"]
    assert shared["score"] > solo["score"]
    assert "background-color: rgba" in mm._planner_trait_style(shared["ratio"])


def test_foundation_panel_slot_count_updates_visible_rows(qt_app, planner_config):
    panel = mm.PerfectPlannerFoundationPairsPanel()
    panel.set_slot_count(3)

    assert panel._slot_count == 3
    assert len(panel._slots) == 3
    assert all(not slot["use_btn"].isEnabled() for slot in panel._slots)


def test_suggested_foundation_pairs_do_not_override_auto_plan(qt_app, planner_config, monkeypatch):
    cats = [
        _make_cat(1, unique_id="uid-a", gender_display="M", name="Alpha"),
        _make_cat(2, unique_id="uid-b", gender_display="F", name="Bravo"),
        _make_cat(3, unique_id="uid-c", gender_display="M", name="Charlie"),
        _make_cat(4, unique_id="uid-d", gender_display="F", name="Delta"),
    ]

    def _fake_score_pair_factors(cat_a, cat_b, *args, **kwargs):
        names = {cat_a.name, cat_b.name}
        if names == {"Charlie", "Delta"}:
            score = 7.0
            locked = list(STAT_NAMES)
            missing = []
        elif names == {"Alpha", "Bravo"}:
            score = 1.0
            locked = []
            missing = list(STAT_NAMES)
        else:
            score = 2.0
            locked = STAT_NAMES[:2]
            missing = STAT_NAMES[2:]

        projection = {
            "stat_ranges": {stat: (6, 7) for stat in STAT_NAMES},
            "expected_stats": {stat: 6.5 for stat in STAT_NAMES},
            "sum_range": (42, 49),
            "seven_plus_total": score,
            "locked_stats": locked,
            "reachable_stats": list(STAT_NAMES),
            "missing_stats": missing,
            "distance_total": 0.0,
        }
        return SimpleNamespace(
            compatible=True,
            risk=0.0,
            projection=projection,
            personality_bonus=0.0,
            trait_bonus=0.0,
            lover_bonus=0.0,
            stat_priority_bonus=0.0,
        )

    monkeypatch.setattr(perfect_planner_module, "score_pair_factors", _fake_score_pair_factors)
    monkeypatch.setattr(perfect_planner_module, "planner_pair_allows_breeding", lambda *args, **kwargs: True)

    view = mm.PerfectCatPlannerView()
    view.set_cats(cats)
    view._starter_pairs_input.setValue(1)
    view._foundation_panel.set_config([
        {"cat_a_uid": "uid-a", "cat_b_uid": "uid-b", "using": False},
    ])

    view._calculate_plan()

    stage_data = view._table.item(0, 0).data(Qt.UserRole)
    assert stage_data["actions"][0]["target"].startswith("Suggested: Charlie (M) x Delta (F)")


def test_using_foundation_pairs_override_auto_plan(qt_app, planner_config, monkeypatch):
    cats = [
        _make_cat(1, unique_id="uid-a", gender_display="M", name="Alpha"),
        _make_cat(2, unique_id="uid-b", gender_display="F", name="Bravo"),
        _make_cat(3, unique_id="uid-c", gender_display="M", name="Charlie"),
        _make_cat(4, unique_id="uid-d", gender_display="F", name="Delta"),
    ]

    def _fake_score_pair_factors(cat_a, cat_b, *args, **kwargs):
        names = {cat_a.name, cat_b.name}
        if names == {"Charlie", "Delta"}:
            score = 7.0
            locked = list(STAT_NAMES)
            missing = []
        elif names == {"Alpha", "Bravo"}:
            score = 1.0
            locked = []
            missing = list(STAT_NAMES)
        else:
            score = 2.0
            locked = STAT_NAMES[:2]
            missing = STAT_NAMES[2:]

        projection = {
            "stat_ranges": {stat: (6, 7) for stat in STAT_NAMES},
            "expected_stats": {stat: 6.5 for stat in STAT_NAMES},
            "sum_range": (42, 49),
            "seven_plus_total": score,
            "locked_stats": locked,
            "reachable_stats": list(STAT_NAMES),
            "missing_stats": missing,
            "distance_total": 0.0,
        }
        return SimpleNamespace(
            compatible=True,
            risk=0.0,
            projection=projection,
            personality_bonus=0.0,
            trait_bonus=0.0,
            lover_bonus=0.0,
            stat_priority_bonus=0.0,
        )

    monkeypatch.setattr(perfect_planner_module, "score_pair_factors", _fake_score_pair_factors)
    monkeypatch.setattr(perfect_planner_module, "planner_pair_allows_breeding", lambda *args, **kwargs: True)

    view = mm.PerfectCatPlannerView()
    view.set_cats(cats)
    view._starter_pairs_input.setValue(1)
    view._foundation_panel.set_config([
        {"cat_a_uid": "uid-a", "cat_b_uid": "uid-b", "using": True},
    ])

    view._calculate_plan()

    stage_data = view._table.item(0, 0).data(Qt.UserRole)
    assert stage_data["actions"][0]["target"].startswith("Using these: Alpha (M) x Bravo (F)")


def test_planner_view_uses_split_layout_and_tabs(qt_app, planner_config):
    view = mm.PerfectCatPlannerView()

    assert view._splitter.orientation() == Qt.Vertical
    assert view._bottom_splitter.orientation() == Qt.Horizontal
    assert view._bottom_tabs.count() == 4
    assert view._bottom_tabs.currentIndex() == 0
    assert view._bottom_splitter.widget(0) is view._details_pane
    assert view._bottom_tabs.widget(0) is view._guide_panel
    assert view._bottom_tabs.widget(1) is view._foundation_panel
    assert view._bottom_tabs.widget(2) is view._offspring_tracker
    assert view._bottom_tabs.widget(3) is view._cat_locator
    headers = [
        view._details_pane._actions_table.horizontalHeaderItem(i).text()
        for i in range(view._details_pane._actions_table.columnCount())
    ]
    assert headers == ["Target", "7s", "Risk%"]
    assert view._table.columnWidth(0) >= 98
    assert view._table.columnWidth(1) >= 260
    assert view._table.columnWidth(5) >= 320


def test_planner_detail_target_width_persists(qt_app, planner_config):
    view = mm.PerfectCatPlannerView()

    view._details_pane._actions_table.setColumnWidth(0, 138)
    view._save_session_state()

    saved = mm._load_ui_state("perfect_planner_state")
    assert isinstance(saved.get("actions_table_header_state"), str) and saved["actions_table_header_state"]

    view2 = mm.PerfectCatPlannerView()
    assert view2._details_pane._actions_table.columnWidth(0) == 138


def test_cat_locator_includes_offspring_and_pair_colors(qt_app):
    parent_a = _make_cat(1, unique_id="uid-a", gender_display="M", name="Alpha")
    parent_b = _make_cat(2, unique_id="uid-b", gender_display="F", name="Bravo")
    child = _make_cat(3, unique_id="uid-c", gender_display="F", name="Kid", age=1)
    parent_c = _make_cat(4, unique_id="uid-d", gender_display="M", name="Charlie")
    parent_d = _make_cat(5, unique_id="uid-e", gender_display="F", name="Delta")

    locator = mm.RoomOptimizerCatLocator()
    locator.show_assignments([
        {
            "name": parent_a.name,
            "gender_display": parent_a.gender_display,
            "db_key": parent_a.db_key,
            "has_lover": True,
            "tags": [],
            "age": parent_a.age,
            "current_room": "1st FL L",
            "assigned_room": "Pair 1",
            "room_order": 0,
            "needs_move": False,
        },
        {
            "name": parent_b.name,
            "gender_display": parent_b.gender_display,
            "db_key": parent_b.db_key,
            "has_lover": False,
            "tags": [],
            "age": parent_b.age,
            "current_room": "1st FL L",
            "assigned_room": "Pair 1",
            "room_order": 0,
            "needs_move": False,
        },
        {
            "name": child.name,
            "gender_display": child.gender_display,
            "db_key": child.db_key,
            "has_lover": False,
            "tags": [],
            "age": child.age,
            "current_room": "1st FL L",
            "assigned_room": "Pair 1 offspring",
            "room_order": 0.2,
            "needs_move": False,
        },
        {
            "name": parent_c.name,
            "gender_display": parent_c.gender_display,
            "db_key": parent_c.db_key,
            "has_lover": False,
            "tags": [],
            "age": parent_c.age,
            "current_room": "2nd FL R",
            "assigned_room": "Pair 2",
            "room_order": 1,
            "needs_move": False,
        },
        {
            "name": parent_d.name,
            "gender_display": parent_d.gender_display,
            "db_key": parent_d.db_key,
            "has_lover": False,
            "tags": [],
            "age": parent_d.age,
            "current_room": "2nd FL R",
            "assigned_room": "Pair 2",
            "room_order": 1,
            "needs_move": False,
        },
    ])

    assert locator._table.rowCount() == 5
    assert any("♥" in locator._table.item(row, 0).text() for row in range(locator._table.rowCount()))
    assert locator._table.item(2, 0).text().startswith("Kid")
    first_color = locator._table.item(0, 0).background().color()
    assert first_color == locator._table.item(1, 0).background().color()
    assert first_color == locator._table.item(2, 0).background().color()
    assert first_color != locator._table.item(3, 0).background().color()


def test_foundation_config_changed_schedules_plan_refresh(qt_app, planner_config, monkeypatch):
    refresh_calls = []

    def _fake_calculate_plan(self):
        refresh_calls.append(self)

    monkeypatch.setattr(mm.PerfectCatPlannerView, "_calculate_plan", _fake_calculate_plan)
    view = mm.PerfectCatPlannerView()
    view.set_cats([_make_cat(1, unique_id="uid-a", gender_display="M", name="Oguzok")])

    view._foundation_panel.configChanged.emit()
    for _ in range(5):
        qt_app.processEvents()
        QThread.msleep(25)
    qt_app.processEvents()

    assert refresh_calls == [view]


def test_room_optimizer_restores_state_and_reuses_imported_traits(qt_app, planner_config, monkeypatch):
    saved_traits = [{"category": "mutation", "key": "twoedarm", "display": "[Mutation] Two-Toed Arm", "weight": 3}]
    mm._save_ui_state("mutation_planner_state", {"selected_traits": saved_traits, "last_mode": "multi"})
    mm._save_ui_state(
        "room_optimizer_state",
        {
            "min_stats": "120",
            "max_risk": "15.5",
            "mode_family": True,
            "use_sa": True,
            "has_run": True,
        },
    )
    mm._set_room_optimizer_auto_recalc(True)

    calls = []
    monkeypatch.setattr(
        mm.RoomOptimizerView,
        "_calculate_optimal_distribution",
        lambda self, use_sa=False: calls.append(use_sa),
    )

    view = mm.RoomOptimizerView()
    view.set_planner_view(SimpleNamespace(get_selected_traits=lambda: list(saved_traits)))
    view.set_cats([
        _make_cat(1, unique_id="uid-a", gender_display="M", name="Alpha"),
        _make_cat(2, unique_id="uid-b", gender_display="F", name="Bravo"),
    ])

    assert view._min_stats_input.text() == "120"
    assert view._max_risk_input.text() == "15.5"
    assert view._mode_toggle_btn.isChecked() is True
    assert _trait_core_list(view._planner_traits) == saved_traits
    assert "Settings" in view._shared_search_note.text()
    assert view._deep_optimize_btn.isEnabled() is True
    assert view._maximize_throughput_checkbox.text().startswith("Maximize Throughput")
    assert calls == [True]


def test_room_optimizer_save_scoped_state_round_trips(qt_app, planner_config):
    save_path = planner_config.parent / f"room-save-{uuid.uuid4().hex}.mewsav"
    save_path.write_text("", encoding="utf-8")

    saved_traits = [
        {"category": "mutation", "key": "twoedarm", "display": "[Mutation] Two-Toed Arm", "weight": 4},
        {"category": "ability", "key": "pawmissile", "display": "[Ability] Paw Missile", "weight": -1},
    ]
    cats = [
        _make_cat(1, unique_id="uid-a", gender_display="M", name="Alpha"),
        _make_cat(2, unique_id="uid-b", gender_display="F", name="Bravo"),
        _make_cat(3, unique_id="uid-c", gender_display="F", name="Charlie"),
    ]

    mutation_view = mm.MutationDisorderPlannerView()
    mutation_view.set_save_path(str(save_path), refresh_existing=False)
    mutation_view._set_active_mode_traits(saved_traits)
    mutation_view._save_session_state()

    room_view = mm.RoomOptimizerView()
    room_view.set_planner_view(mutation_view)
    room_view.set_save_path(str(save_path), refresh_existing=False)
    room_view.set_cats(cats)

    room_view._min_stats_input.setText("120")
    room_view._max_risk_input.setText("15.5")
    room_view._maximize_throughput_checkbox.setChecked(True)
    room_view._mode_toggle_btn.setChecked(True)
    room_view._avoid_lovers_checkbox.setChecked(False)
    room_view._prefer_low_aggression_checkbox.setChecked(False)
    room_view._prefer_high_libido_checkbox.setChecked(True)
    room_view._room_priority_panel._slots[0]["mode_combo"].setCurrentIndex(
        room_view._room_priority_panel._slots[0]["mode_combo"].findData("melee")
    )
    room_view._room_priority_panel._slots[0]["cap_spin"].setValue(3)
    room_view._room_priority_panel._slots[0]["stim_spin"].setValue(88)
    qt_app.processEvents()

    fresh_mutation = mm.MutationDisorderPlannerView()
    fresh_mutation.set_save_path(str(save_path), refresh_existing=False)

    fresh_room = mm.RoomOptimizerView()
    fresh_room.set_planner_view(fresh_mutation)
    fresh_room.set_save_path(str(save_path), refresh_existing=False)
    fresh_room.set_cats(cats)

    assert _trait_core_list(fresh_mutation.get_selected_traits()) == saved_traits
    assert fresh_room._min_stats_input.text() == "120"
    assert fresh_room._max_risk_input.text() == "15.5"
    assert fresh_room._mode_toggle_btn.isChecked() is True
    assert fresh_room._avoid_lovers_checkbox.isChecked() is False
    assert fresh_room._prefer_low_aggression_checkbox.isChecked() is False
    assert fresh_room._prefer_high_libido_checkbox.isChecked() is True
    assert fresh_room._maximize_throughput_checkbox.isChecked() is True
    assert _trait_core_list(fresh_room._planner_traits) == saved_traits
    slot = fresh_room._room_priority_panel._slots[0]
    assert slot["mode_combo"].currentData() == "melee"
    assert slot["cap_spin"].value() == 3
    assert slot["stim_spin"].value() == 88


def test_room_optimizer_remembers_last_session_globally(qt_app, planner_config):
    save_path = planner_config.parent / f"room-save-{uuid.uuid4().hex}.mewsav"
    save_path.write_text("", encoding="utf-8")

    cats = [
        _make_cat(1, unique_id="uid-a", gender_display="M", name="Alpha"),
        _make_cat(2, unique_id="uid-b", gender_display="F", name="Bravo"),
    ]

    view = mm.RoomOptimizerView()
    view.set_save_path(str(save_path), refresh_existing=False)
    view.set_cats(cats)

    view._min_stats_input.setText("120")
    view._max_risk_input.setText("15.5")
    view._mode_toggle_btn.setChecked(True)
    view._avoid_lovers_checkbox.setChecked(False)
    view._prefer_low_aggression_checkbox.setChecked(False)
    view._prefer_high_libido_checkbox.setChecked(True)
    view._maximize_throughput_checkbox.setChecked(True)
    view._bottom_tabs.setCurrentIndex(1)

    slot = view._room_priority_panel._slots[0]
    slot["mode_combo"].setCurrentIndex(slot["mode_combo"].findData("fallback"))
    slot["cap_spin"].setValue(3)
    slot["stim_spin"].setValue(88)
    qt_app.processEvents()

    fresh = mm.RoomOptimizerView()
    assert fresh._min_stats_input.text() == "120"
    assert fresh._max_risk_input.text() == "15.5"
    assert fresh._mode_toggle_btn.isChecked() is True
    assert fresh._avoid_lovers_checkbox.isChecked() is False
    assert fresh._prefer_low_aggression_checkbox.isChecked() is False
    assert fresh._prefer_high_libido_checkbox.isChecked() is True
    assert fresh._maximize_throughput_checkbox.isChecked() is True
    assert fresh._bottom_tabs.currentIndex() == 1

    # Room priority config is intentionally NOT mirrored globally (see issue
    # #68): each save owns its room layout, so a fresh view without a save
    # path bound should see the built-in defaults, not whatever the previous
    # save had configured. The session state above (min_stats, toggles, etc.)
    # is still remembered globally for convenience.
    fresh_slot = fresh._room_priority_panel._slots[0]
    assert fresh_slot["mode_combo"].currentData() != "fallback"


def test_room_optimizer_global_state_wins_over_stale_save_state(qt_app, planner_config):
    save_path = planner_config.parent / f"room-save-{uuid.uuid4().hex}.mewsav"
    save_path.write_text("", encoding="utf-8")

    mm._save_ui_state(
        "room_optimizer_state",
        {
            "min_stats": "120",
            "max_risk": "15.5",
            "mode_family": True,
            "avoid_lovers": False,
            "prefer_low_aggression": False,
            "prefer_high_libido": True,
            "maximize_throughput": True,
            "bottom_tab_index": 1,
            "has_run": False,
            "use_sa": False,
        },
    )
    mm._save_planner_state_value(
        "room_priority_config",
        [
            {"room": "Floor1_Large", "type": "fallback", "max_cats": 3, "base_stim": 88.0},
            {"room": "Floor1_Small", "type": "best_pairs", "max_cats": 6, "base_stim": 50.0},
            {"room": "Floor2_Small", "type": "best_pairs", "max_cats": 6, "base_stim": 50.0},
            {"room": "Floor2_Large", "type": "best_pairs", "max_cats": 6, "base_stim": 50.0},
            {"room": "Attic", "type": "fallback", "max_cats": 0, "base_stim": 50.0},
        ],
        None,
    )

    stale_blob = {
        "room_optimizer_state": {
            "min_stats": "5",
            "max_risk": "1.0",
            "mode_family": False,
            "avoid_lovers": True,
            "prefer_low_aggression": True,
            "prefer_high_libido": False,
            "maximize_throughput": False,
            "bottom_tab_index": 2,
            "has_run": True,
            "use_sa": True,
        },
        "room_priority_config": [
            {"room": "Floor1_Large", "type": "best_pairs", "max_cats": 6, "base_stim": 50.0},
            {"room": "Floor1_Small", "type": "best_pairs", "max_cats": 6, "base_stim": 50.0},
            {"room": "Floor2_Small", "type": "best_pairs", "max_cats": 6, "base_stim": 50.0},
            {"room": "Floor2_Large", "type": "best_pairs", "max_cats": 6, "base_stim": 50.0},
            {"room": "Attic", "type": "fallback", "max_cats": 0, "base_stim": 50.0},
        ],
    }
    Path(str(save_path) + ".planner_state.json").write_text(json.dumps(stale_blob, indent=2, sort_keys=True), encoding="utf-8")

    cats = [
        _make_cat(1, unique_id="uid-a", gender_display="M", name="Alpha"),
        _make_cat(2, unique_id="uid-b", gender_display="F", name="Bravo"),
    ]

    fresh = mm.RoomOptimizerView()
    fresh.set_save_path(str(save_path), refresh_existing=False)
    fresh.set_cats(cats)

    assert fresh._min_stats_input.text() == "120"
    assert fresh._max_risk_input.text() == "15.5"
    assert fresh._mode_toggle_btn.isChecked() is True
    assert fresh._avoid_lovers_checkbox.isChecked() is False
    assert fresh._prefer_low_aggression_checkbox.isChecked() is False
    assert fresh._prefer_high_libido_checkbox.isChecked() is True
    assert fresh._maximize_throughput_checkbox.isChecked() is True
    assert fresh._bottom_tabs.currentIndex() == 1

    fresh_slot = fresh._room_priority_panel._slots[0]
    assert fresh_slot["mode_combo"].currentData() in {"best_pairs", "fallback"}
    assert fresh_slot["cap_spin"].value() in {3, 10}
    assert fresh_slot["stim_spin"].value() in {50, 88}


def test_room_optimizer_save_state_is_mirrored_back_to_app_config_on_load(qt_app, planner_config):
    save_path = planner_config.parent / f"room-save-{uuid.uuid4().hex}.mewsav"
    save_path.write_text("", encoding="utf-8")

    stale_blob = {
        "room_optimizer_state": {
            "min_stats": "120",
            "max_risk": "15.5",
            "mode_family": True,
            "minimize_variance": False,
            "avoid_lovers": False,
            "prefer_low_aggression": False,
            "prefer_high_libido": True,
            "maximize_throughput": True,
            "bottom_tab_index": 1,
            "has_run": False,
            "use_sa": False,
        },
        "room_priority_config": [
            {"room": "Floor1_Large", "type": "fallback", "max_cats": 3, "base_stim": 88.0},
            {"room": "Floor1_Small", "type": "best_pairs", "max_cats": 6, "base_stim": 50.0},
            {"room": "Floor2_Small", "type": "best_pairs", "max_cats": 6, "base_stim": 50.0},
            {"room": "Floor2_Large", "type": "best_pairs", "max_cats": 6, "base_stim": 50.0},
            {"room": "Attic", "type": "fallback", "max_cats": 0, "base_stim": 50.0},
        ],
    }
    Path(str(save_path) + ".planner_state.json").write_text(json.dumps(stale_blob, indent=2, sort_keys=True), encoding="utf-8")

    fresh = mm.RoomOptimizerView()
    fresh.set_save_path(str(save_path), refresh_existing=False)

    assert mm._load_ui_state("room_optimizer_state") == stale_blob["room_optimizer_state"]
    mirrored_config = mm._load_planner_state_value("room_priority_config", [], None)
    assert isinstance(mirrored_config, list)
    assert len(mirrored_config) == 5


def test_room_priority_available_room_refresh_does_not_clobber_saved_config(planner_config):
    initial_config = [
        {"room": "Floor1_Large", "type": "best_pairs", "max_cats": 10, "base_stim": 50.0},
        {"room": "Floor1_Small", "type": "best_pairs", "max_cats": 10, "base_stim": 50.0},
        {"room": "Floor2_Small", "type": "best_pairs", "max_cats": 10, "base_stim": 50.0},
        {"room": "Floor2_Large", "type": "best_pairs", "max_cats": 10, "base_stim": 50.0},
        {"room": "Attic", "type": "fallback", "max_cats": 0, "base_stim": 50.0},
    ]
    mm._save_planner_state_value("room_priority_config", initial_config, None)

    panel = mm.RoomPriorityPanel()
    assert panel.get_config() == initial_config

    panel.set_available_rooms(["Attic"])
    assert panel.get_config() == [
        {"room": "Attic", "type": "best_pairs", "max_cats": 10, "base_stim": 50.0},
    ]
    persisted = mm._load_planner_state_value("room_priority_config", [], None)
    assert [slot["room"] for slot in persisted] == [slot["room"] for slot in initial_config]
    assert [slot["type"] for slot in persisted] == [slot["type"] for slot in initial_config]


def test_flush_persistent_view_state_saves_room_optimizer_on_exit(planner_config, monkeypatch):
    calls = []
    captured = {}

    class _RoomView:
        _save_path = "save-one.mewsav"
        save_path = "save-one.mewsav"

        def save_session_state(self):
            calls.append("room_optimizer")

        def get_room_config(self):
            return [{"room": "Attic", "type": "fallback", "max_cats": 0, "base_stim": 50.0}]

    class _PlainView:
        def __init__(self, name):
            self._name = name

        def save_session_state(self):
            calls.append(self._name)

    monkeypatch.setattr(
        main_window_module,
        "_save_room_priority_config",
        lambda config, save_path: captured.update({"config": config, "save_path": save_path}),
    )

    window = SimpleNamespace(
        _room_optimizer_view=_RoomView(),
        _perfect_planner_view=_PlainView("perfect_planner"),
        _mutation_planner_view=_PlainView("mutation_planner"),
        _furniture_view=_PlainView("furniture"),
    )

    mm.MainWindow._flush_persistent_view_state(window)

    assert calls == ["room_optimizer", "perfect_planner", "mutation_planner", "furniture"]
    assert captured["save_path"] == "save-one.mewsav"
    assert captured["config"] == [{"room": "Attic", "type": "fallback", "max_cats": 0, "base_stim": 50.0}]


def test_room_optimizer_uses_shared_sa_settings_when_calculating(qt_app, planner_config, monkeypatch):
    mm._save_optimizer_search_settings({"temperature": 12.5, "neighbors": 73})

    captured = {}

    class _DummyWorker:
        def __init__(self, cats, excluded_keys, cache, params, parent=None):
            captured["params"] = dict(params)
            self.finished = SimpleNamespace(connect=lambda _slot: None)

        def isRunning(self):
            return False

        def start(self):
            captured["started"] = True

    monkeypatch.setattr(room_optimizer_view_module, "RoomOptimizerWorker", _DummyWorker)

    view = mm.RoomOptimizerView()
    view.set_cats([
        _make_cat(1, unique_id="uid-a", gender_display="M", name="Alpha"),
        _make_cat(2, unique_id="uid-b", gender_display="F", name="Bravo"),
    ])

    view._calculate_optimal_distribution(use_sa=True)

    assert captured["started"] is True
    assert captured["params"]["sa_temperature"] == 12.5
    assert captured["params"]["sa_neighbors"] == 73
    assert captured["params"]["use_sa"] is True


def test_mutation_planner_saved_traits_are_available_before_cats_are_loaded(qt_app, planner_config):
    saved_traits = [
        {"category": "mutation", "key": "twoedarm", "display": "[Mutation] Two-Toed Arm", "weight": 4},
        {"category": "ability", "key": "pawmissile", "display": "[Ability] Paw Missile", "weight": -1},
    ]
    mm._save_ui_state("mutation_planner_state", {"selected_traits": saved_traits, "last_mode": "multi"})

    mutation_view = mm.MutationDisorderPlannerView()
    assert _trait_core_list(mutation_view.get_selected_traits()) == saved_traits

    room_view = mm.RoomOptimizerView()
    room_view.set_planner_view(mutation_view)
    assert _trait_core_list(room_view._planner_traits) == saved_traits
    assert room_view._import_planner_btn.isEnabled() is True

    perfect_view = mm.PerfectCatPlannerView()
    perfect_view.set_mutation_planner_view(mutation_view)
    assert _trait_core_list(perfect_view._mutation_planner_traits) == saved_traits
    assert perfect_view._import_mutation_btn.isEnabled() is True


def test_room_optimizer_auto_recalc_toggle_persists_and_controls_autorun(qt_app, planner_config, monkeypatch):
    td = Path(_proj_root) / "tmp" / "_codex_test_runs" / uuid.uuid4().hex
    td.mkdir(parents=True, exist_ok=True)
    try:
        config_path = td / "settings.json"
        _patch_config_paths(monkeypatch, td, config_path)

        mm._set_room_optimizer_auto_recalc(True)
        mm._save_ui_state("room_optimizer_state", {"has_run": True})

        calls = []
        monkeypatch.setattr(
            mm.RoomOptimizerView,
            "_calculate_optimal_distribution",
            lambda self, use_sa=False: calls.append(use_sa),
        )

        view = mm.RoomOptimizerView()
        window = mm.MainWindow.__new__(mm.MainWindow)
        window._room_optimizer_view = view

        mm.MainWindow._toggle_room_optimizer_auto_recalc(window, False)
        view.set_cats([
            _make_cat(1, unique_id="uid-a", gender_display="M", name="Alpha"),
            _make_cat(2, unique_id="uid-b", gender_display="F", name="Bravo"),
        ])

        assert mm._saved_room_optimizer_auto_recalc() is False
        assert view._auto_recalculate is False
        assert calls == [False]

        mm.MainWindow._toggle_room_optimizer_auto_recalc(window, True)
        view.set_cats([
            _make_cat(1, unique_id="uid-a", gender_display="M", name="Alpha"),
            _make_cat(2, unique_id="uid-b", gender_display="F", name="Bravo"),
        ])

        assert mm._saved_room_optimizer_auto_recalc() is True
        assert view._auto_recalculate is True
        assert calls == [False, False]
    finally:
        shutil.rmtree(td, ignore_errors=True)


def test_room_optimizer_restores_last_run_on_load_even_with_autorecalc_off(qt_app, planner_config, monkeypatch):
    save_path = planner_config.parent / f"room-save-{uuid.uuid4().hex}.mewsav"
    save_path.write_text("", encoding="utf-8")
    mm._save_ui_state("room_optimizer_state", {"has_run": True, "use_sa": True})

    calls = []
    monkeypatch.setattr(
        mm.RoomOptimizerView,
        "_calculate_optimal_distribution",
        lambda self, use_sa=False: calls.append(use_sa),
    )

    view = mm.RoomOptimizerView()
    view._auto_recalculate = False
    view.set_save_path(str(save_path), refresh_existing=False)
    view.set_cats([
        _make_cat(1, unique_id="uid-a", gender_display="M", name="Alpha"),
        _make_cat(2, unique_id="uid-b", gender_display="F", name="Bravo"),
    ])

    assert calls == [True]


def test_perfect_planner_restores_last_session_and_autoruns(qt_app, planner_config, monkeypatch):
    mm._save_ui_state(
        "perfect_planner_state",
        {
            "min_stats": "110",
            "max_risk": "25.0",
            "starter_pairs": 6,
            "stimulation": 42,
            "sa_temperature": 12.5,
            "sa_neighbors": 77,
            "use_sa": True,
            "avoid_lovers": True,
            "prefer_low_aggression": False,
            "prefer_high_libido": True,
            "has_run": True,
        },
    )

    calls = []
    monkeypatch.setattr(mm.PerfectCatPlannerView, "_calculate_plan", lambda self: calls.append(self))

    view = mm.PerfectCatPlannerView()
    view.set_cats([
        _make_cat(1, unique_id="uid-a", gender_display="M", name="Alpha"),
        _make_cat(2, unique_id="uid-b", gender_display="F", name="Bravo"),
    ])

    assert view._min_stats_input.text() == "110"
    assert view._max_risk_input.text() == "25.0"
    assert view._starter_pairs_input.value() == 6
    assert view._stimulation_input.value() == 42
    assert not hasattr(view, "_shared_search_note")
    assert view._deep_optimize_btn.isChecked() is True
    assert view._avoid_lovers_checkbox.isChecked() is True
    assert view._prefer_low_aggression_checkbox.isChecked() is False
    assert view._prefer_high_libido_checkbox.isChecked() is True
    assert calls == [view]


def test_perfect_planner_save_scoped_state_round_trips(qt_app, planner_config):
    save_path = planner_config.parent / f"perfect-save-{uuid.uuid4().hex}.mewsav"
    save_path.write_text("", encoding="utf-8")

    saved_traits = [
        {"category": "mutation", "key": "twoedarm", "display": "[Mutation] Two-Toed Arm", "weight": 4},
        {"category": "ability", "key": "pawmissile", "display": "[Ability] Paw Missile", "weight": -1},
    ]
    cats = [
        _make_cat(1, unique_id="uid-a", gender_display="M", name="Alpha"),
        _make_cat(2, unique_id="uid-b", gender_display="F", name="Bravo"),
        _make_cat(3, unique_id="uid-c", gender_display="F", name="Charlie"),
    ]

    mutation_view = mm.MutationDisorderPlannerView()
    mutation_view.set_save_path(str(save_path), refresh_existing=False)
    mutation_view._set_active_mode_traits(saved_traits)
    mutation_view._save_session_state()

    view = mm.PerfectCatPlannerView()
    view.set_mutation_planner_view(mutation_view)
    view.set_save_path(str(save_path), refresh_existing=False)
    view.set_cats(cats)

    view._min_stats_input.setText("110")
    view._max_risk_input.setText("25.0")
    view._starter_pairs_input.setValue(6)
    view._stimulation_input.setValue(42)
    view._deep_optimize_btn.setChecked(True)
    view._avoid_lovers_checkbox.setChecked(True)
    view._prefer_low_aggression_checkbox.setChecked(False)
    view._prefer_high_libido_checkbox.setChecked(True)

    foundation_slot = view._foundation_panel._slots[0]
    foundation_slot["combo_a"].setCurrentIndex(foundation_slot["combo_a"].findData(mm._cat_uid(cats[0])))
    foundation_slot["combo_b"].setCurrentIndex(foundation_slot["combo_b"].findData(mm._cat_uid(cats[1])))
    foundation_slot["use_btn"].setChecked(True)

    rows = [_make_tracker_row(1, cats[0], cats[1], [cats[2]])]
    view._offspring_tracker.set_rows(rows)
    view._offspring_tracker._on_cell_clicked(0, 3)
    qt_app.processEvents()

    fresh_mutation = mm.MutationDisorderPlannerView()
    fresh_mutation.set_save_path(str(save_path), refresh_existing=False)

    fresh_view = mm.PerfectCatPlannerView()
    fresh_view.set_mutation_planner_view(fresh_mutation)
    fresh_view.set_save_path(str(save_path), refresh_existing=False)
    fresh_view.set_cats(cats)
    fresh_view._offspring_tracker.set_rows(rows)

    assert _trait_core_list(fresh_mutation.get_selected_traits()) == saved_traits
    assert fresh_view._min_stats_input.text() == "110"
    assert fresh_view._max_risk_input.text() == "25.0"
    assert fresh_view._starter_pairs_input.value() == 6
    assert fresh_view._stimulation_input.value() == 42
    assert not hasattr(fresh_view, "_shared_search_note")
    assert fresh_view._deep_optimize_btn.isChecked() is True
    assert fresh_view._avoid_lovers_checkbox.isChecked() is True
    assert fresh_view._prefer_low_aggression_checkbox.isChecked() is False
    assert fresh_view._prefer_high_libido_checkbox.isChecked() is True
    assert fresh_view._foundation_panel._slots[0]["combo_a"].currentData() == mm._cat_uid(cats[0])
    assert fresh_view._foundation_panel._slots[0]["combo_b"].currentData() == mm._cat_uid(cats[1])
    assert fresh_view._foundation_panel._slots[0]["use_btn"].isChecked() is True
    assert fresh_view._offspring_tracker._selected_child_uid_by_pair_key[
        mm._planner_pair_uid_key(cats[0], cats[1])
    ] == mm._cat_uid(cats[2])


def test_perfect_planner_passes_sa_parameters_to_refinement(qt_app, planner_config, monkeypatch):
    cats = [
        _make_cat(1, unique_id="uid-a", gender_display="M", name="Alpha"),
        _make_cat(2, unique_id="uid-b", gender_display="F", name="Bravo"),
        _make_cat(3, unique_id="uid-c", gender_display="M", name="Charlie"),
        _make_cat(4, unique_id="uid-d", gender_display="F", name="Delta"),
    ]

    def _fake_score_pair_factors(cat_a, cat_b, *args, **kwargs):
        projection = {
            "stat_ranges": {stat: (6, 7) for stat in STAT_NAMES},
            "expected_stats": {stat: 6.5 for stat in STAT_NAMES},
            "sum_range": (42, 49),
            "seven_plus_total": 5.0,
            "locked_stats": list(STAT_NAMES[:2]),
            "reachable_stats": list(STAT_NAMES),
            "missing_stats": [],
            "distance_total": 0.0,
        }
        return SimpleNamespace(
            compatible=True,
            risk=0.0,
            projection=projection,
            personality_bonus=0.0,
            trait_bonus=0.0,
            lover_bonus=0.0,
            stat_priority_bonus=0.0,
        )

    monkeypatch.setattr(perfect_planner_module, "score_pair_factors", _fake_score_pair_factors)
    monkeypatch.setattr(perfect_planner_module, "planner_pair_allows_breeding", lambda *args, **kwargs: True)
    mm._save_optimizer_search_settings({"temperature": 12.5, "neighbors": 73})

    captured = {}

    def _fake_run_sa_refinement(self, evaluated_pairs, selected_pairs, starter_pairs, sa_temperature, sa_neighbors):
        captured["starter_pairs"] = starter_pairs
        captured["sa_temperature"] = sa_temperature
        captured["sa_neighbors"] = sa_neighbors
        captured["selected_pairs"] = len(selected_pairs)
        return selected_pairs

    monkeypatch.setattr(mm.PerfectCatPlannerView, "_run_sa_refinement", _fake_run_sa_refinement)

    view = mm.PerfectCatPlannerView()
    view.set_cats(cats)
    view._starter_pairs_input.setValue(2)
    view._deep_optimize_btn.setChecked(True)

    view._calculate_plan()

    assert captured["starter_pairs"] == 2
    assert captured["sa_temperature"] == 12.5
    assert captured["sa_neighbors"] == 73
    assert captured["selected_pairs"] >= 2


def test_mutation_planner_restores_saved_traits_and_plan_mode(qt_app, planner_config, monkeypatch):
    saved_traits = [
        {"category": "mutation", "key": "twoedarm", "display": "[Mutation] Two-Toed Arm", "weight": 4},
        {"category": "ability", "key": "pawmissile", "display": "[Ability] Paw Missile", "weight": -1},
    ]
    mm._save_ui_state(
        "mutation_planner_state",
        {
            "room": "1st FL L",
            "stim": 72,
            "selected_traits": saved_traits,
            "last_mode": "multi",
        },
    )

    calls = []
    monkeypatch.setattr(
        mm.MutationDisorderPlannerView,
        "_update_multi_trait_plan",
        lambda self: calls.append([dict(t) for t in self._selected_traits]),
    )

    view = mm.MutationDisorderPlannerView()
    view.set_cats([
        _make_cat(
            1,
            unique_id="uid-a",
            gender_display="M",
            name="Alpha",
            room="1st FL L",
            mutations=["twoedarm"],
            abilities=["pawmissile"],
        ),
        _make_cat(
            2,
            unique_id="uid-b",
            gender_display="F",
            name="Bravo",
            room="1st FL L",
            mutations=["twoedarm"],
            abilities=["pawmissile"],
        ),
    ])

    assert view._room_combo.currentData() == "1st FL L"
    assert view._stim_spin.value() == 72
    assert _trait_identity_list(view._selected_traits) == _trait_identity_list(saved_traits)
    assert calls == [] or [_trait_identity_list(run) for run in calls] == [_trait_identity_list(saved_traits)]


def test_mutation_planner_places_cats_above_selected_traits(qt_app, planner_config):
    view = mm.MutationDisorderPlannerView()
    view.set_cats([
        _make_cat(1, unique_id="uid-a", gender_display="M", name="Alpha"),
        _make_cat(2, unique_id="uid-b", gender_display="F", name="Bravo"),
    ])

    assert view._right_splitter.widget(0) is view._cat_table
    assert view._right_splitter.widget(1) is view._traits_panel
    assert view._right_splitter.widget(2) is view._outcome_scroll


def test_mutation_planner_includes_birth_defects(qt_app, planner_config):
    view = mm.MutationDisorderPlannerView()
    view.set_cats([
        _make_cat(
            1,
            unique_id="uid-a",
            gender_display="M",
            name="Alpha",
            room="1st FL L",
            mutations=["twoedarm"],
            defects=["no eyebrows"],
        ),
    ])

    assert any(
        display == "[Birth Defect] No Eyebrows — -2 CHA"
        and tooltip == "-2 Charisma"
        and data == ("defect", "no eyebrows")
        for display, data, tooltip in view._trait_items_master
    )
    assert any(
        display == "[Mutation] Two-Toed Arm — -2 STR"
        and tooltip == "-2 Strength"
        and data == ("mutation", "twoedarm")
        for display, data, tooltip in view._trait_items_master
    )


def test_mutation_planner_trait_table_filters_cats(qt_app, planner_config):
    view = mm.MutationDisorderPlannerView()
    view.set_cats([
        _make_cat(
            1,
            unique_id="uid-a",
            gender_display="M",
            name="Alpha",
            room="1st FL L",
            mutations=["twoedarm"],
            abilities=["pawmissile"],
        ),
        _make_cat(
            2,
            unique_id="uid-b",
            gender_display="F",
            name="Bravo",
            room="1st FL L",
            abilities=["pawmissile"],
        ),
    ])

    assert view._trait_table.rowCount() > 0
    row = next(
        i for i in range(view._trait_table.rowCount())
        if view._trait_table.item(i, 0).text() == "Two-Toed Arm"
    )
    view._trait_table.selectRow(row)
    qt_app.processEvents()

    assert len(view._selected_traits) == 0
    assert view._cat_table.rowCount() == 1
    assert view._cat_table.item(0, 0).text() == "Alpha"

    view._add_trait_btn.click()
    qt_app.processEvents()
    assert len(view._selected_traits) == 1
    assert view._selected_traits[0]["category"] == "mutation"
    assert view._selected_traits[0]["key"] == "twoedarm"
    assert view._cat_table.rowCount() == 1
    assert view._cat_table.item(0, 0).text() == "Alpha"

    view._deselect_traits_btn.click()
    qt_app.processEvents()
    assert len(view._selected_traits) == 1
    assert not view._trait_table.selectionModel().selectedRows()
    assert view._cat_table.rowCount() == 2
    assert {view._cat_table.item(r, 0).text() for r in range(view._cat_table.rowCount())} == {"Alpha", "Bravo"}


def test_mutation_planner_multi_trait_selection_filters_union_and_clear_button(qt_app, planner_config):
    view = mm.MutationDisorderPlannerView()
    view.set_cats([
        _make_cat(
            1,
            unique_id="uid-a",
            gender_display="M",
            name="Alpha",
            room="Attic",
            mutations=["twoedarm"],
        ),
        _make_cat(
            2,
            unique_id="uid-b",
            gender_display="F",
            name="Bravo",
            room="Floor1_Large",
            abilities=["pawmissile"],
        ),
        _make_cat(
            3,
            unique_id="uid-c",
            gender_display="F",
            name="Charlie",
            room="Floor2_Large",
        ),
    ])

    rows = []
    for i in range(view._trait_table.rowCount()):
        name = view._trait_table.item(i, 0).text()
        if name in {"Two-Toed Arm", "Paw Missile"}:
            rows.append(i)
    assert len(rows) == 2
    selection = view._trait_table.selectionModel()
    selection.select(
        view._trait_table.model().index(rows[0], 0),
        QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows,
    )
    selection.select(
        view._trait_table.model().index(rows[1], 0),
        QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows,
    )
    qt_app.processEvents()

    assert len(view._selected_traits) == 0
    assert view._cat_table.rowCount() == 2
    assert {view._cat_table.item(r, 0).text() for r in range(view._cat_table.rowCount())} == {"Alpha", "Bravo"}

    view._add_trait_btn.click()
    qt_app.processEvents()

    assert len(view._selected_traits) == 2
    assert view._cat_table.rowCount() == 2
    assert {view._cat_table.item(r, 0).text() for r in range(view._cat_table.rowCount())} == {"Alpha", "Bravo"}

    view._clear_traits_btn.click()
    qt_app.processEvents()
    assert len(view._selected_traits) == 0
    assert not view._trait_table.selectionModel().selectedRows()
    assert view._cat_table.rowCount() == 3


def test_mutation_planner_defaults_to_all_cats_when_no_traits_selected(qt_app, planner_config):
    view = mm.MutationDisorderPlannerView()
    view.set_cats([
        _make_cat(
            1,
            unique_id="uid-a",
            gender_display="M",
            name="Alpha",
            room="Attic",
            mutations=["twoedarm"],
        ),
        _make_cat(
            2,
            unique_id="uid-b",
            gender_display="F",
            name="Bravo",
            room="Floor1_Large",
            abilities=["pawmissile"],
        ),
    ])

    assert len(view._selected_traits) == 0
    assert view._cat_table.rowCount() == 2
    assert {view._cat_table.item(r, 0).text() for r in range(view._cat_table.rowCount())} == {"Alpha", "Bravo"}

    idx = view._room_combo.findData("Floor1_Large")
    assert idx >= 0
    view._room_combo.setCurrentIndex(idx)
    qt_app.processEvents()

    assert len(view._selected_traits) == 0
    assert view._cat_table.rowCount() == 1
    assert view._cat_table.item(0, 0).text() == "Bravo"


def test_mutation_planner_trait_descriptions_hide_raw_keys(qt_app, planner_config):
    view = mm.MutationDisorderPlannerView()
    view.set_cats([
        _make_cat(
            1,
            unique_id="uid-a",
            gender_display="M",
            name="Alpha",
            room="1st FL L",
            abilities=["gym membership"],
        ),
    ])

    assert view._trait_table.columnCount() == 4
    row = next(
        i for i in range(view._trait_table.rowCount())
        if view._trait_table.item(i, 0).text() == "Gym Membership"
    )
    desc = view._trait_table.item(row, 3).text()
    assert "_DESC" not in desc
    assert desc != "ABILITY_GYMMEMBERSHIP_DESC"
    view._trait_table.selectRow(row)
    qt_app.processEvents()


def test_ability_tip_preserves_legitimate_commas(monkeypatch):
    # Previously `_trait_description_preview` cropped at the first comma as
    # a workaround for `_load_gpak_text_strings` returning multi-language
    # glob. The parser now picks the English column, so legitimate commas
    # inside a single-language description must survive to the tooltip
    # (e.g. GON fallback strings like "+2 STR, -1 DEX").
    key = "eyemutation"
    monkeypatch.setitem(mm._ABILITY_LOOKUP, key, "Eye Mutation lookup")
    monkeypatch.setitem(
        mm._ABILITY_DESC,
        key,
        "+2 STR, -1 DEX",
    )

    tip = mm._ability_tip("Eye Mutation")
    assert "+2 STR" in tip
    assert "-1 DEX" in tip


def test_trait_selector_summary_only_shows_stat_hints():
    assert mm._trait_selector_summary(
        "25% chance to spawn tall grass wherever you end your movement., "
        "25 % de probabilidad de generar hierba alta donde termine su movimiento."
    ) == ""
    assert mm._trait_selector_summary(
        "Poop when you take damage, Hace caca al sufrir daño."
    ) == ""
    assert mm._trait_selector_summary("-2 Charisma, -2 Charisma") == "-2 CHA"
    assert mm._trait_selector_summary("+2 Constitution, -1 Dexterity") == "+2 CON, -1 DEX"


def test_mutation_planner_two_cat_selection_builds_outcome_panel(qt_app, planner_config):
    view = mm.MutationDisorderPlannerView()
    view.set_cats([
        _make_cat(
            1,
            unique_id="uid-a",
            gender_display="M",
            name="Alpha",
            room="1st FL L",
            passive_abilities=["library"],
        ),
        _make_cat(
            2,
            unique_id="uid-b",
            gender_display="F",
            name="Bravo",
            room="1st FL L",
            passive_abilities=["library"],
        ),
    ])

    selection = view._cat_table.selectionModel()
    selection.select(
        view._cat_table.model().index(0, 0),
        QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows,
    )
    selection.select(
        view._cat_table.model().index(1, 0),
        QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows,
    )
    qt_app.processEvents()

    assert len(view._selected_pair) == 2
    assert "Alpha × Bravo" in view._pair_label.text()
    assert view._outcome_layout.count() > 0

    top_strip = view._outcome_layout.itemAt(1).widget()
    assert top_strip is not None
    top_layout = top_strip.layout()
    assert top_layout is not None
    assert isinstance(top_layout.itemAt(0).widget(), QTableWidget)
    assert isinstance(top_layout.itemAt(1).widget(), QFrame)

    pair_context = top_layout.itemAt(1).widget()
    pair_texts = []
    pair_layout = pair_context.layout()
    for i in range(pair_layout.count()):
        item = pair_layout.itemAt(i)
        widget = item.widget() if item is not None else None
        if widget is not None and hasattr(widget, "text"):
            pair_texts.append(widget.text())

    assert any("Partner A" in text for text in pair_texts)
    assert any("Likely offspring" in text for text in pair_texts)
    assert any("Alpha" in text for text in pair_texts)
    assert any("Bravo" in text for text in pair_texts)


def test_perfect_planner_import_button_uses_mutation_traits(qt_app, planner_config, monkeypatch):
    mutation_view = mm.MutationDisorderPlannerView()
    mutation_view._selected_traits = [
        {"category": "mutation", "key": "twoedarm", "display": "[Mutation] Two-Toed Arm", "weight": 4},
    ]

    view = mm.PerfectCatPlannerView()
    view.set_mutation_planner_view(mutation_view)

    refresh_calls = []
    monkeypatch.setattr(view, "_request_plan_refresh", lambda: refresh_calls.append(True))

    assert view._import_mutation_btn.isEnabled()

    view._import_mutation_btn.click()
    assert refresh_calls == [True]


def test_offspring_tracker_selection_is_exclusive_and_persistent(qt_app, planner_config):
    parent_a = _make_cat(1, unique_id="uid-a", gender_display="M", name="Oguzok")
    parent_b = _make_cat(2, unique_id="uid-b", gender_display="F", name="Molly Moo")
    child_one = _make_cat(3, unique_id="uid-c1", gender_display="F", name="Krita", age=1)
    child_two = _make_cat(4, unique_id="uid-c2", gender_display="M", name="Trigger", age=1)
    rows = [_make_tracker_row(1, parent_a, parent_b, [child_one, child_two])]

    tracker = mm.PerfectPlannerOffspringTracker()
    tracker.set_rows(rows)
    tracker._on_cell_clicked(0, 3)

    assert tracker._table.item(0, 3).text() == "☑"
    assert tracker._table.item(1, 3).text() == "☐"
    assert mm._load_perfect_planner_selected_offspring() == {"uid-a|uid-b": "uid-c1"}

    restored = mm.PerfectPlannerOffspringTracker()
    restored.set_rows(rows)
    assert restored._table.item(0, 3).text() == "☑"
    assert restored._table.item(1, 3).text() == "☐"

    restored._on_cell_clicked(1, 3)
    assert restored._table.item(0, 3).text() == "☐"
    assert restored._table.item(1, 3).text() == "☑"
    assert mm._load_perfect_planner_selected_offspring() == {"uid-a|uid-b": "uid-c2"}


def test_offspring_tracker_keeps_clicked_row_selected_after_refresh(qt_app, planner_config):
    parent_a = _make_cat(1, unique_id="uid-a", gender_display="M", name="Oguzok")
    parent_b = _make_cat(2, unique_id="uid-b", gender_display="F", name="Molly Moo")
    child_one = _make_cat(3, unique_id="uid-c1", gender_display="F", name="Krita", age=1)
    child_two = _make_cat(4, unique_id="uid-c2", gender_display="M", name="Trigger", age=1)
    rows = [_make_tracker_row(1, parent_a, parent_b, [child_one, child_two])]

    tracker = mm.PerfectPlannerOffspringTracker()
    tracker.set_rows(rows)
    tracker._table.setCurrentCell(0, 3)

    tracker._on_cell_clicked(0, 3)

    assert tracker._table.currentRow() == 0
    assert tracker._table.currentColumn() == 3
    assert tracker._table.item(0, 3).text() == "☑"


def test_offspring_tracker_keeps_row_order_when_sorting_was_enabled(qt_app, planner_config):
    parent_a = _make_cat(1, unique_id="uid-a", gender_display="M", name="Oguzok")
    parent_b = _make_cat(2, unique_id="uid-b", gender_display="F", name="Molly Moo")
    child_one = _make_cat(3, unique_id="uid-c1", gender_display="F", name="Krita", age=1)
    child_two = _make_cat(4, unique_id="uid-c2", gender_display="M", name="Trigger", age=1)
    rows = [_make_tracker_row(1, parent_a, parent_b, [child_one, child_two])]

    tracker = mm.PerfectPlannerOffspringTracker()
    tracker._table.setSortingEnabled(True)
    tracker.set_rows(rows)

    assert tracker._table.isSortingEnabled() is False
    assert tracker._table.rowCount() == 2
    assert tracker._table.item(0, 2).text() == "Krita"
    assert tracker._table.item(1, 2).text() == "Trigger"

    tracker._table.setSortingEnabled(True)
    tracker.set_rows(rows)

    assert tracker._table.isSortingEnabled() is False
    assert tracker._table.item(0, 2).text() == "Krita"
    assert tracker._table.item(1, 2).text() == "Trigger"


def test_offspring_selection_updates_stage_details_and_requests_refresh(qt_app, planner_config, monkeypatch):
    refresh_calls = []

    def _fake_refresh(self):
        refresh_calls.append(self)

    monkeypatch.setattr(mm.PerfectCatPlannerView, "_request_plan_refresh", _fake_refresh)
    view = mm.PerfectCatPlannerView()

    details_calls = []
    monkeypatch.setattr(
        view._details_pane,
        "show_stage",
        lambda data, context_note=None: details_calls.append((data, context_note)),
    )

    stage_data = {
        "stage": "Stage 1",
        "details": "Best unrelated pairs to start pushing 7s immediately",
        "summary": "Stage summary",
        "notes": ["note one"],
        "actions": [],
    }
    item = QTableWidgetItem("Stage 1")
    item.setData(Qt.UserRole, stage_data)
    view._table.setRowCount(1)
    view._table.setItem(0, 0, item)
    view._selected_stage_row = 0

    parent_a = _make_cat(1, unique_id="uid-a", gender_display="M", name="Oguzok")
    parent_b = _make_cat(2, unique_id="uid-b", gender_display="F", name="Molly Moo")
    child = _make_cat(3, unique_id="uid-c1", gender_display="F", name="Krita", age=1)
    row = {"pair": {"cat_a": parent_a, "cat_b": parent_b, "known_offspring": [child]}, "child": child}

    view._on_offspring_selected(row)

    assert refresh_calls == [view]
    assert details_calls
    assert details_calls[-1][0] == stage_data
    assert "Selected offspring pair: Oguzok x Molly Moo" in details_calls[-1][1]
    assert "Selected: Krita" in details_calls[-1][1]


def test_reset_ui_settings_action_resets_pane_views_without_touching_save_data(qt_app, planner_config, monkeypatch):
    calls = []

    class _DummyView:
        def reset_to_defaults(self):
            calls.append(self)

    window = mm.MainWindow.__new__(mm.MainWindow)
    window._room_optimizer_view = _DummyView()
    window._perfect_planner_view = _DummyView()
    window._mutation_planner_view = _DummyView()
    window._furniture_view = _DummyView()
    window._detail_splitter = QSplitter(Qt.Vertical)
    window._sidebar_splitter = QSplitter(Qt.Horizontal)
    window._base_sidebar_width = 190
    window._default_app_stylesheet = ""
    window._zoom_percent = 100
    window._font_size_offset = 0
    window._room_optimizer_auto_recalc_action = SimpleNamespace(
        blockSignals=lambda _blocked: None,
        setChecked=lambda _checked: None,
    )
    mm._set_room_optimizer_auto_recalc(False)

    messages = []
    window.statusBar = lambda: SimpleNamespace(showMessage=lambda msg: messages.append(msg))

    monkeypatch.setattr(mm.QMessageBox, "question", lambda *args, **kwargs: mm.QMessageBox.Yes)

    mm.MainWindow._reset_ui_settings_to_defaults(window)

    assert len(calls) == 4
    assert mm._saved_room_optimizer_auto_recalc() is False
    assert messages[-1] == "UI settings reset to defaults"
