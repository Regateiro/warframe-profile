"""Missing Mods sub-command (``mods``).

Compares the player's mod inventory against the complete game mod list
and reports which mods are missing, grouped by in-game category.

Flow:
    1. Load the export DB (all game mods) and inventory (owned mods).
    2. Build owned mods from RawUpgrades + Upgrades + FocusUpgrades.
    3. Map WFCD mod types to in-game categories.
    4. Report missing mods grouped by category with acquisition hints.
"""

from collections import defaultdict

from prettytable import PrettyTable, TableStyle

from warframe_profile.model.drop_data import get_best_source, load_mod_drops
from warframe_profile.model.inventory import (
    ExportDB,
    build_owned_mods,
    load_data,
)
from warframe_profile.model.utils import normalize_path

# Requiem mod names — drop from Kuva Lich / Sister / Coda murmurs
_REQUIEM_NAMES = frozenset({
    "Lohk", "Xata", "Jahu", "Khra", "Vome", "Fass", "Netra", "Oull", "Ris",
})

# In-game category display order
_CATEGORY_ORDER = [
    "Warframe",
    "Aura",
    "Augment",
    "Exilus",
    "Primary",
    "Secondary",
    "Melee",
    "Stance",
    "Robotic",
    "Beast",
    "Archgun",
    "Archmelee",
    "Vehicles",
    "Railjack",
    "Antique",
    "Parazon",
    "Riven",
]

# ---------------------------------------------------------------------------
# Data-driven mod type → game category mapping
# ---------------------------------------------------------------------------

#: WFCD mod type → in-game category (exact match).
_WFCD_TYPE_TO_CATEGORY: dict[str, str] = {
    "Riven Mod": "Riven",
    "Parazon Mod": "Parazon",
    "Arch-Gun Mod": "Archgun",
    "Archwing Mod": "Archgun",
    "Arch-Melee Mod": "Archmelee",
    "K-Drive Mod": "Vehicles",
    "Necramech Mod": "Vehicles",
    "Plexus Mod": "Vehicles",
    "Railjack Mod": "Railjack",
    "Tektolyst Artifact Mod": "Antique",
    "Stance Mod": "Stance",
    "Posture Mod": "Stance",
    "Melee Mod": "Melee",
    "Secondary Mod": "Secondary",
    "Primary Mod": "Primary",
    "Shotgun Mod": "Primary",
    "Mod Set Mod": "Warframe",
    "Peculiar Mod": "Warframe",
}

#: WFCD mod types that should be skipped entirely.
_SKIP_TYPES = {
    "Arch-Gun Riven Mod",
    "Kitgun Riven Mod",
    "Melee Riven Mod",
    "Pistol Riven Mod",
    "Rifle Riven Mod",
    "Shotgun Riven Mod",
    "Zaw Riven Mod",
    "Companion Weapon Riven Mod",
    "Focus Way",
    "Transmutation Mod",
}

#: uniqueName path substrings that identify Warframe sub-categories.
_WARFRAME_SUBPATH_MAP: list[tuple[str, str]] = [
    ("/Aura/", "Aura"),
    ("/Expert/", "Exilus"),
]

#: uniqueName path substrings that identify augment mods.
_AUGMENT_PATH_MARKERS: tuple[str, ...] = ("AugmentCard", "AugmentMod")

#: Keywords used to classify Companion mods as "Beast" type.
_BEAST_KEYWORDS: tuple[str, ...] = (
    "Kubrow", "Kavat", "Charger", "Huras", "Sahasa", "Adarza",
    "Smeeta", "Helminth", "Predasite", "Vulpaphyla", "Moa",
    "Hound", "Zanuka",
)


def _category_sort_key(cat: str) -> int:
    """Return sort order for a category."""
    try:
        return _CATEGORY_ORDER.index(cat)
    except ValueError:
        return len(_CATEGORY_ORDER)


def _game_category(mod: dict) -> str:
    """Map a WFCD mod dict to its in-game category name."""
    wfcd_type = mod.get("type", "")
    un = mod.get("uniqueName", "")
    name = mod.get("name", "")

    # Riven mods
    if "Riven" in wfcd_type:
        return "Riven"

    # Focus Ways and Transmutation
    if wfcd_type in ("Focus Way", "Transmutation Mod"):
        return ""  # should be skipped

    # Companion mods → Robotic or Beast
    if wfcd_type == "Companion Mod":
        lower_un = un.lower()
        lower_name = name.lower()
        if any(k.lower() in lower_un or k.lower() in lower_name for k in _BEAST_KEYWORDS):
            return "Beast"
        return "Robotic"

    # Warframe Mod — split into Warframe / Aura / Augment / Exilus
    if wfcd_type == "Warframe Mod":
        for path_sub, sub_cat in _WARFRAME_SUBPATH_MAP:
            if path_sub in un:
                return sub_cat
        if any(marker in un for marker in _AUGMENT_PATH_MARKERS):
            return "Augment"
        return "Warframe"

    # Direct type → category lookup
    if wfcd_type in _WFCD_TYPE_TO_CATEGORY:
        return _WFCD_TYPE_TO_CATEGORY[wfcd_type]

    return "Warframe"


