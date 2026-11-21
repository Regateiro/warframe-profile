"""Missing-mod terminal view — renders :class:`ModsReport` as tables."""

from prettytable import PrettyTable, TableStyle

from warframe_profile.model.mods import ModsReport


def print_missing_mods_report(report: ModsReport) -> None:
    """Print the missing mods report."""
    print("=" * 72)
    print("  MISSING MODS REPORT")
    print("=" * 72)
    print(f"  Owned: {report.owned_unique} unique mods ({report.total_copies} total copies)")
    print(f"  Missing: {report.total_missing} mods (excluding rivens, focus, transmutation)")
    print()

    if not report.categories:
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

    for cat in sorted(report.categories, key=lambda c: c.missing, reverse=True):
        summary.add_row([cat.name, cat.owned, cat.missing])

    print(summary)
    print()

    # Detailed tables per category
    for cat in report.categories:
        t = PrettyTable(title=f"{cat.name} ({cat.missing} missing)")
        t.field_names = ["Mod", "Rarity", "Source"]
        t.align["Mod"] = "l"
        t.align["Rarity"] = "l"
        t.align["Source"] = "l"
        t.set_style(TableStyle.DEFAULT)

        for row in cat.rows:
            t.add_row([row.name, row.rarity, row.source])

        print(t)
        print()

    print("=" * 72)
