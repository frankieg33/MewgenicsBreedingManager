# CLAUDE.md

## Project Overview

PySide6 desktop app that reads Mewgenics save files and provides breeding management tools. Parses binary `.sav` files (LZ4-compressed SQLite) to extract cat data (stats, abilities, mutations, relationships, lineage) and displays it across 12+ specialized views.

## Build & Run

```bash
pip install -r requirements.txt
python src/mewgenics_manager.py

# Build standalone Windows exe
build.bat
```

Tests: `pytest tests/ --basetemp=tmp/pytest` (use basetemp on WSL/Windows to avoid permission issues).

## Module Structure

Entry point is `src/mewgenics_manager.py` (thin wrapper that calls `mewgenics.app.main()`). All application code lives in the `src/mewgenics/` package.

```
src/
  mewgenics_manager.py              # Backwards-compatible entry point (thin wrapper)
  save_parser.py                    # Binary parser, Cat model, genetics/kinship logic
  breeding.py                       # Breeding compatibility, scoring, offspring tracking
  room_optimizer/
    types.py                        # Dataclasses: RoomConfig, OptimizationParams, ScoredPair, etc.
    optimizer.py                    # Room assignment algorithm
  visual_mutation_catalog.py        # Lookup tables: (slot, mutation_id) -> display name
  breed_priority/                   # Detailed Scoring view (standalone UI package)
    __init__.py                     # BreedPriorityView — main widget
    scoring.py                      # compute_breed_priority_score, ScoreResult, weights
    filters.py                      # FilterState + FilterDialog
    profiles.py                     # 5-slot profile UI + load/save/delete handlers
    complex_weights/                # ComplexWeight rule system (model, dialog, evaluator)
    columns.py, column_values.py    # Score table column layout + per-mode values
    recompute_helpers.py            # Relationship maps, compute_all_scores, heatmap norms
    stats_overview.py               # Current Stats Overview popup
    weight_popup.py                 # Weight editor popup
    tooltips.py                     # HTML tooltip builders
    delegates.py                    # Custom item delegates + header overlays
    theme.py, styles.py             # Colors + stylesheets
    collapsible_splitter.py         # Left-panel collapsible splitter
    color_utils.py, chip_colors.py  # Color math + chip color pairs
    stat_text_formatter.py          # Stat-cell text formatter
    constants.py                    # Backwards-compat re-exports
  mewgenics/
    __init__.py                     # Package init + module-level setup (locale, tags, thresholds)
    app.py                          # main() — QApplication, palette, save selector
    main_window.py                  # MainWindow (~3000 lines)
    constants.py                    # Colors, column indices, widths, stylesheets
    dialogs.py                      # TagManagerDialog, ThresholdPreferencesDialog,
                                    #   SharedOptimizerSearchSettingsDialog, SaveSelectorDialog
    panels/
      cat_detail.py                 # CatDetailPanel, LineageDialog, chip helpers
      room_priority.py              # RoomPriorityPanel
    models/
      breeding_cache.py             # BreedingCache + BreedingCacheWorker
      cat_table_model.py            # CatTableModel, NameTagDelegate, sort helpers
      room_filter_model.py          # RoomFilterModel
    workers/
      save_loader.py                # SaveLoadWorker
      room_refresh.py               # QuickRoomRefreshWorker
      optimizer_worker.py           # RoomOptimizerWorker
    views/
      family_tree.py                # FamilyTreeBrowserView
      safe_breeding.py              # SafeBreedingView
      breeding_partners.py          # BreedingPartnersView
      room_optimizer.py             # RoomOptimizerView, RoomOptimizerCatLocator, RoomOptimizerDetailPanel
      perfect_planner.py            # PerfectCatPlannerView + 4 sub-panels
      calibration.py                # CalibrationView
      mutation_planner.py           # MutationDisorderPlannerView + planner trait helpers
      furniture.py                  # FurnitureView
    utils/
      paths.py                      # Bundle dir, save dir, gpak paths, file finders
      config.py                     # App config load/save, UI state, splitter persistence
      localization.py               # _tr(), locale catalog, language management
      styling.py                    # Font enforcement, widget styling, _chip(), _sec()
      tags.py                       # Tag definitions, icons, pixmaps
      thresholds.py                 # Breeding threshold preferences
      optimizer_settings.py         # Optimizer flags, search settings, room priority config
      planner_state.py              # Planner blob persistence, foundation pairs
      game_data.py                  # GPAK loading, game data reload
      calibration.py                # Calibration data load/save, trait overrides
      cat_persistence.py            # Blacklist, must-breed, pinned, tags load/save
      cat_analysis.py               # _cat_base_sum, exceptional/donation checks
      abilities.py                  # Ability/mutation descriptions, tooltips, effect lines
      ability_icons.py              # SWF shape parser, ability icon rendering from GPAK
      shape_extractor.py            # DefinedShape PNG extraction from ZIP or GPAK catparts.swf
      table_state.py                # Table view header/sort state persistence
```

