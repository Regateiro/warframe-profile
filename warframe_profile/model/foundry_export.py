"""Build the import file accepted by https://warframe-foundry.app/.

The Warframe Foundry is a browser-based mastery / resource tracker.  It
imports a single JSON blob (downloaded from its "Export Data" button) with
this shape (format version 2):

.. code-block:: json

    {
      "items": {
        "data": {
          "<uniqueKey>": {
            "built": true,
            "mastered": true,
            "<componentUniqueKey>": 2
          }
        }
      },
      "resources": {
        "<resourceUniqueKey>": 12345
      },
      "settings": {
        "missionCount": 0, "junctionCount": 0,
        "steelMissionCount": 0, "steelJunctionCount": 0,
        "intrinsicsCount": 0
      },
      "version": 2
    }

Items and components are keyed by their ``uniqueKey`` — the final path
segment of the item's WFCD/DE unique name (e.g.
``/Lotus/Powersuits/Excalibur/Excalibur`` → ``Excalibur``).  This module
turns the player's DE inventory into that exact format via
:func:`build_foundry_export`.
"""

from warframe_profile.model.inventory import (
    EQUIPMENT_SECTIONS,
    ExportDB,
    build_owned,
)
from warframe_profile.model.utils import normalize_path

#: The Warframe Foundry data-format version this exporter writes.
FOUNDRY_VERSION = 2

#: Path prefix identifying material/resources (tracked via the ``resources``
#: section instead of per-item component counts).
_RESOURCE_PATH_PREFIX = "/lotus/types/items/"

