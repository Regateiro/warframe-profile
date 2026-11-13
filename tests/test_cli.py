"""Tests for the unified CLI parser and its command table."""

import importlib

import pytest

from warframe_profile.__main__ import _COMMAND_TABLE, _build_parser


@pytest.mark.parametrize(
    "argv, command",
    [
        (["ducats", "--refresh"], "ducats"),
        (["craft", "--refresh"], "craft"),
        (["cleanup", "--refresh"], "cleanup"),
        (["ducats", "--refresh-items"], "ducats"),
        (["serve", "--port", "8091"], "serve"),
        (["serve", "--host", "0.0.0.0"], "serve"),
        (["--refresh", "ducats"], "ducats"),
        (["--host", "0.0.0.0", "serve"], "serve"),
        (["--refresh", "ducats", "--refresh-items"], "ducats"),
    ],
)
def test_shared_flags_are_accepted_on_either_side(argv, command):
    args = _build_parser().parse_args(argv)
    assert args.command == command


def test_refresh_after_subcommand():
    assert _build_parser().parse_args(["ducats", "--refresh"]).refresh is True


def test_refresh_before_subcommand_is_preserved():
    args = _build_parser().parse_args(["--refresh", "ducats"])
    assert args.refresh is True
    # An absent subparser flag must not clobber the root value or its default.
    assert args.refresh_items is False


def test_host_after_subcommand():
    assert _build_parser().parse_args(["serve", "--host", "0.0.0.0"]).host == "0.0.0.0"


def test_profile_defaults_to_none():
    assert _build_parser().parse_args(["ducats"]).profile is None


def test_items_cache_default_is_export_db():
    args = _build_parser().parse_args(["ducats"])
    assert args.items_cache.endswith("export_db.json")


@pytest.mark.parametrize("name", sorted(_COMMAND_TABLE))
def test_every_command_target_is_importable(name):
    module_name, function_name = _COMMAND_TABLE[name]
    module = importlib.import_module(module_name)
    assert callable(getattr(module, function_name))