def _clean_mod_name(mod: dict) -> str:
    """Return a display name for a mod, fixing raw WFCD names."""
    name = mod.get("name", "?")
    # Fix Mod Set mod names (e.g. "Amarsetmod" → "Amar Set")
    if mod.get("type") == "Mod Set Mod" and name.endswith("setmod"):
        return name[: -len("setmod")] + " Set"
    return name


def _is_real_augment(mod: dict) -> bool:
    """Check if a mod is a real augment (has AugmentCard/AugmentMod in path)."""
    un = mod.get("uniqueName", "")
    return mod.get("isAugment") and ("AugmentCard" in un or "AugmentMod" in un)


#: uniqueName path substring → acquisition hint (checked in order).
_PATH_HINT_MAP: list[tuple[str, str]] = [
    ("/PvPMods/", "Conclave (Teshin)"),
    ("/OrokinChallenge/", "Orokin Vaults (Derelict)"),
    ("/Aura/", "Sorties / Nightmare / Transmutation"),
    ("/Expert/", "Various (Syndicates, Sorties, Transmutation)"),
    ("/Immortal/", "Lich / Sister / Coda"),
    ("/DataSpike/Cipher/", "Cephalon Simaris"),
    ("/DataSpike/Assassin/", "Enemy Executions"),
    ("/Set/", "Multiple Sources"),
    ("/Sets/", "Multiple Sources"),
    ("/Beginner/", "New Player Experience"),
]

#: WFCD mod type → acquisition hint (checked when path hints don't match).
_TYPE_HINT_MAP: dict[str, str] = {
    "Mod Set Mod": "Multiple Sources",
    "Peculiar Mod": "Baro Ki'teer",
}

#: Category → acquisition hint (fallback).
_CATEGORY_HINT_MAP: dict[str, str] = {
    "Stance": "Enemy Drop (varies by stance)",
    "Antique": "Zariman",
    "Archgun": "Archwing Missions",
    "Archmelee": "Archwing Missions",
    "Railjack": "Railjack Proxima",
    "Robotic": "Market / Dojo",
    "Beast": "Market / Bred Molth",
}

#: Parazon uniqueName path substrings → hint.
_PARAZON_PATH_HINTS: list[tuple[str, str]] = [
    ("/Immortal/", "Lich / Sister / Coda"),
    ("/DataSpike/Cipher/", "Cephalon Simaris"),
    ("/DataSpike/Assassin/", "Enemy Executions"),
]

#: Vehicles WFCD type → hint.
_VEHICLE_TYPE_HINTS: list[tuple[str, str]] = [
    ("Necramech", "Cambion Drift"),
    ("K-Drive", "Ventkids"),
    ("Plexus", "Railjack Proxima"),
]


def _acquisition_hint(
    mod: dict,
    drop_data: dict[str, list[dict]] | None = None,
) -> str:
    """Return a rough acquisition source hint for a mod."""
    name = mod.get("name", "")
    un = mod.get("uniqueName", "")
    wfcd_type = mod.get("type", "")
    category = _game_category(mod)
    rarity = mod.get("rarity", "")

    # Check drop data first for specific enemy info
    if drop_data:
        drop_hint = get_best_source(name, drop_data)
        if drop_hint:
            return drop_hint

    # Augment mods
    if _is_real_augment(mod):
        return "Syndicate Augment"

    # Path-based hints
    for pattern, hint in _PATH_HINT_MAP:
        if pattern in un:
            return hint

    # Requiem mods → Kuva Lich/Sister/Coda murmurs
    if name in _REQUIEM_NAMES:
        return "Kuva Lich / Sister / Coda Murmurs"

    # Parazon mods → specific sources
    if category == "Parazon":
        for pattern, hint in _PARAZON_PATH_HINTS:
            if pattern in un:
                return hint
        return "Enemy Executions"

    # Primed mods (Baro Ki'teer only)
    if rarity == "Legendary" and "Primed" in name:
        return "Baro Ki'teer"

    # Type-based hints
    if wfcd_type in _TYPE_HINT_MAP:
        return _TYPE_HINT_MAP[wfcd_type]

    # Category-based hints
    if category in _CATEGORY_HINT_MAP:
        return _CATEGORY_HINT_MAP[category]

    # Vehicles → sub-type hints
    if category == "Vehicles":
        for marker, hint in _VEHICLE_TYPE_HINTS:
            if marker in wfcd_type:
                return hint

    # Rarity-based hints
    if rarity == "Legendary":
        return "Rare Drop / Baro Ki'teer"
    if rarity in ("Rare", "Uncommon", "Common"):
        return "Mission Drop / Enemy Drop"

    return "Mission Drop / Enemy Drop"