#: The Warframe Foundry's tracked resources, keyed by ``uniqueKey`` (the
#: final path segment of the item's unique name).  Extracted from the
#: Foundry's ``resources.resourceKeys`` map (157 entries).  The export only
#: includes owned resources the Foundry actually tracks, so nothing is
#: silently dropped or ignored on import.
_FOUNDRY_RESOURCE_KEYS = frozenset(
    (
        "1999ResourceCommonA",
        "1999ResourceCommonB",
        "1999ResourceRareA",
        "1999ResourceUncommonA",
        "Alertium",
        "AlloyPlate",
        "ArachnoidHungerItem",
        "ArachnoidMicroidItem",
        "ArachnoidWraithItem",
        "ArgonCrystal",
        "BardQuestSequencerItem",
        "BioComponent",
        "BirdOfPreyPartItem",
        "BothCommonFishAPartItem",
        "BothCommonFishBPartItem",
        "BothRareFishAPartItem",
        "CarbidesRailjackItem",
        "CetusWispItem",
        "ChemComponent",
        "Circuits",
        "CommonGemACutAItem",
        "CommonGemBCutAItem",
        "CommonOreAAlloyAItem",
        "CommonOreBAlloyBItem",
        "ConcentratedGas",
        "ControlModule",
        "CoolantItem",
        "CorpusFishExaBrainItem",
        "CorpusFishMuonBatteryItem",
        "CorpusFishParralelBiodeItem",
        "CorpusFishRotobladeItem",
        "CorpusFishThermalLaserItem",
        "CorpusFishVenedoCaseItem",
        "CorpusWidgetAItem",
        "CorpusWidgetBItem",
        "CorpusWidgetCItem",
        "Cryotic",
        "DayCommonFishAPartItem",
        "DayCommonFishBPartItem",
        "DayCommonFishCPartItem",
        "DayUncommonFishAPartItem",
        "DayUncommonFishBPartItem",
        "DeimosCommonOreAAlloyItem",
        "DeimosCommonOreBAlloyItem",
        "DeimosEidolonGemACutItem",
        "DeimosEidolonGemBCutItem",
        "DeimosGenericSharedFishPartItem",
        "DeimosInfestedFishBPartItem",
        "DeimosInfestedFishEPartItem",
        "DeimosInfestedFishFPartItem",
        "DeimosInfestedFishGPartItem",
        "DeimosOrokinFishBPartItem",
        "DeimosOrokinFishCPartItem",
        "DeimosRareGemACutItem",
        "DeimosRareOreAAlloyItem",
        "DeimosUncommonGemACutItem",
        "DeimosUncommonOreAAlloyItem",
        "DuviriDragonDropItem",
        "DuviriEnemyDropItem",
        "DuviriFractalItem",
        "DuviriMurmurItemA",
        "DuviriMushroomItem",
        "DuviriPlantItemA",
        "DuviriPlantItemB",
        "DuviriPlantItemC",
        "DuviriPlantItemD",
        "DuviriPlantItemE",
        "DuviriPlantItemF",
        "DuviriProcessedItem",
        "DuviriRockItem",
        "EclipseVoidWraithItem",
        "EidolonFishOilItem",
        "EidolonFishScalesItem",
        "EidolonFruitItem",
        "EidolonGemACutAItem",
        "Eidolonium",
        "EnergyComponent",
        "EntratiLabDogTagCommon",
        "EntratiLabDogTagRare",
        "EntratiLabMiscItemA",
        "EntratiLabMiscItemB",
        "EntratiWispItem",
        "Ferrite",
        "ForestRodentPartItem",
        "Forma",
        "FungusHeartItem",
        "Gallium",
        "GenericDuviriFishPartAItem",
        "GenericDuviriFishPartCItem",
        "GenericFishScrapItem",
        "GrnFlameSpearPart",
        "GrokdrulItem",
        "InfGorgaricusSeedItem",
        "InfMapricoFruitItem",
        "IraditeItem",
        "IsosRailjackItem",
        "Kuva",
        "Morphic",
        "Nanospores",
        "NarmerBountyResource",
        "NecromechPartChassisItem",
        "NecromechPartEngineItem",
        "NecromechPartSystemsItem",
        "NecromechPartWeaponPodItem",
        "NeuralSensor",
        "Neurode",
        "NightLegendaryFishAPartItem",
        "NightRareFishAPartItem",
        "NightRareFishBPartItem",
        "NistlebrushItem",
        "NullstonesRailjackItem",
        "OrokinCell",
        "OrokinFishEntroplasmaItem",
        "OrokinFishSaganModuleItem",
        "OxiumAlloy",
        "Plastids",
        "PolymerBundle",
        "PrimeBucks",
        "QuillsDogTag",
        "QuillsUncommonDogTag",
        "RareGemACutAItem",
        "RareOreAAlloyAItem",
        "Rubedo",
        "Salvage",
        "SentientFragmentLootItem",
        "SentientShardCommonItem",
        "SeriglassShard",
        "ShrineFragment",
        "SolarisCommonGemACutItem",
        "SolarisCommonGemBCutItem",
        "SolarisCommonOreAAlloyItem",
        "SolarisCommonOreBAlloyItem",
        "SolarisEidolonGemACutItem",
        "SolarisEidolonGemBCutItem",
        "SolarisFishDissipatorCoilItem",
        "SolarisFishLatheCoogulantItem",
        "SolarisFishNeuralRelayItem",
        "SolarisRareGemACutItem",
        "SolarisRareOreAAlloyItem",
        "SolarisUncommonGemACutItem",
        "SolarisUncommonOreAAlloyItem",
        "SpecterParticleResidue",
        "Tellurium",
        "ThanomechPartChassisItem",
        "ThanomechPartEngineItem",
        "ThanomechPartSystemsItem",
        "ThanomechPartWeaponPodItem",
        "TitaniumRailjackItem",
        "UncommonGemACutAItem",
        "UncommonOreAAlloyAItem",
        "VenusCoconutItem",
        "VenusTreeItem",
        "VoidAngelItem",
        "VoidWraithItem",
        "ZarimanDogTagBounty",
        "ZarimanMiscItemA",
        "ZarimanMiscItemB",
    )
)

#: Mastery points awarded by the Foundry per mastered item.  Warframes and
#: companions (pets + sentinels) grant 6000, everything else 3000 (mirrors
#: the Foundry's per-module ``masteryPoints`` getters — defaults with no
#: per-item override).
_MASTERY_6000_CATEGORIES = ("Warframes", "Pets", "Sentinels")
_MASTERY_6000_CATEGORY_SET = frozenset(_MASTERY_6000_CATEGORIES)

#: Per-feature mastery points used by the Foundry's ``settings`` module.
#: Mission / junction / intrinsics contributions are scaled approximations
#: chosen by the Foundry author (63/1000/1500 points per unit).
_MASTERY_PER_MISSION = 63
_MASTERY_PER_JUNCTION = 1000
_MASTERY_PER_INTRINSIC = 1500

