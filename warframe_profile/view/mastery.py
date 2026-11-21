"""Mastery terminal view — renders :class:`MasteryReport` as tables."""

from prettytable import PrettyTable, TableStyle

from warframe_profile.model.mastery import MasteryReport, MasteryRow, MasterySection


def _print_section_table(rows: list[MasteryRow]) -> None:
    """Print a flat table for the given rows."""
    t = PrettyTable()
    t.set_style(TableStyle.DEFAULT)
    t.field_names = ["Item", "Category", "MR", "Source"]
    t.align["Item"] = "l"
    t.align["Category"] = "l"
    t.align["MR"] = "r"
    t.align["Source"] = "l"

    for row in rows:
        t.add_row([row.name, row.category, row.mr, row.source])

    print(t)
    print()


def _print_mastery_section(section: MasterySection) -> None:
    """Print one mastery section.

    When ``section.group_by_tier`` is True (default) items are grouped by
    difficulty tier with separate tables.  Otherwise all items are printed
    in one flat table.
    """
    if not section.rows:
        return

    print(f"{'─' * 68}")
    print(f"  {section.header}")
    if section.desc:
        print(f"  {section.desc}")
    print(f"  {section.count} item{'s' if section.count != 1 else ''}  ~{section.mr:,} MR")
    print()

    if not section.group_by_tier:
        _print_section_table(section.rows)
        return

    for group in section.tier_groups:
        print(f"{'─' * 68}")
        print(f"  {section.header} — {group.label}")
        print(f"  {group.desc}")
        print(f"  {group.count} items  ~{group.mr:,} MR")
        print()

        _print_section_table(group.rows)


def print_mastery_report(report: MasteryReport) -> None:
    """Print the full mastery progression report."""
    owned_items = report.sections[0]
    ready_to_build = report.sections[1]
    owned_blueprints = report.sections[2]
    missing_blueprints = report.sections[3]

    print("=" * 68)
    print("  MASTERY PROGRESSION REPORT")
    print("=" * 68)
    print(f"  {report.mastered} / {report.total} masterable items mastered ({report.percent}%)")
    print(
        f"  {owned_items.count} unmastered owned"
        f"  {ready_to_build.count} ready to build"
        f"  {owned_blueprints.count} owned blueprints"
        f"  {missing_blueprints.count} missing blueprints"
        f"  {report.remaining} remaining"
    )
    print(f"  ~{report.total_mr:,} MR available from unmastered items")
    print()

    for section in report.sections:
        _print_mastery_section(section)

    print("=" * 68)
    print(f"  {report.remaining} items remaining  ~{report.total_mr:,} MR to earn")
    print("=" * 68)