### `save_parser.py` — Core Data Layer

Everything that touches the binary save format or genetic math lives here. No Qt dependencies.

- **`BinaryReader`**: Stateful binary reader (u32, u64, f64, utf16str, etc.)
- **`Cat`**: Core data model. Holds stats, abilities, mutations, relationships, room assignment, generation depth.
- **`SaveData`**: Container for a fully-parsed save (cats list + metadata).
- **`GameData`**: Lookup tables for visual mutations and furniture definitions. Populated at startup from `.gpak` files.
- **`FurnitureItem / FurnitureDefinition / FurnitureRoomSummary`**: Furniture parsing and room stat aggregation.
- **`parse_save(path) -> (cats, errors)`**: Top-level entry point. Constructs Cat objects, resolves parent/child links, computes generation depths.
- `can_breed`, `risk_percent`, `kinship_coi`, `raw_coi`, `shared_ancestor_counts`: Breeding eligibility and kinship math.

Key constants:
- `STAT_NAMES = ["STR", "DEX", "CON", "INT", "SPD", "CHA", "LCK"]` — 7 stats, max value 7
- `EXCEPTIONAL_SUM_THRESHOLD = 40`, `DONATION_SUM_THRESHOLD = 34`, `DONATION_MAX_TOP_STAT = 6`
- Generation: `0` = stray (no parents in save), `1+` = bred kitten

### `breeding.py` — Breeding Logic

No Qt dependencies.

- **`PairProjection`**: Expected offspring stat ranges for a pair.
- **`PairFactors`**: Full score breakdown (risk, complementarity, personality bonus, etc.).
- **`pair_projection(cat_a, cat_b) -> PairProjection`**: Offspring stat projections.
- **`score_pair(cat_a, cat_b) -> PairFactors`**: Scores a pair on all axes.
- `is_mutual_lover_pair`, `planner_pair_allows_breeding`, `planner_inbreeding_penalty`, `planner_pair_bias`: Planner compatibility checks.
- `tracked_offspring`: Offspring tracked for a pair in the planner.

### `room_optimizer/` — Room Assignment

Greedy optimizer that assigns cats to rooms to maximize breeding outcomes.

- **`RoomType`** (enum): `BREEDING`, `FALLBACK`, `GENERAL`, `NONE`
- **`RoomConfig`**: Per-room settings (capacity, type, base stimulation).
- **`OptimizationParams`**: Solver config (min_stats, max_risk, stimulation threshold).
- **`optimize_room_distribution(cats, rooms, params) -> OptimizationResult`**: Main solver entry point.

### `mewgenics/` — Qt UI Package

All PySide6 code lives here. `mewgenics/__init__.py` runs one-time initialization (locale, tags, thresholds, game data).

**Key modules:**
- **`main_window.py`** — `MainWindow` (QMainWindow hub, owns all views via QTabWidget)
- **`app.py`** — `main()` entry point (QApplication setup, palette, save selector)
- **`dialogs.py`** — All dialog windows (tag manager, threshold prefs, optimizer settings, save selector)
- **`panels/cat_detail.py`** — `CatDetailPanel` (stat/trait detail for selected cat) + `LineageDialog`
- **`panels/room_priority.py`** — `RoomPriorityPanel` (room priority configuration)

**Views** (each is a self-contained tab):
- `views/family_tree.py` — `FamilyTreeBrowserView` (visual ancestry tree)
- `views/safe_breeding.py` — `SafeBreedingView` (safe breeding partners)
- `views/breeding_partners.py` — `BreedingPartnersView` (pair compatibility grid)
- `views/room_optimizer.py` — `RoomOptimizerView` + detail panel + cat locator
- `views/perfect_planner.py` — `PerfectCatPlannerView` + 4 sub-panels
- `views/calibration.py` — `CalibrationView` (parser field calibration, dev use)
- `views/mutation_planner.py` — `MutationDisorderPlannerView` (mutation/disorder targeting)
- `views/furniture.py` — `FurnitureView` (furniture stat viewer per room)
- `views/manual_scoring.py` — `ManualScoringView` (Simple Scoring — point-value editor)
- `../breed_priority/` — `BreedPriorityView` (Detailed Scoring — weighted breed-priority ranker with profiles, complex weights, filters, heatmap)