#: Upper bounds of the Foundry's settings sliders (the Foundry clamps its UI
#: to these; values beyond them are ignored on import).
_SETTINGS_LIMITS = {
    "missionCount": 232,
    "junctionCount": 13,
    "steelMissionCount": 232,
    "steelJunctionCount": 13,
    "intrinsicsCount": 90,
}

#: Single-pass affinity (XP) needed to fully rank an item, per the game's
#: ``1000 * rank**2`` affinity curve: rank 30 warframes / companions /
#: archwings reach 900,000 and rank 30 weapons 450,000, while rank-40 items
#: cost more (800,000 for adversary weapons, 1,600,000 for necramechs).
#: Lifetime XP keeps accumulating across forma cycles, so an item is only
#: "mastered" once its total XP reaches its single-pass figure.
_MASTERY_XP_NECRAMECH = 1_600_000
_MASTERY_XP_ADVERSARY_WEAPON = 800_000
_MASTERY_XP_RANK30_CORE = 900_000
_MASTERY_XP_RANK30_WEAPON = 450_000

#: Categories whose items follow the 900,000 XP (rank 30, 200 MR/level) curve.
_MASTERY_XP_CORE_CATEGORIES = ("Warframes", "Archwing", "Sentinels", "Pets")

#: Name prefixes of Kuva / Tenet / Coda adversary weapons (rank 40).  Their
#: DE unique names are internal codenames (e.g. ``CrpBigSlash`` for the Kuva
#: Kronsh), so the display name is the reliable signal; Paracesis is the one
#: exception and is matched by its ``BallasSword`` unique-name segment.
_LICH_WEAPON_PREFIXES = ("Kuva ", "Tenet ", "Coda ", "Dual Coda ")


def mastery_xp_threshold(unique_name: str, item: dict | None) -> int:
    """Return the XP an item must reach before the Foundry counts it mastered.

    *unique_name* is the item's DE/WFCD unique name; *item* is the matching
    export-database entry (used for its category / display name), or ``None``
    if unknown.
    """
    if "EntratiMech" in unique_name or (item or {}).get("productCategory") == "MechSuits":
        return _MASTERY_XP_NECRAMECH
    name = (item or {}).get("name", "")
    if name.startswith(_LICH_WEAPON_PREFIXES) or "BallasSword" in unique_name:
        return _MASTERY_XP_ADVERSARY_WEAPON
    category = (item or {}).get("category")
    if category in _MASTERY_XP_CORE_CATEGORIES:
        return _MASTERY_XP_RANK30_CORE
    return _MASTERY_XP_RANK30_WEAPON


def foundry_key(unique_name: str) -> str:
    """Return the Warframe Foundry ``uniqueKey`` for a ``uniqueName``.

    The Foundry keys every item and component by the final path segment of
    its unique name (e.g. ``/Lotus/Powersuits/Excalibur/Excalibur`` →
    ``Excalibur``).  Case is preserved to match the Foundry's item list.
    """
    return unique_name.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]


def _is_resource_component(unique_name: str) -> bool:
    """Return ``True`` if *unique_name* points at a material/resource.

    Resources live under ``/Lotus/Types/Items/`` (Orokin Cell, Argon
    Crystal, credits, etc.) and are tracked in the Foundry's resource
    section rather than as build components.
    """
    return normalize_path(unique_name).startswith(_RESOURCE_PATH_PREFIX)


def _derive_settings(inventory: dict) -> dict:
    """Derive the Foundry ``settings`` counts from the raw inventory.

    Sources in the DE profile export:

    * ``Missions`` — one entry per distinct star-chart node / junction the
      player has cleared (tags like ``SolNode<id>`` for nodes and
      ``<Planet><Planet>Junction`` for junctions).
    * ``PlayerSkills`` — ``LPS_*`` keys hold the player's intrinsics ranks
      (Railjack + Drifter), one point per rank.

    Steel Path progress is not exposed in the inventory export, so
    ``steelMissionCount`` / ``steelJunctionCount`` default to 0 (the caller
    can override them).
    """
    tags = {
        str(m.get("Tag", ""))
        for m in (inventory.get("Missions") or [])
        if m.get("Tag")
    }
    sol_nodes = {t for t in tags if t.startswith("SolNode")}
    junctions = {t for t in tags if t.endswith("Junction")}
    skills = inventory.get("PlayerSkills") or {}
    intrinsics = sum(
        value for key, value in skills.items() if key.startswith("LPS_")
    )
    return {
        "missionCount": min(_SETTINGS_LIMITS["missionCount"], len(sol_nodes)),
        "junctionCount": min(_SETTINGS_LIMITS["junctionCount"], len(junctions)),
        "steelMissionCount": 0,
        "steelJunctionCount": 0,
        "intrinsicsCount": min(_SETTINGS_LIMITS["intrinsicsCount"], intrinsics),
    }


