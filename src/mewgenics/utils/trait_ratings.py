"""Shared trait desirability ratings with 5-slot profiles.

Used by the Simple Scoring (Manual Scoring) view. Persists to a single
JSON sidecar file per save. Detailed Scoring (Breed Priority) has its
own richer per-profile persistence in breed_priority.json.
"""

from __future__ import annotations

import json
import os


class TraitRatings:
    """Trait desirability ratings with 5-slot profile system.

    Rating values:
        2  = Top Priority
        1  = Desirable
        0  = Neutral
        None = Undecided
        -1 = Undesirable
    """

    def __init__(self, path: str):
        self._path = path
        self.active_profile: int = 1
        self.ratings: dict[str, int | None] = {}
        self.profiles: dict[int, dict] = {}
        self._auto_weights: dict[str, float] = {}
        self._auto_options: dict = {}
        self._manual_weights: dict = {}
        self._load()

    # ── Rating access ─────────────────────────────────────────────────────

    def get_rating(self, key: str) -> int | None:
        return self.ratings.get(key)

    def set_rating(self, key: str, value: int | None):
        if value is None:
            self.ratings.pop(key, None)
        else:
            self.ratings[key] = value

    # ── Weight access ─────────────────────────────────────────────────────

    def get_auto_weights(self) -> dict[str, float]:
        return dict(self._auto_weights)

    def set_auto_weights(self, weights: dict[str, float]):
        self._auto_weights = dict(weights)

    def get_auto_options(self) -> dict:
        return dict(self._auto_options)

    def set_auto_options(self, options: dict):
        self._auto_options = dict(options)

    def get_manual_weights(self) -> dict:
        return dict(self._manual_weights)

    def set_manual_weights(self, weights: dict):
        self._manual_weights = dict(weights)

    # ── Profile management ────────────────────────────────────────────────

    def switch_profile(self, slot: int):
        """Auto-save current state to outgoing slot, load incoming slot."""
        if slot == self.active_profile and slot in self.profiles:
            # Reload from saved profile
            self._load_slot(slot)
            return

        # Save current state to outgoing slot
        self.profiles[self.active_profile] = self._serialize_current()

        # Load incoming slot
        self.active_profile = slot
        if slot in self.profiles:
            self._load_slot(slot)
        else:
            # Empty slot — reset to defaults
            self.ratings = {}
            self._auto_weights = {}
            self._auto_options = {}
            self._manual_weights = {}

    def _serialize_current(self) -> dict:
        return {
            "ratings": dict(self.ratings),
            "auto_weights": dict(self._auto_weights),
            "auto_options": dict(self._auto_options),
            "manual_weights": dict(self._manual_weights),
        }

    def _load_slot(self, slot: int):
        data = self.profiles.get(slot, {})
        self.ratings = dict(data.get("ratings", {}))
        self._auto_weights = dict(data.get("auto_weights", {}))
        self._auto_options = dict(data.get("auto_options", {}))
        self._manual_weights = dict(data.get("manual_weights", {}))

    # ── Persistence ───────────────────────────────────────────────────────

    def save(self):
        """Write current state to JSON file."""
        # Save current into its profile slot before writing
        self.profiles[self.active_profile] = self._serialize_current()
        data = {
            "active_profile": self.active_profile,
            "profiles": {str(k): v for k, v in self.profiles.items()},
        }
        try:
            os.makedirs(os.path.dirname(self._path) or ".", exist_ok=True)
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception:
            pass

    def _load(self):
        if not os.path.exists(self._path):
            return
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return
        if not isinstance(data, dict):
            return

        self.active_profile = data.get("active_profile", 1)
        raw_profiles = data.get("profiles", {})
        self.profiles = {}
        for k, v in raw_profiles.items():
            try:
                self.profiles[int(k)] = v
            except (ValueError, TypeError):
                pass

        # Load active profile's data into working state
        if self.active_profile in self.profiles:
            self._load_slot(self.active_profile)
