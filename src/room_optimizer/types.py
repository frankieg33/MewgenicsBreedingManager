"""Data models for the room optimizer."""

from dataclasses import dataclass, field
from enum import Enum

from save_parser import Cat


class RoomType(Enum):
    """Room designation types used by the optimizer."""

    BEST_PAIRS = "best_pairs"
    MELEE = "melee"
    RANGED = "ranged"
    MAGIC = "magic"
    FALLBACK = "fallback"
    BREEDING = "best_pairs"  # legacy alias
    GENERAL = "fallback"     # legacy alias
    NONE = "none"

    @property
    def uses_profile(self) -> bool:
        return self in {RoomType.BEST_PAIRS, RoomType.MELEE, RoomType.RANGED, RoomType.MAGIC}

    @property
    def is_pairing_room(self) -> bool:
        return self.uses_profile

    @property
    def mode_key(self) -> str:
        return str(self.value)


@dataclass
class RoomConfig:
    """Configuration for a single room."""

    key: str
    room_type: RoomType
    max_cats: int | None
    base_stim: float = 50.0
    evolution: float = 0.0
    health: float = 0.0

    @property
    def display_name(self) -> str:
        from save_parser import ROOM_DISPLAY

        return ROOM_DISPLAY.get(self.key, self.key)

    @property
    def mode_key(self) -> str:
        return self.room_type.mode_key

    @property
    def uses_profile(self) -> bool:
        return self.room_type.uses_profile


@dataclass
class ScoredPair:
    """A breeding pair with score metadata."""

    cat_a: Cat
    cat_b: Cat
    risk: float
    quality: float


@dataclass
class RoomAssignment:
    """Cats assigned to a room."""

    room: RoomConfig
    cats: list[Cat]
    pairs: list[ScoredPair]
    eternal_youth_cats: list[Cat] = field(default_factory=list)


@dataclass
class OptimizationParams:
    """Optimizer configuration."""

    min_stats: int = 0
    max_risk: float = 10.0
    stimulation: float = 50.0
    maximize_throughput: bool = False
    minimize_variance: bool = True
    avoid_lovers: bool = True
    prefer_low_aggression: bool = True
    prefer_high_libido: bool = True
    mode_family: bool = False
    use_sa: bool = False
    sa_temperature: float = 8.0
    sa_cooling_rate: float = 0.95
    sa_neighbors_per_temp: int = 120
    risk_barrier_lambda: float = 20.0
    move_penalty_weight: float = 0.5
    sa_chains: int = 0  # 0 = auto (min(cpu_count, 4)), 1 = single-chain
    planner_traits: list[dict] = field(default_factory=list)
    mode_profiles: dict[str, dict] = field(default_factory=dict)
    # When True, room-mode stat priority lists are ignored when scoring
    # pairs. Useful when the user is chasing all-7s and has no reason to
    # favor particular stats for any class.
    ignore_stat_priority: bool = False
    # When True, kittens (age < kitten_age_threshold) are routed to fallback
    # rooms instead of breeding rooms, since they cannot breed in-game.
    # Eternal youth cats are excluded from this — they are handled separately.
    send_kittens_to_fallback: bool = False
    kitten_age_threshold: int = 2  # age < 2 = kitten (day 0 or 1)
    # When True, the optimizer penalizes placing cats carrying desired mutations
    # into high-Evolution rooms, and cats carrying desired disorders into
    # high-Health rooms (both can be stripped away by the room effects).
    avoid_trait_loss: bool = False


@dataclass
class OptimizationStats:
    """Summary statistics for an optimization run."""

    total_cats: int
    assigned_cats: int
    total_pairs: int
    breeding_rooms_used: int
    general_rooms_used: int
    avg_pair_quality: float
    avg_risk_percent: float


@dataclass
class OptimizationResult:
    """Final optimizer output."""

    rooms: list[RoomAssignment]
    excluded_cats: list[Cat]
    stats: OptimizationStats


DEFAULT_ROOM_CONFIGS = [
    RoomConfig("Floor1_Large", RoomType.BEST_PAIRS, 6, 50.0),
    RoomConfig("Floor1_Small", RoomType.BEST_PAIRS, 6, 50.0),
    RoomConfig("Floor2_Small", RoomType.BEST_PAIRS, 6, 50.0),
    RoomConfig("Floor2_Large", RoomType.BEST_PAIRS, 6, 50.0),
    RoomConfig("Attic", RoomType.FALLBACK, None, 50.0),
]