def _merged_settings(inventory: dict, settings: dict | None) -> dict:
    """Return the Foundry settings for *inventory*, overridden by *settings*.

    Overrides are clamped to the Foundry's slider limits so the import can
    never exceed what the Foundry UI accepts.
    """
    merged = _derive_settings(inventory)
    for key, limit in _SETTINGS_LIMITS.items():
        if settings and settings.get(key) is not None:
            merged[key] = max(0, min(limit, int(settings[key])))
    return merged


def steel_settings_from_xp(steel_xp: int) -> dict:
    """Split a Steel Path mastery XP total into Foundry mission/junction counts.

    Steel Path nodes grant the same ~63 mastery points as normal star-chart
    nodes and Steel Path junctions grant 1000, so a single XP total fully
    determines the Foundry ``settings`` contribution.  Returns the pair of
    ``steelMissionCount`` / ``steelJunctionCount`` (within the Foundry's
    limits) that reproduces *steel_xp* most closely.
    """
    best: tuple[int, int, int] | None = None  # (error, missions, junctions)
    for junctions in range(_SETTINGS_LIMITS["steelJunctionCount"] + 1):
        remaining = steel_xp - _MASTERY_PER_JUNCTION * junctions
        missions = min(
            _SETTINGS_LIMITS["steelMissionCount"],
            max(0, round(remaining / _MASTERY_PER_MISSION)),
        )
        points = _MASTERY_PER_MISSION * missions + _MASTERY_PER_JUNCTION * junctions
        error = abs(points - steel_xp)
        if best is None or error < best[0]:
            best = (error, missions, junctions)
    return {
        "steelMissionCount": best[1],
        "steelJunctionCount": best[2],
    }


def _db_categories_by_foundry_key(db: ExportDB) -> dict[str, set[str]]:
    """Return (and memoise on *db*) foundry key → item categories."""
    cached = getattr(db, "_foundry_categories_by_key", None)
    if cached is None:
        cached = {}
        for item in db.items:
            un = item.get("uniqueName")
            if un:
                cached.setdefault(foundry_key(un), set()).add(item.get("category") or "")
        db._foundry_categories_by_key = cached
    return cached


def _db_items_by_un(db: ExportDB) -> dict[str, dict]:
    """Return (and memoise on *db*) uniqueName → item."""
    cached = getattr(db, "_foundry_items_by_un", None)
    if cached is None:
        cached = {item.get("uniqueName", ""): item for item in db.items}
        db._foundry_items_by_un = cached
    return cached


def _item_mastery_points(db: ExportDB, items_data: dict[str, dict]) -> int:
    """Mastery points the Foundry would award for the mastered items.

    Mirrors the Foundry's per-module ``masteryPoints`` getters: warframes and
    companions grant 6000 points each, every other item 3000.  A key is
    treated as a 6000-point item if *any* db item sharing that key belongs to
    a 6000-point category (e.g. the ``Frost``/``Glass`` keys collide with
    texture-skin items but are warframes to the Foundry).
    """
    categories_by_key = _db_categories_by_foundry_key(db)
    return sum(
        6000
        if categories_by_key.get(key, set()) & _MASTERY_6000_CATEGORY_SET
        else 3000
        for key, entry in items_data.items()
        if entry.get("mastered")
    )