**Models & Workers:**
- `models/cat_table_model.py` — `CatTableModel`, `NameTagDelegate`
- `models/room_filter_model.py` — `RoomFilterModel`
- `models/breeding_cache.py` — `BreedingCache`, `BreedingCacheWorker`
- `workers/save_loader.py` — `SaveLoadWorker`
- `workers/room_refresh.py` — `QuickRoomRefreshWorker`
- `workers/optimizer_worker.py` — `RoomOptimizerWorker`

## Data Flow

1. User selects a `.sav` file -> `SaveLoadWorker` calls `parse_save()` -> `Cat` objects created
2. Parent/child links resolved by UID matching + blob scanning fallback
3. Generation depth computed iteratively (gen 0 = no parents)
4. `BreedingCache` pre-computes all pair outcomes in a background thread
5. `QFileSystemWatcher` triggers auto-refresh when the save file changes on disk

## Cat Sprite Rendering

Cat sprites are composited from DefinedShape PNGs in `src/CatAssets/DefinedShapes/`. These are rasterized SWF vector shapes extracted from `catparts.swf` in the game's GPAK archive.

**Extraction chain** (in `app.py` at startup via `ensure_defined_shapes()`):
1. If `DefinedShapes/` has >= 5000 PNGs, skip (already cached)
2. Try extracting from `CatAssets/DefinedShapes.zip` (~3 s, bundled in git)
3. Fall back to parsing `catparts.swf` from the GPAK (~25 s, requires game)

**Key files:**
- `utils/shape_extractor.py` — ZIP extraction + GPAK SWF parsing + Qt QPainter rendering
- `utils/ability_icons.py` — Shared SWF parsing primitives (`_BitReader`, `_parse_shape`, `_gpak_entry_bytes`)
- `swf_cat_renderer.py` — Reads PNGs from `DefinedShapes/`, composites layered sprites with palette texturing
- `swf_database.py` — `SWFDatabaseAccessor` for precomputed sprite frame data + shape bounds
- `CatAssets/swf_database/shapes.db` — Shape bounds metadata (10K+ entries)
- `CatAssets/DefinedShapes.zip` — Pre-rendered shape PNGs (6,894 files, 16.5 MB)

## Conventions

- Windows-targeted: save paths use `%LOCALAPPDATA%`, build produces `.exe`
- Qt signals/slots for all UI reactivity; `blockSignals(True)` prevents cascading updates during programmatic changes
- Views persist user choices to a JSON sidecar file alongside the save (load on `__init__`, save on every change)
- Utility modules use `_` prefix convention — functions are module-private but importable across the package
- Mutable module-level state (dicts, lists) must use in-place mutation (`.clear()` + `.update()`, slice assignment) when shared across modules, not rebinding

## Known Design Decisions

- **Lover conflicts at room level, not pair level**: `breeding.py::is_lover_conflict()` intentionally returns `False`. Lover exclusivity is enforced at room assignment time by `room_optimizer/optimizer.py::_filter_lover_exclusivity()`.
- **Generation depth fallback**: Cats with unresolvable ancestry default to generation 0 (stray). The iterative algorithm in `parse_save()` converges; the fallback is intentional.
- **Inbredness/sexuality dual field**: During `Cat.__init__`, `inbredness` temporarily holds the raw sexuality float. It is overwritten with true COI in `MainWindow._on_save_loaded()`. `parsed_inbredness` preserves the original for calibration override detection.
- **Cross-class access**: Views expose public properties/methods (`room_priority_panel`, `cat_locator`, `offspring_tracker`, `set_navigate_to_cat_callback()`, `save_session_state()`) for MainWindow to use. Avoid accessing `_private` attributes across class boundaries.
- **Module-level initialization**: `mewgenics/__init__.py` runs setup (game data, locale, tags, thresholds) once when the package is first imported. Modules that need initialized state import it after this runs.
- **DefinedShape extraction**: Shapes are extracted once and cached as PNGs. The ZIP is the primary source (fast, no game dependency). GPAK is the fallback (requires game). Individual PNGs are gitignored; only the ZIP is tracked.

## Release Checklist

Before pushing a release commit:

1. Update `VERSION` file with the new version number.
2. Update `WhatsNewDialog` default highlights and body text in `src/mewgenics/dialogs.py` to reflect the new release.
3. Update `README.md` current release and add release notes.
4. Commit, tag (`vX.Y.Z`), push, and create a GitHub release.

## tools/field_mapper/

Reverse-engineering pipeline for discovering binary field offsets. Dev-only — not part of the main app.
