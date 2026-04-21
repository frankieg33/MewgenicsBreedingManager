# Mewgenics Breeding Manager

A Python desktop tool for managing your Mewgenics cats. Reads your save file directly, scores every cat for breeding priority, optimizes room layouts, and helps plan multi-generation lines — all while tracking lineage, inbreeding risk, and trait inheritance.

Current release: `v5.7.9`

If you'd like to support the project, you can [here](https://ko-fi.com/frankieg33).

## Screenshots

### Main Page

![Main Page](Sceenshots/Home%20Screen.png)

### Breeding Optimizer

![Breeding Optimizer](Sceenshots/Room%20Optimizer.png)

### Perfect 7 Planner

![Perfect 7 Planner](Sceenshots/Perfect%207%20Planner.png)

## Features

### Cat Scoring

Two views for evaluating which cats to keep, breed, or cull:

- **Detailed Scoring** — assigns a breed priority score to every cat based on configurable weights: stat rarity, genetic safety, trait ratings, personality, age, and relationships. Includes heatmap mode, scope filtering by room, complex weight rules, and 5 independent profiles for different strategies.
- **Simple Scoring** — assign point values to individual stats, mutations, and traits, then sort by total. Great for targeted goals like "high STR melee cats" or "collect all rare mutations."
- Each view has its own trait-rating profiles (5 slots) stored per save.

### Breeding & Genetics

- Compare any pair with inheritance odds, expected offspring stats, and inbreeding risk
- Full ancestry tracking — generation depth, shared ancestors, coefficient of inbreeding
- Mating Pair Search finds safe breeding partners with compatibility scoring
- Breeding Partners grid shows all pair combinations at a glance

### Room Optimizer

- Assigns cats to rooms to maximize breeding outcomes
- Movement-aware scoring accounts for relocation cost
- Configurable room capacity, type (breeding/fallback/general), and stimulation
- Avoids trait loss from high-Evolution or high-Health rooms
- Routes kittens to fallback rooms until they're old enough to breed

### Perfect 7 Planner

- Plans multi-generation lines toward all-7 stat cats
- Simulated annealing solver finds optimal breeding chains
- Foundation pair selection with depth control

### Other Tools

- Family Tree browser with in-game cat sprite rendering
- Mutation & Disorder Planner for targeting specific traits
- Furniture Viewer showing per-room stat effects
- Live save file watching — auto-refreshes when the game saves
- Ability and mutation descriptions from `resources.gpak`

## Install

```bash
git clone https://github.com/frankieg33/MewgenicsBreedingManager
cd MewgenicsBreedingManager
pip install -r requirements.txt
python src/mewgenics_manager.py
```

On Windows you can also run `run.bat` which auto-installs dependencies on first run.

The app looks for `resources.gpak` in common Steam paths, the configured save root, and the working directory. If it can't find it, you'll be prompted to browse for it.

## Build

```bash
# Windows
build.bat

# Linux
build.sh
```

Produces a standalone executable via PyInstaller.

## Requirements

- Python 3.14+
- PySide6
- lz4
- openpyxl

## Credits