def foundry_mastery_points(
    db: ExportDB, items_data: dict[str, dict], settings: dict
) -> int:
    """Total mastery points the Foundry would compute for this export."""
    return (
        _item_mastery_points(db, items_data)
        + _MASTERY_PER_MISSION * settings["missionCount"]
        + _MASTERY_PER_JUNCTION * settings["junctionCount"]
        + _MASTERY_PER_MISSION * settings["steelMissionCount"]
        + _MASTERY_PER_JUNCTION * settings["steelJunctionCount"]
        + _MASTERY_PER_INTRINSIC * settings["intrinsicsCount"]
    )


def mastery_rank(points: int) -> int:
    """Return the Mastery Rank the Foundry displays for *points*.

    The Foundry's rank table follows ``required = 2500 * rank ** 2``; the
    displayed rank is the highest one whose requirement is met.
    """
    rank = 0
    while points >= 2500 * rank * rank:
        rank += 1
    return rank - 1


def build_foundry_export(
    db: ExportDB, inventory: dict, settings: dict | None = None
) -> dict:
    """Build the Warframe Foundry import dict from *db* and *inventory*.

    Flow:
        1. Owned equipment → ``built`` (+ ``mastered`` when the item's total
           XP reaches its single-pass max — see :func:`mastery_xp_threshold`).
        2. ``XPInfo`` (profile history) → ``mastered``, even for items already
           sold, subject to the same XP threshold.
        3. Owned blueprints / parts → per-item component counts (non-resource
           components only).
        4. Owned resources → the ``resources`` map keyed by resource key.
        5. ``settings`` — mission / junction / intrinsics counts derived from
           the inventory, with *settings* applied as overrides.

    Returns:
        A dict ready to serialise as ``warframe-foundry.json``.
    """
    items: dict[str, dict] = {}
    owned = build_owned(inventory, db.recipes)
    db_by_un = _db_items_by_un(db)

    # Highest lifetime XP recorded for each normalised item path, across every
    # source (equipment slots, XPInfo history, and owned misc/recipes).  An
    # item counts as mastered only when this total reaches its single-pass
    # max — XPInfo presence alone would wrongly mark partially-ranked items
    # (e.g. a rank-40 necramech that has only earned 460k of its 1.6M XP).
    max_xp: dict[str, int] = {}
    for xp in inventory.get("XPInfo") or []:
        un = xp.get("ItemType", "")
        if un:
            path = normalize_path(un)
            max_xp[path] = max(max_xp.get(path, 0), xp.get("XP") or 0)

    def _mastered(un: str) -> bool:
        item = db_by_un.get(un)
        return max_xp.get(normalize_path(un), 0) >= mastery_xp_threshold(un, item)

    # 1. Equipment currently owned.
    for section in EQUIPMENT_SECTIONS:
        for eq in inventory.get(section, []):
            un = eq.get("ItemType", "")
            if not un:
                continue
            entry = items.setdefault(foundry_key(un), {})
            entry["built"] = True
            if _mastered(un):
                entry["mastered"] = True

    # 2. Items ever ranked up (mastered-and-sold copies are lost here).
    for xp in inventory.get("XPInfo") or []:
        un = xp.get("ItemType", "")
        if un and _mastered(un):
            items.setdefault(foundry_key(un), {})["mastered"] = True

    # 3. Owned blueprints / parts → per-item component counts.
    for item in db.items:
        components = item.get("components")
        if not components:
            continue
        entry = items.get(foundry_key(item.get("uniqueName", "")))
        if entry is None:
            # Only create a "partial" entry when we own at least one part.
            if not any(owned.get(normalize_path(c.get("uniqueName", "")), 0) for c in components):
                continue
            entry = items[foundry_key(item.get("uniqueName", ""))] = {}
        for comp in components:
            cun = comp.get("uniqueName", "")
            if not cun or _is_resource_component(cun):
                continue
            count = owned.get(normalize_path(cun), 0)
            if count:
                entry[foundry_key(cun)] = min(count, comp.get("itemCount", count))

    # 4. Resources owned (only resources the Foundry actually tracks).
    resources: dict[str, int] = {}
    for misc in inventory.get("MiscItems", []):
        un = misc.get("ItemType", "")
        if un:
            key = foundry_key(un)
            if key in _FOUNDRY_RESOURCE_KEYS:
                resources[key] = misc.get("ItemCount", 1)

    return {
        "items": {"data": items},
        "resources": resources,
        "settings": _merged_settings(inventory, settings),
        "version": FOUNDRY_VERSION,
    }
