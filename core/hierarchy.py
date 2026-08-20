"""
Stage 3 of the pipeline: work out who reports to whom, then find the cycles.

Runs on accepted employees only. A row that failed identity validation has no
usable id or email, so it can neither be a manager nor look one up.
"""

from collections import defaultdict
from dataclasses import dataclass

from .types import Employee, ManagerCount, RowError


class Lookups:
    """Two indexes over the accepted employees, built once and reused."""

    def __init__(self, employees: list[Employee]):
        # Both are built from accepted employees only. Rows rejected for a
        # duplicate or missing identity are invisible to manager resolution,
        # so pointing at one reads as "manager not found", which is honest:
        # that person is not being imported.
        self.by_id = {employee.employee_id: employee for employee in employees}
        self.by_email = {employee.email: employee for employee in employees}


def build_lookups(employees: list[Employee]) -> Lookups:
    """Index accepted employees by id and by email for constant-time lookup."""
    return Lookups(employees)


@dataclass(frozen=True)
class Resolution:
    """The reporting graph, plus the roots and the problems found building it."""

    # employee id -> manager's employee id. An employee with a manager problem
    # is simply absent from this map. Every employee has at most one manager,
    # so this is a plain dict, not a dict of lists.
    manager_of: dict[str, str]
    roots: list[Employee]
    errors: list[RowError]


def resolve_managers(employees: list[Employee], lookups: Lookups) -> Resolution:
    """
    Turn each employee's manager_id and manager_email into one reporting edge.

    This is a second pass over the employees on purpose. Rows can appear in any
    order, so a manager may be listed below their report, and no reference can
    be resolved until every employee has been indexed.

    An employee with a manager problem stays ACCEPTED. They are a real person
    who should be imported. But they get no edge and they are not a root:
    calling them a root would tell the user "this person is top of the org",
    which is a different and wrong claim from "we could not place this person".
    """
    manager_of: dict[str, str] = {}
    roots: list[Employee] = []
    errors: list[RowError] = []

    for employee in employees:
        wants_id = bool(employee.manager_id)
        wants_email = bool(employee.manager_email)

        if not wants_id and not wants_email:
            roots.append(employee)
            continue

        by_id = lookups.by_id.get(employee.manager_id) if wants_id else None
        by_email = lookups.by_email.get(employee.manager_email) if wants_email else None

        unresolved = _describe_unresolved(employee, wants_id, by_id, wants_email, by_email)
        if unresolved:
            errors.append(
                RowError(
                    source_row=employee.source_row,
                    kind="manager_not_found",
                    message=(
                        f"Row {employee.source_row}: {employee.employee_name or employee.employee_id} "
                        f"lists a manager that is not in this file ({unresolved}). "
                        "They will be imported without a manager."
                    ),
                )
            )
            continue

        # Both references given and both resolved, but to different people. We
        # cannot pick one, because either choice silently invents an org chart
        # the client did not send us.
        if by_id is not None and by_email is not None and by_id is not by_email:
            errors.append(
                RowError(
                    source_row=employee.source_row,
                    kind="manager_conflict",
                    message=(
                        f"Row {employee.source_row}: {employee.employee_name or employee.employee_id} "
                        f"lists manager_id {employee.manager_id} ({by_id.employee_name}) but "
                        f"manager_email {employee.manager_email} ({by_email.employee_name}). "
                        "These are two different people, so no manager was assigned."
                    ),
                )
            )
            continue

        manager = by_id if by_id is not None else by_email

        # Handled here, before cycle detection, so it is reported as the data
        # entry mistake it is rather than surfacing as a one-person cycle.
        if manager.employee_id == employee.employee_id:
            errors.append(
                RowError(
                    source_row=employee.source_row,
                    kind="self_management",
                    message=(
                        f"Row {employee.source_row}: {employee.employee_name or employee.employee_id} "
                        "is listed as their own manager. They will be imported "
                        "without a manager."
                    ),
                )
            )
            continue

        manager_of[employee.employee_id] = manager.employee_id

    return Resolution(manager_of=manager_of, roots=roots, errors=errors)


def _describe_unresolved(
    employee: Employee,
    wants_id: bool,
    by_id: Employee | None,
    wants_email: bool,
    by_email: Employee | None,
) -> str:
    """Name whichever manager references failed to match anyone, or "" if all matched."""
    missing = []
    if wants_id and by_id is None:
        missing.append(f"manager_id {employee.manager_id}")
    if wants_email and by_email is None:
        missing.append(f"manager_email {employee.manager_email}")
    return " and ".join(missing)


# Per-node marks for the cycle walk. Explicit constants rather than two sets,
# because the whole correctness argument is about which of these three states a
# node is in when we reach it again.
_UNVISITED = 0
_IN_PROGRESS = 1
_DONE = 2


def find_cycle_members(manager_of: dict[str, str]) -> set[str]:
    """
    Return the employees who are ON a reporting cycle.

    Two things make this simple. Every employee has at most one manager, so
    following managers from any node is a single path, never a branching search.
    And that means a node is on a cycle if and only if walking its manager chain
    comes back to that same node.

    So we walk each chain iteratively, marking nodes in progress as we go. When
    the walk meets a node marked in progress, we have closed a loop, and only
    the nodes from that meeting point onwards are on it. Everything walked
    BEFORE the meeting point merely reports into the cycle, which is not the
    same thing and must not be flagged. That is trap rule 3.

    The walk is iterative rather than recursive on purpose. A 100,000 row file
    can hold a manager chain thousands deep, and Python's default recursion
    limit is 1,000, so a recursive version raises RecursionError on exactly the
    file size the brief asks about.
    """
    state: dict[str, int] = {}
    cycle_members: set[str] = set()

    for start in manager_of:
        if state.get(start, _UNVISITED) != _UNVISITED:
            continue

        # The chain walked on this pass, in order, so we can slice off the loop.
        path: list[str] = []

        node = start
        while node is not None and state.get(node, _UNVISITED) == _UNVISITED:
            state[node] = _IN_PROGRESS
            path.append(node)
            node = manager_of.get(node)

        # Stopped because we met a node still in progress on THIS walk, so the
        # chain has closed on itself. The loop is the tail of the path starting
        # at that node. Anything earlier in the path just feeds into the loop.
        if node is not None and state.get(node) == _IN_PROGRESS:
            cycle_members.update(path[path.index(node):])

        # Stopping for any other reason means no new cycle: the chain ran out of
        # managers, or reached a node finished on an earlier walk. In the second
        # case that node's cycle membership was already decided correctly, and
        # reaching it from outside does not change it.

        for walked in path:
            state[walked] = _DONE

    return cycle_members


def count_direct_reports(manager_of: dict[str, str], lookups: Lookups) -> list[ManagerCount]:
    """
    Count how many people resolved to each manager.

    Only people who actually manage someone appear. Listing every employee with
    a count of zero would bury the six real managers in a list of 25 names.

    Sorted by count, largest first, then by name so the order is stable between
    runs of the same file. Stable ordering matters more than it sounds: a user
    comparing two previews should not see rows move for no reason.
    """
    counts: dict[str, int] = defaultdict(int)
    for manager_id in manager_of.values():
        counts[manager_id] += 1

    managers = [
        ManagerCount(manager=lookups.by_id[manager_id], direct_reports=count)
        for manager_id, count in counts.items()
    ]
    managers.sort(key=lambda entry: (-entry.direct_reports, entry.manager.employee_name))
    return managers
