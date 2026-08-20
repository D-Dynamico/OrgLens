"""
The data contract every stage of the pipeline speaks.

These are plain dataclasses with no Django imports, which is what lets the
parsing and hierarchy logic be tested by handing bytes to a function instead
of driving a browser.
"""

from dataclasses import dataclass, field


class InvalidHRISFile(Exception):
    """
    Raised when the upload is not usable as an HRIS export at all.

    This is for whole-file problems (empty, not UTF-8, missing headers), not
    for bad individual rows. Bad rows become RowError and still get reported.
    The web layer catches this one exception and shows the message as-is, so
    every message here has to be readable by a non-engineer.
    """


@dataclass(frozen=True)
class Employee:
    """One accepted row, after normalization."""

    employee_id: str
    employee_name: str
    email: str
    manager_id: str
    manager_email: str
    department: str
    # 1-based line number in the uploaded file, header counted as line 1.
    # Kept so every message can point the user at a line they can actually find.
    source_row: int


@dataclass(frozen=True)
class RowError:
    """One problem found on one row."""

    source_row: int
    # One of: duplicate_id, duplicate_email, missing_required,
    # manager_not_found, manager_conflict, self_management
    kind: str
    message: str


@dataclass(frozen=True)
class ManagerTeam:
    """A manager and the people who resolved to them."""

    manager: Employee
    # The people themselves, not just a tally. Someone checking an import needs
    # to see WHO ended up under a manager, because a plausible looking count can
    # still be the wrong five people.
    reports: list[Employee]

    @property
    def direct_reports(self) -> int:
        return len(self.reports)


@dataclass(frozen=True)
class ImportPreview:
    """Everything the result page renders. The single return value of the core."""

    total_rows: int
    employees: list[Employee] = field(default_factory=list)
    errors: list[RowError] = field(default_factory=list)
    roots: list[Employee] = field(default_factory=list)
    managers: list[ManagerTeam] = field(default_factory=list)
    cycle_members: list[Employee] = field(default_factory=list)

    @property
    def accepted_count(self) -> int:
        return len(self.employees)

    @property
    def error_count(self) -> int:
        return len(self.errors)