- Save parsing research based on [pzx521521/mewgenics-save-editor](https://github.com/pzx521521/mewgenics-save-editor)
- Community reverse-engineering help from players and mod users
- **Detailed Scoring view** — concept and implementation by [Byron Altice](https://github.com/byronaltice) (ported from his fork)
- PR contributors: [0demongamer0](https://github.com/0demongamer0), [An-on-im](https://github.com/An-on-im), [byronaltice](https://github.com/byronaltice), [heartskingu](https://github.com/heartskingu), [ICaxapl](https://github.com/ICaxapl), [luisMolina95](https://github.com/luisMolina95), [TheMegax](https://github.com/TheMegax)
- Simulated annealing (SA) idea from [PurpleMyst](https://github.com/PurpleMyst/mewgenics_breeding_helper)
- Original idea and reference from frankieg33

## Release Notes

### v5.8.0

Detailed Scoring view — ported and consolidated from the [byronaltice fork](https://github.com/byronaltice/MewgenicsBreedingManager).

- **New Detailed Scoring view** (concept and implementation by [Byron Altice](https://github.com/byronaltice)): weighted breed-priority ranker with 5-slot profiles, per-cat custom Complex Weights rules, a filter dialog, heatmap column coloring, and a current-stats overview popup.
- **Cat Scoring section** in the sidebar: **Simple Scoring** (was Manual Scoring) and **Detailed Scoring**. The previous Automatic Scoring view has been retired — Detailed Scoring supersedes it.
- **Consolidated scoring engine**: Detailed Scoring and Simple Scoring now share `src/mewgenics/scoring/engine.py`. Ports forward the `(group_key, mutation_id)` mutation-bonus dedupe fix and folds class stat modifiers into current-stat readouts.
- **Session persistence**: auto-loads your most recently used save on startup; restores window geometry, splitter sizes, table column widths, and all Detailed Scoring preferences (active profile, weights, Complex Weights, filters, scope, toggles, sort).
- **Fork back-ports**: defect detection via GON block data, distinct mutation stat variants surfaced as separate traits, and a latent nav bug fix so back/forward now correctly returns to Simple/Detailed Scoring.
- **Performance**: Detailed Scoring runs computation on a background thread and defers recompute while hidden.

### v5.7.9

- Guarded `auto_scoring` worker retirement disconnect against already-disconnected or destroyed C++ object state

### v5.7.8

Thread safety hardening and code quality fixes.

- **Data race eliminated**: `BreedingCacheWorker` now snapshots the alive cat list at construction time instead of reading live `cat.status` from the background thread while the main thread may mutate it
- **Worker interruption**: `SaveLoadWorker` and `QuickRoomRefreshWorker` now check `isInterruptionRequested()` so retired workers exit early instead of running to completion
- **Double-retirement guard**: `_retire_worker()` tracks whether a worker was already retired, preventing duplicate `deleteLater` connections
- **API cleanup**: Replaced direct `_cats_by_key` access across class boundaries with public `BreedingCache.refresh_cat_index()` method

### v5.7.6

Crash fix, icon rendering improvements, and UI polish.

- **Crash fix**: Proper QThread worker lifecycle management — superseded workers are now retired with `requestInterruption()` + `deleteLater()` instead of being leaked as zombies, preventing the ~1-minute crash cycle when the game autosaves while MBM is open
- **#90**: Gradient icon colors no longer washed out in table view (reduced Screen composition from 2 passes to 1); detail panel icons fixed at 72px with camelCase word-wrapping labels
- **Profile sprites**: Cat face thumbnails now scale-to-fit instead of fill-and-crop, so ears and chin are no longer cut off
- **UI**: Column header borders now show subtle resize grip indicators

### v5.7.5

Dead cat detection, gradient icon rendering, and adventure heuristic improvements.

- **#89**: Dead cats are now detected via the `death_day` field in the save binary — cats that died but remain in a room are marked "Dead" in the status column and excluded from alive filters
- **#90**: Ability/mutation/passive icons now render with proper gradient fills instead of flat single-color approximations
- **#81**: Improved `has_adventured` heuristic — now requires 4+ abilities in addition to positive stat gains, eliminating nearly all false positives from nightly cat fights

### v5.7.4

Not-adventured override for issue #81.

- **#81**: Added "Toggle Not Adventured" context menu action — right-click cats whose `has_adventured` flag is a false positive (e.g. from nightly fights, not actual adventures) and mark them as not-adventured. The override persists in a `.not_adventured` sidecar file and survives save reloads.

### v5.7.3

Bug fix and mutation planner UX improvements.

- **#85**: Newly unlocked rooms (e.g. Attic) now appear immediately after the game writes the save, without requiring a game restart — `house_unlocks` is now merged with `house_state`/furniture instead of used as a fallback
- **Mutation Planner**: Added "Add Desired" (weight +5) and "Add Undesired" (weight -5) buttons for quick trait targeting; buttons override each other when re-adding an existing trait
- **Mutation Planner**: Added "Remove Trait" button to remove traits highlighted in the left table from the selected list
- **Mutation Planner**: Added sortable "Sel" column to the trait table so selected traits can be grouped together
- **Reset UI to Defaults** no longer destroys user-curated data (selected traits, foundation pairs, offspring selections, room priority config) — only resets layout and search state

### v5.7.2

Bug-fix release addressing issues #81–#84.

- **#81**: `has_adventured` heuristic now only counts positive `stat_mod` values, avoiding false exclusions from Fight Club / Adv Ready caused by negative debuffs or status effects
- **#82**: Main window is now vertically resizable — sidebar wrapped in a scroll area so it adapts to smaller screens
- **#83**: Mutation planner selected traits list now expands properly with the splitter instead of capping at 200px
- **#84**: Manual Scoring no longer loses selected mutations/disorders on restart — fixed initialization order where `set_trait_ratings()` overwrote saved checked lists before `set_cats()` could rebuild them

### v5.7.1

Stability release. Fixes the ~10% crash reported against v5.4.8 that also affected v5.7.0, in which the application would crash when the game wrote to its save while the manager was open (a day passing, a new cat being added, breeding, etc.).

- Closed four independent race / exception gaps in the auto-refresh path:
  - `SaveLoadWorker.run()` now catches every exception and emits a `failed` signal instead of letting the QThread die silently (which had stranded the loading overlay and the `_save_load_worker` reference forever)
  - `QuickRoomRefreshWorker` carries a *generation token*; `MainWindow._on_room_patch` drops stale signals from superseded workers and wraps the body in a try/except that falls back to a reload instead of aborting the event loop
  - `load_save` / cache cleanup no longer call `QThread.terminate()` on in-flight workers (the textbook crash recipe while the thread was mid-SQLite / mid-parse) — superseded workers are discarded by identity check in their finished slots and allowed to finish naturally
  - `QFileSystemWatcher` bursts are debounced with a 250 ms single-shot timer so simultaneous writes from the game collapse into one refresh
- New `retry_transient` helper retries only genuinely transient I/O errors (`sqlite3.OperationalError`, `sqlite3.DatabaseError`, `OSError`, `EOFError`) from the partial-write window; real bugs propagate immediately instead of wasting ~350 ms re-running a doomed parse
- `_on_save_load_failed` only schedules a self-heal retry for transient errors, capped at 3 consecutive attempts so a permanently-broken save cannot spin a busy loop
- **Atomic file writes** — config and sidecar files (blacklist, must-breed, pinned, tags) now use write-then-rename to prevent corruption on crash or power loss
- Consolidated `_active_cat_fingerprint()` helper shared between Perfect Planner and Room Optimizer caches
- Logging for sidecar I/O failures instead of silent `pass`

16 regression tests cover each fix in isolation.

### v5.7.0

- New **Automatic Scoring** view — ranks every cat with a breed priority score based on stat rarity (7rare), genetic safety risk, trait ratings, personality (libido, aggression, sexuality), age, love/hate relationships, and stat sum percentile. Configurable weights, heatmap mode, scope filtering by room, and 5 independent profiles
- New **Trait Ratings** system — rate abilities and mutations as Top Priority, Desirable, Neutral, or Undesirable. Ratings are shared between Automatic and Manual Scoring views via 5-slot profiles with JSON persistence
- Manual Scoring profile dropdown — switch between trait rating profiles directly from the Manual Scoring config panel
- Stats Overview dialog — popup showing stat distribution across all cats
- Background scoring thread — heavy computation runs off the main thread so the UI stays responsive
- Pre-computed scope data — eliminates redundant O(N*S) per-cat work for stat counts, trait counts, and scope stats
- Lazy view computation — Automatic Scoring only computes when the view is visible; scores are cached until cat data changes
- Startup splash screen with progress indicator during shape extraction and save loading
- Comprehensive tooltips for all Automatic Scoring weights, options, display modes, and score columns
- Updated onboarding tutorial with Cat Sorting walkthrough (Automatic and Manual Scoring)

### v5.6.2

- Tier-2 ability support — upgraded passive abilities parsed from save, shown with "+" suffix and green-tinted chips (cherry-picked from byronaltice fork)
- GPAK ability descriptions preferred over hardcoded lookup; multi-language text extraction with BOM-aware decoding
- Generic mutation disambiguation — mutations with identical names now append their stat description
- Tooltip detail deduplication when detail already appears in display name
- Eager view loading — all views build at startup and receive cat data immediately, eliminating tab-switch freezes

### v5.6.0

- Game-accurate compatibility formula (`0.15 * CHA * libido * lover_mult * sexuality_mult`) displayed as a color-coded chip with per-attempt success % in the pair detail panel
- Ability inheritance chances (stimulation-based: first active, second active, passive) shown inline with candidate labels
- Disorder inheritance (15% per parent + inbred disorder roll based on COI) shown as chips in the risk row
- Compatibility integrated into optimizer pipeline — pairs below 5% are rejected early (performance win), quality scores scale with compatibility factor
- Stimulation inheritance weight unclamped to allow negative values per wiki mechanics
- Soft warnings instead of hard blocks for edge-case sexuality pairings; only hard-blocks when both cats have near-zero compatibility
- ? gender cats bypass sexuality scoring entirely (issue #75)
- Manual Scoring: added disorder selectors, cross-cat mutation disambiguation, QCheckBox state fix, filter buttons, undesired mutation persistence fix

### v5.5.0

- New Manual Scoring view — assign configurable point weights to stats, desired/undesirable mutations, inbredness, libido, aggression, passives, spells, and sexuality, then sort and filter cats by total score
- Instant view switching when cat data hasn't changed — generation counter skips redundant rebuilds
- Lazy data propagation — only the visible view receives cat data updates

### v5.4.9

- Lowered optimizer bitmask DP threshold from 24 to 22 cats per room
- Rewrote `_kinship()` from recursive to iterative stack-based evaluation
- Replaced O(V * Depth) generation depth computation with O(V) memoized DFS
- Fixed room button rebuild, quick room refresh re-filtering, and broken test imports

### v5.4.8

- Fixed room config bleeding between save files
- Room Optimizer routes kittens to fallback rooms
- Room Optimizer avoids placing cats with desired mutations into trait-loss rooms
