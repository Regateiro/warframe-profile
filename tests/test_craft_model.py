"""Tests for crafting-plan blueprint visibility and foundry-aware colours."""

from warframe_profile.model.craft_model import CraftNode
from warframe_profile.model.data import Inventory, ItemData
from warframe_profile.view import report
from warframe_profile.view.web.templates import _craft_color, _node_html

_ROOT = "/Lotus/Weapons/Tenno/TestWeapon"
_COMPONENT = "/Lotus/Weapons/Tenno/TestComponent"
_MATERIAL = "/Lotus/Types/Items/MiscItems/OrokinCell"
_MATERIAL_BLUEPRINT = "/Lotus/Types/Recipes/Resources/TestOrokinCellResourceBlueprint"
_ROOT_BLUEPRINT = "/Lotus/Types/Recipes/Weapons/TestWeaponBlueprint"
_COMPONENT_BLUEPRINT = "/Lotus/Types/Recipes/Weapons/TestComponentBlueprint"


def _item_data(*, with_component: bool = False) -> ItemData:
    items = [
        {
            "uniqueName": _ROOT,
            "name": "Test Weapon",
            "category": "Primary",
            "_source": "weapons",
            "masterable": True,
        },
        {
            "uniqueName": _MATERIAL,
            "name": "Orokin Cell",
            "category": "Resources",
            "_source": "resources",
            "masterable": False,
        },
    ]
    recipes = {
        _ROOT_BLUEPRINT: {
            "resultType": _ROOT,
            "ingredients": [
                {
                    "ItemType": _COMPONENT if with_component else _MATERIAL,
                    "ItemCount": 2,
                }
            ],
            "num": 1,
            "consumeOnUse": True,
        }
    }
    if with_component:
        items.append(
            {
                "uniqueName": _COMPONENT,
                "name": "Test Component",
                "category": "Primary",
                "_source": "weapons",
                "masterable": False,
            }
        )
        recipes[_COMPONENT_BLUEPRINT] = {
            "resultType": _COMPONENT,
            "ingredients": [{"ItemType": _MATERIAL, "ItemCount": 2}],
            "num": 1,
            "consumeOnUse": True,
        }

    raw = {"items": items, "recipes": recipes, "dict": {}}
    return ItemData.from_raw(raw)


def test_craft_plan_shows_owned_root_blueprint():
    item_data = _item_data()
    inventory = Inventory(
        {
            "Recipes": [{"ItemType": _ROOT_BLUEPRINT, "ItemCount": 1}],
            "MiscItems": [{"ItemType": _MATERIAL, "ItemCount": 2}],
        },
        item_data,
    )

    plan = item_data.craft_plan(_ROOT, inventory, max_depth=3)

    blueprint = _ROOT_BLUEPRINT.lower()
    assert plan.requirements[blueprint] == {
        "name": "Test Weapon Blueprint",
        "quantity": 1,
        "owned": 1,
    }
    assert [(node.name, node.owned) for node in plan.tree.nodes] == [
        ("Test Weapon Blueprint", 1),
        ("Orokin Cell", 2),
    ]


def test_craft_plan_shows_owned_intermediate_blueprint():
    item_data = _item_data(with_component=True)
    inventory = Inventory(
        {
            "Recipes": [
                {"ItemType": _ROOT_BLUEPRINT, "ItemCount": 1},
                {"ItemType": _COMPONENT_BLUEPRINT, "ItemCount": 1},
            ],
            "MiscItems": [{"ItemType": _MATERIAL, "ItemCount": 2}],
        },
        item_data,
    )

    plan = item_data.craft_plan(_ROOT, inventory, max_depth=3)

    blueprint = _COMPONENT_BLUEPRINT.lower()
    assert plan.requirements[blueprint] == {
        "name": "Test Component Blueprint",
        "quantity": 1,
        "owned": 1,
    }
    assert [(node.name, node.owned) for node in plan.tree.nodes[1].children] == [
        ("Test Component Blueprint", 1),
        ("Orokin Cell", 2),
    ]


def test_craft_tree_marks_pending_required_material_as_crafting():
    item_data = _item_data()
    item_data.recipes[_MATERIAL_BLUEPRINT] = {
        "resultType": _MATERIAL,
        "ingredients": [],
        "num": 1,
        "consumeOnUse": True,
    }
    inventory = Inventory(
        {
            "MiscItems": [{"ItemType": _MATERIAL, "ItemCount": 1}],
            "PendingRecipes": [{"ItemType": _MATERIAL_BLUEPRINT, "ItemCount": 1}],
        },
        item_data,
    )

    tree = item_data.craft_tree(_ROOT, 1, inventory, max_depth=3)

    material = next(node for node in tree.nodes if node.name == "Orokin Cell")
    # ``owned`` still includes the foundry result so the plan does not tell the
    # player to farm another copy, but the node records that one is pending.
    assert (material.owned, material.crafting) == (2, 1)
    assert material.crafting_needed == 1
    assert 'class="cyan"' in _node_html(material)


def test_pending_colours_are_distinct_from_owned_and_partial(monkeypatch):
    monkeypatch.setattr(report, "CYAN", "cyan")
    node = CraftNode("Fieldron", quantity=5, owned=5, crafting=1)

    assert node.crafting_needed == 1
    assert 'class="cyan"' in _node_html(node)

    # Extra foundry copies that are not required keep the normal colour.
    extra = CraftNode("Fieldron", quantity=5, owned=7, crafting=2)
    assert extra.crafting_needed == 0
    assert 'class="green"' in _node_html(extra)

    assert report.craft_color_for_owned(2, 2) == report.GREEN
    assert report.craft_color_for_owned(1, 2) == report.YELLOW
    assert _craft_color(2, 2) == "green"
    assert _craft_color(1, 2) == "yellow"
