"""
Ground truth for the supplied sample_hris.csv.

The other two test files use small made-up files that isolate one rule each.
This one pins the whole supplied file, so a change that quietly alters the
result of a real export cannot pass unnoticed.
"""

import pathlib

import pytest

from core.analysis import analyze_csv

SAMPLE = pathlib.Path(__file__).resolve().parent.parent / "sample_hris.csv"


@pytest.fixture(scope="module")
def preview():
    return analyze_csv(SAMPLE.read_bytes())


def test_every_row_is_accepted(preview):
    assert preview.total_rows == 25
    assert preview.accepted_count == 25


def test_avery_morgan_is_the_only_root(preview):
    assert [employee.employee_id for employee in preview.roots] == ["DIV-1001"]


def test_the_research_cycle_is_found(preview):
    assert {employee.employee_id for employee in preview.cycle_members} == {
        "DIV-1701",
        "DIV-1702",
        "DIV-1703",
    }


def test_the_two_manager_problems_are_reported(preview):
    assert [(error.source_row, error.kind) for error in preview.errors] == [
        (10, "manager_not_found"),  # DIV-1600 Casey Bell points at DIV-9999
        (21, "manager_conflict"),   # DIV-1601 Riley Cooper names two people
    ]


def test_the_two_problem_rows_are_still_imported_but_are_not_roots(preview):
    """Trap rule 2, checked against the real file rather than a fixture."""
    problem_ids = {"DIV-1600", "DIV-1601"}
    accepted = {employee.employee_id for employee in preview.employees}
    roots = {employee.employee_id for employee in preview.roots}

    assert problem_ids <= accepted
    assert problem_ids.isdisjoint(roots)


def test_a_manager_conflict_costs_the_named_manager_a_direct_report(preview):
    """
    Riley Cooper names Priya Shah by id, but the conflict means no edge is
    created, so Priya shows one report (Sofia Chen) rather than two.
    """
    priya = next(
        entry for entry in preview.managers if entry.manager.employee_id == "DIV-1100"
    )

    assert priya.direct_reports == 1


def test_reporting_relationships_add_up(preview):
    total_reports = sum(entry.direct_reports for entry in preview.managers)

    # 25 employees, minus one root and two unplaced, leaves 22 with a manager.
    assert total_reports == 22
    assert len(preview.managers) == 13


def test_the_quoted_name_survives_parsing(preview):
    renee = next(
        employee for employee in preview.employees if employee.employee_id == "DIV-1412"
    )

    assert renee.employee_name == "Alvarez, Renée"


def test_the_same_file_always_produces_the_same_preview():
    """Roots, managers and cycle members are sorted, so the page is stable."""
    assert analyze_csv(SAMPLE.read_bytes()) == analyze_csv(SAMPLE.read_bytes())
