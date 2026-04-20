"""Stat resolution for scoring — base, total, or total+mutation modes."""

import re

_MUT_STAT_RE = re.compile(r'([+-]?\d+)\s+(STR|CON|INT|DEX|SPD|LCK|CHA)')


def get_mutation_stat_bonuses(cat) -> dict[str, int]:
    """Return {stat_name: total_delta} from visual mutation detail fields.

    Each unique (group_key, mutation_id) pair is counted only once — paired
    body-part slots (e.g. arm_L/arm_R) that share the same mutation within
    the same group represent a single mutation and collapse to one bonus.
    Mutation IDs are reused across groups, so keying on mutation_id alone
    would silently drop legitimately-distinct mutations.
    """
    bonuses: dict[str, int] = {}
    seen: set[tuple[str, int]] = set()
    for entry in getattr(cat, 'visual_mutation_entries', []) or []:
        mutation_id = entry.get('mutation_id')
        # Only dedupe entries that actually carry a mutation_id. Entries
        # without one (e.g. test fixtures) are treated as distinct.
        if mutation_id is not None:
            key = (str(entry.get('group_key') or ''), int(mutation_id))
            if key in seen:
                continue
            seen.add(key)
        detail = entry.get('detail', '') or ''
        for match in _MUT_STAT_RE.finditer(detail):
            delta = int(match.group(1))
            stat = match.group(2)
            bonuses[stat] = bonuses.get(stat, 0) + delta
    return bonuses


def get_cat_stats(cat, use_current: bool, add_mutation_stats: bool = False) -> dict[str, int]:
    """Return the stat dict to use for scoring.

    use_current=True  -> total_stats (base + modifiers/injuries)
    use_current=False -> base_stats
    add_mutation_stats -> parse mutation detail fields and add on top
    """
    if use_current:
        source = getattr(cat, 'total_stats', None) or getattr(cat, 'base_stats', {}) or {}
    else:
        source = getattr(cat, 'base_stats', {}) or {}

    if not add_mutation_stats:
        return source

    bonuses = get_mutation_stat_bonuses(cat)
    if not bonuses:
        return source
    result = dict(source)
    for stat, delta in bonuses.items():
        if stat in result:
            result[stat] = result[stat] + delta
    return result
