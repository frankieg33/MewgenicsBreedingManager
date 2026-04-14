# Mewgenics Breeding Manager

A high-performance, Python-based tool for optimizing breeding operations in Mewgenics. It extracts data directly from your save files and helps you compare pairings, optimize room layouts, and plan long-term lines to maximize strong offspring while minimizing inbreeding risk.

Current release: `v5.4.8`

If you'd like to support the project, you can [here](https://ko-fi.com/frankieg33).

## Screenshots

### Main Page

![Main Page](Sceenshots/Home%20Screen.png)

### Breeding Optimizer

![Breeding Optimizer](Sceenshots/Room%20Optimizer.png)

### Perfect 7 Planner

![Perfect 7 Planner](Sceenshots/Perfect%207%20Planner.png)

## Core Features

- Load your save file and keep your full roster, lineage, and relationships in one place
- Compare pairings with inheritance odds, expected offspring stats, and risk
- Optimize room layouts with movement-aware scoring
- Plan long-term perfect-stat lines with the Perfect 7 planner
- Read ability and mutation text from `resources.gpak` when available
- Cat sprite rendering with DefinedShape extraction from GPAK

## Install

This project uses `pip` and `requirements.txt`.

```bash
git clone https://github.com/frankieg33/MewgenicsBreedingManager
cd MewgenicsBreedingManager
pip install -r requirements.txt
python src/mewgenics_manager.py
```

The app will automatically look for `resources.gpak` in common Steam install paths, the app's configured save root, or in the current working directory.

## Build

```bash
build.bat
```

On Linux, use `build.sh`.

## Requirements

- Python 3.14
- PySide6
- lz4
- openpyxl

## Credits