def analyze_missing_mods(
    db: ExportDB,
    inv: dict,
) -> tuple[dict[str, int], dict[str, list[dict]], dict[str, int], int, dict[str, list[dict]]]:
    """Analyze which mods the player is missing.

    Returns:
        ``(owned_mods, missing_by_category, owned_by_category, total_game_mods, drop_data)``
        where *missing_by_category* maps in-game category → list of missing
        mod dicts, *owned_by_category* maps category → count owned,
        *owned_mods* maps normalised path → quantity, and *drop_data*
        is the loaded mod drop locations from WFCD.
    """
    all_game_mods = [i for i in db.items if i.get("category") == "Mods"]
    game_mod_paths = {normalize_path(m.get("uniqueName", "")) for m in all_game_mods}

    owned_mods = build_owned_mods(inv, game_mod_paths=game_mod_paths)

    # Load mod drop data
    drop_data = load_mod_drops()

    # Deduplicate by name — if multiple entries share a name, keep one.
    # A mod is "owned" if ANY of its path variants is in inventory.
    seen_names: dict[str, dict] = {}
    for mod in all_game_mods:
        name = mod.get("name", "")
        if name in seen_names:
            # Mark owned if any variant is owned
            existing_norm = normalize_path(seen_names[name].get("uniqueName", ""))
            new_norm = normalize_path(mod.get("uniqueName", ""))
            if new_norm in owned_mods and existing_norm not in owned_mods:
                seen_names[name] = mod
        else:
            seen_names[name] = mod
    unique_game_mods = list(seen_names.values())

    # Build owned-by-name set for deduplicated lookup
    owned_names: set[str] = set()
    for mod in unique_game_mods:
        norm = normalize_path(mod.get("uniqueName", ""))
        if norm in owned_mods:
            owned_names.add(mod.get("name", ""))

    missing_by_cat: dict[str, list[dict]] = defaultdict(list)
    owned_by_cat: dict[str, int] = defaultdict(int)

    # Count owned by category
    for name in owned_names:
        mod = seen_names[name]
        cat = _game_category(mod)
        if cat:
            owned_by_cat[cat] += 1

    # Find missing by category
    for mod in unique_game_mods:
        wfcd_type = mod.get("type", "Unknown")
        if wfcd_type in _SKIP_TYPES:
            continue
        cat = _game_category(mod)
        if not cat:
            continue
        if mod.get("name", "") not in owned_names:
            missing_by_cat[cat].append(mod)

    # Sort mods within each category
    for cat in missing_by_cat:
        missing_by_cat[cat].sort(key=lambda m: m.get("name", ""))

    return owned_mods, dict(missing_by_cat), dict(owned_by_cat), len(unique_game_mods), drop_data


def print_missing_mods_report(
    owned_mods: dict[str, int],
    missing_by_cat: dict[str, list[dict]],
    owned_by_cat: dict[str, int],
    total_game_mods: int,
    drop_data: dict[str, list[dict]],
) -> None:
    """Print the missing mods report."""
    total_missing = sum(len(mods) for mods in missing_by_cat.values())
    owned_count = len(owned_mods)
    total_copies = sum(owned_mods.values())

    print("=" * 72)
    print("  MISSING MODS REPORT")
    print("=" * 72)
    print(f"  Owned: {owned_count} unique mods ({total_copies} total copies)")
    print(f"  Missing: {total_missing} mods (excluding rivens, focus, transmutation)")
    print()

    if not missing_by_cat:
        print("  You have all mods!")
        print("=" * 72)
        return

    # Summary table
    summary = PrettyTable(title="Missing Mods by Category")
    summary.field_names = ["Category", "Owned", "Missing"]
    summary.align["Category"] = "l"
    summary.align["Owned"] = "r"
    summary.align["Missing"] = "r"
    summary.sortby = "Missing"
    summary.reversesort = True

    for cat in sorted(missing_by_cat.keys(), key=_category_sort_key):
        o = owned_by_cat.get(cat, 0)
        m = len(missing_by_cat[cat])
        summary.add_row([cat, o, m])

    print(summary)
    print()

    # Detailed tables per category
    sorted_cats = sorted(missing_by_cat.keys(), key=_category_sort_key)
    for cat in sorted_cats:
        mods = missing_by_cat[cat]
        t = PrettyTable(title=f"{cat} ({len(mods)} missing)")
        t.field_names = ["Mod", "Rarity", "Source"]
        t.align["Mod"] = "l"
        t.align["Rarity"] = "l"
        t.align["Source"] = "l"
        t.set_style(TableStyle.DEFAULT)

        for mod in mods:
            t.add_row([
                _clean_mod_name(mod),
                mod.get("rarity", "?"),
                _acquisition_hint(mod, drop_data),
            ])

        print(t)
        print()

    print("=" * 72)


def main(args) -> None:
    """Entry point for the ``mods`` sub-command."""
    db, inv = load_data(
        args.items_cache,
        args.refresh_items,
        args.inventory,
        args.refresh,
    )

    result = analyze_missing_mods(db, inv)
    owned_mods, missing_by_cat, owned_by_cat, total_game_mods, drop_data = result
    print_missing_mods_report(
        owned_mods, missing_by_cat, owned_by_cat, total_game_mods, drop_data
    )
