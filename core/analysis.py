"""
Stage 4: the one function the rest of the world calls.

`analyze_csv` is the entire public surface of the core. The web layer imports
this and nothing else, which is what keeps CSV details out of views.py and
keeps the pipeline testable by handing it bytes.
"""

from .hierarchy import (
    build_lookups,
    count_direct_reports,
    find_cycle_members,
    resolve_managers,
)
from .parsing import parse
from .types import Employee, ImportPreview


def analyze_csv(raw: bytes) -> ImportPreview:
    """
    Turn an uploaded HRIS export into everything the preview page shows.

    Raises InvalidHRISFile if the upload is not usable as a CSV export at all.
    Problems with individual rows come back inside the preview as errors, not
    as exceptions, because the user should still see the rows that were fine.
    """
    total_rows, employees, parse_errors = parse(raw)

    lookups = build_lookups(employees)
    resolution = resolve_managers(employees, lookups)
    cycle_ids = find_cycle_members(resolution.manager_of)
    managers = count_direct_reports(resolution.manager_of, lookups)

    # One list, in file order, so the user reads problems against the file in
    # front of them rather than grouped by which stage happened to find them.
    errors = sorted(
        parse_errors + resolution.errors,
        key=lambda error: error.source_row,
    )

    return ImportPreview(
        total_rows=total_rows,
        employees=employees,
        errors=errors,
        roots=_by_name(resolution.roots),
        managers=managers,
        cycle_members=_by_name(
            [employee for employee in employees if employee.employee_id in cycle_ids]
        ),
    )


def _by_name(employees: list[Employee]) -> list[Employee]:
    """Sort for display. Same file in, same page out, every time."""
    return sorted(employees, key=lambda employee: (employee.employee_name, employee.employee_id))