- Save parsing research based on [pzx521521/mewgenics-save-editor](https://github.com/pzx521521/mewgenics-save-editor)
- Community reverse-engineering help from players and mod users
- PR contributors: [0demongamer0](https://github.com/0demongamer0), [An-on-im](https://github.com/An-on-im), [byronaltice](https://github.com/byronaltice), [heartskingu](https://github.com/heartskingu), [ICaxapl](https://github.com/ICaxapl), [luisMolina95](https://github.com/luisMolina95), [TheMegax](https://github.com/TheMegax)
- Simulated annealing (SA) idea from [PurpleMyst](https://github.com/PurpleMyst/mewgenics_breeding_helper)
- Original idea and reference from frankieg33

## Release Notes

### v5.4.8

- Fixed Configure Rooms layout (capacity, room type, stimulation) bleeding between save files — `room_priority_config` now lives only in the per-save sidecar instead of being mirrored to the global app config (issue #68)
- Room Optimizer can now route kittens (age < 2) into fallback rooms instead of wasting breeding-room capacity on cats that can't breed yet. Eternal-youth cats are still placed in the best breeding room (issue #70)
- Room Optimizer can now avoid placing cats with desired mutations into high-Evolution rooms, and cats with desired disorders into high-Health rooms (which would otherwise override or cure those traits). Opt in via the new "Avoid Trait Loss" toggle (issue #71)

### v5.4.7

- Fixed Room Optimizer hanging or taking 30+ minutes for large rosters (58-96+ cats) — bitmask DP capped at 24 cats with greedy fallback, fallback rooms skip unnecessary pair computation, and shared kinship memo eliminates redundant pedigree traversals (issues #63, #64)
- Optimizer auto-calculation now waits for the breeding cache before starting, avoiding expensive uncached risk computations on every tab restore
- Fixed Mutation Planner trait selections reverting when switching tabs (issue #62)

### v5.4.6

- Fixed pixelated images on HiDPI/scaled displays — all icons, sprites, and thumbnails now render at native resolution
- Added a Cancel button to the Room Optimizer so long-running calculations can be aborted (issue #64)
- Added `face_cache` to `.gitignore` alongside other runtime caches

### v5.4.2

- Total Stats on the cat detail panel now includes visual mutation stat bonuses (e.g. Conjoined Body +2 CON). Previously these contributions were silently missing from the total — `base + mod + secondary` is now also augmented with `mutation_stat_bonus`, and a new `_parse_mutation_stat_delta` / `_mutation_stat_bonus_from_entries` pair handles regex extraction and L/R paired-part deduplication
- Mutation description formatting fixes: no longer glues all language variants (`"English,Polski,Русский,中文"`) together, no longer truncates after the first comma so `"+2 STR, -1 DEX"` survives in full, both halves of a two-stat modifier now show as separate chips, and short stat aliases (`STR`, `DEX`, …) are recognized alongside the full names
- Base body parts (fur/head/body sprite IDs stored in the same save slots as mutations) no longer surface as fake mutations in the detail panel — only entries with an actual gpak/catalog hit, or the 0xFFFFFFFE missing-part sentinel, are shown
- "Adv Ready" column no longer shows a ✓ for retired/dead ("Gone") cats even if a stale entry lingers in the game's accessible-cat hash table
- Auto-refresh after an in-game day now re-subscribes the `QFileSystemWatcher` to the save path after Mewgenics atomically renames the file, so subsequent days continue to trigger reloads instead of the watcher going silent after day one
- Added a rotating crash log at `%APPDATA%/MewgenicsBreedingManager/logs/mewgenics.log` that captures unhandled exceptions (main + worker threads) and Qt warnings via `qInstallMessageHandler`. Main-thread crashes now also show a dialog pointing at the log file
- Regenerated `DefinedShapes.zip` from 6,894 → 10,564 shapes, restoring color to the vast majority of cat portraits that previously rendered as black silhouettes. The old zip was built from an incomplete extraction pass that never triggered the GPAK fallback because the cache count was already above the threshold. A handful of black faces remain and are still under investigation
- Silenced spammy `Could not parse stylesheet of object QLabel` warnings that appeared when selecting a cat. `letter-spacing` is not a Qt QSS property; tracking is now applied via `QFont.setLetterSpacing` in `styling._sec()` and the handful of other call sites so the visual result is identical

### v5.4.1

- Fixed Perfect 7 Planner "More Depth" hanging for hours on large rosters (issue #63) — the SA candidate pool is now index-based instead of doing an O(pairs) scan on every neighbor generation
- Fixed Configure Rooms settings being wiped when upgrading to a new version of the program — the panel no longer stamps defaults into the global config during init before a save is selected
- Updated 3 pre-existing tests that drifted from current code behavior (iterative pedigree cycle break, room-level lover exclusivity, renamed "Import Breeding Planner" locale string)

### v5.4.0

- Improved tooltip coverage across all views with full localization support
- Updated onboarding tutorial with cat sprite rendering and shape extraction info
- Updated What's New dialog for v5.4.0 release content
- Added new locale keys for tooltips and dialogs across all 4 languages (en, zh_CN, ru, pl)
- Added tests for shape extraction, localization, configuration, and dialog modules
- Wrapped hardcoded tooltips in `_tr()` across breeding partners, calibration, family tree, room priority, furniture, safe breeding, and cat detail views

### v5.3.1

- Added automatic DefinedShape extraction from `resources.gpak` for cat sprite rendering
- Bundled `DefinedShapes.zip` (16.5 MB) so cloners can render cat sprites without the game installed
- Startup extraction chain: cached PNGs (instant) -> ZIP (~3 s) -> GPAK (~25 s)
- Replaced `.rar` distribution with `.zip` for cross-platform compatibility
- New module: `utils/shape_extractor.py` — SWF shape parsing and Qt-based rendering

### v5.2.0

- Added class-specific mutation trees for `Best Pairs`, `Melee`, `Ranged`, and `Magic`, with per-room mode scoring in the room optimizer
- Moved class stat-priority editing into the room distributor with dedicated class stats controls and recommended reset actions
- Updated mutation-planner trait visibility with clearer effects, softer wanted/avoid coloring, and cleaner mutation descriptions
- Kept Perfect 7 Planner tied to `Best Pairs` mutations only so its imports stay predictable
- Refined persistence, migration, and UI coverage for the new mutation-class workflow

### v5.0.0

- Full codebase refactoring: split monolithic `mewgenics_manager.py` (~19k lines) into a structured `mewgenics/` package
- New package layout: `utils/`, `models/`, `workers/`, `views/`, `panels/`, `dialogs` — 30+ focused modules
- Entry point (`mewgenics_manager.py`) is now a thin wrapper for backwards compatibility
- No feature changes or behavior differences — pure structural refactor
- Updated PyInstaller spec with all new submodule imports

### v4.4.1

- Follow-up release for the same planner, optimizer, localization, and test updates shipped in `v4.4.0`
- Keeps the shared optimizer search settings, deeper room optimizer controls, breeding partner improvements, and planner persistence updates in sync with the latest release line

### v4.4.0

- Added shared optimizer search settings so the room optimizer and Perfect 7 planner use the same simulated annealing controls
- Expanded the room optimizer with deeper search options, clearer setup/configuration tabs, and improved room-related tooltips
- Improved the breeding partners view to distinguish mutual and one-way love links
- Refined the mutation planner so cats are shown alongside selected traits instead of being buried behind room filters
- Updated the saved UI defaults and persistence behavior for the new planner and optimizer settings
- Expanded localization coverage for the new settings, labels, and status messages
- Added and updated tests around planner persistence, optimizer behavior, trait labels, and UI interactions
