"""
Tests for manager resolution and cycle detection.

Same rule as the parsing tests: bytes in, dataclasses out, no browser and no
Django test client.
"""

from core.analysis import analyze_csv

HEADER = b"employee_id,employee_name,email,manager_id,manager_email,department\n"


def ids(employees):
    return {employee.employee_id for employee in employees}


def kinds(preview):
    return [error.kind for error in preview.errors]


def test_reporter_into_cycle_is_not_a_cycle_member():
    """
    Trap rule 3. A, B and C report to each other in a circle. X reports to A and
    Y reports to X, so both can reach the cycle, but neither is on it. A DFS
    that flags everything on the visiting path would wrongly include them.
    """
    preview = analyze_csv(
        HEADER
        + b"A,Ana,a@x.com,B,,Research\n"
        + b"B,Ben,b@x.com,C,,Research\n"
        + b"C,Cleo,c@x.com,A,,Research\n"
        + b"X,Xavi,x@x.com,A,,Research\n"
        + b"Y,Yuki,y@x.com,X,,Research\n"
    )

    assert ids(preview.cycle_members) == {"A", "B", "C"}
    assert preview.errors == []
    # Everyone is still imported. A cycle is a shape problem, not a bad row.
    assert preview.accepted_count == 5


def test_self_management_is_an_error_not_a_one_person_cycle():
    """
    Trap rule 4. Handled during manager resolution, before cycle detection ever
    sees the graph, so it never shows up under reporting loops.
    """
    preview = analyze_csv(HEADER + b"A,Ana,a@x.com,A,,Research\n")

    assert kinds(preview) == ["self_management"]
    assert preview.cycle_members == []
    assert preview.roots == []
    assert preview.accepted_count == 1


def test_self_management_by_email_is_caught_too():
    preview = analyze_csv(HEADER + b"A,Ana,a@x.com,,A@X.COM,Research\n")

    assert kinds(preview) == ["self_management"]
    assert preview.cycle_members == []


def test_manager_error_employee_is_accepted_but_is_not_a_root():
    """
    Trap rule 2. Calling them a root would tell the user "this person is top of
    the org", which is a different and wrong claim from "we could not place
    this person".
    """
    preview = analyze_csv(
        HEADER
        + b"A,Ana,a@x.com,,,Executive\n"
        + b"B,Ben,b@x.com,MISSING,,Engineering\n"
    )

    assert ids(preview.employees) == {"A", "B"}
    assert ids(preview.roots) == {"A"}
    assert kinds(preview) == ["manager_not_found"]
    # No edge was created, so Ana gained no direct report from Ben.
    assert preview.managers == []


def test_conflicting_manager_references_are_reported_not_guessed():
    preview = analyze_csv(
        HEADER
        + b"A,Ana,a@x.com,,,Executive\n"
        + b"B,Ben,b@x.com,,,Executive\n"
        + b"C,Cleo,c@x.com,A,b@x.com,Engineering\n"
    )

    assert kinds(preview) == ["manager_conflict"]
    assert preview.accepted_count == 3
    assert preview.managers == []


def test_both_manager_references_agreeing_is_not_a_conflict():
    preview = analyze_csv(
        HEADER
        + b"A,Ana,a@x.com,,,Executive\n"
        + b"B,Ben,b@x.com,A,A@X.COM,Engineering\n"
    )

    assert preview.errors == []
    assert [entry.manager.employee_id for entry in preview.managers] == ["A"]
    assert preview.managers[0].direct_reports == 1


def test_manager_pointing_at_a_rejected_duplicate_reads_as_not_found():
    """
    A row rejected for a duplicate id is not being imported, so nobody can
    report to it. "Manager not found" is the honest thing to say.
    """
    preview = analyze_csv(
        HEADER
        + b"DUP,Ana,a@x.com,,,Executive\n"
        + b"DUP,Ada,ada@x.com,,,Executive\n"
        + b"C,Cleo,c@x.com,DUP,,Engineering\n"
    )

    assert kinds(preview) == ["duplicate_id", "duplicate_id", "manager_not_found"]


def test_rows_may_appear_in_any_order():
    """The manager is listed below their report, which resolution must survive."""
    preview = analyze_csv(
        HEADER
        + b"B,Ben,b@x.com,A,,Engineering\n"
        + b"A,Ana,a@x.com,,,Executive\n"
    )

    assert ids(preview.roots) == {"A"}
    assert preview.managers[0].direct_reports == 1


def test_two_separate_cycles_are_both_found():
    preview = analyze_csv(
        HEADER
        + b"A,Ana,a@x.com,B,,Research\n"
        + b"B,Ben,b@x.com,A,,Research\n"
        + b"P,Pia,p@x.com,Q,,Product\n"
        + b"Q,Quin,q@x.com,R,,Product\n"
        + b"R,Rio,r@x.com,P,,Product\n"
        + b"T,Tom,t@x.com,,,Sales\n"
    )

    assert ids(preview.cycle_members) == {"A", "B", "P", "Q", "R"}
    assert ids(preview.roots) == {"T"}


def test_a_long_manager_chain_does_not_hit_the_recursion_limit():
    """
    The reason cycle detection is a loop and not a recursive DFS. This chain is
    far deeper than Python's default 1,000 frame limit, and it closes into a
    cycle at the top so every node has to be walked.
    """
    depth = 5000
    rows = b"".join(
        f"E{i},Person {i},e{i}@x.com,E{i + 1},,Engineering\n".encode()
        for i in range(depth)
    )
    rows += f"E{depth},Person {depth},e{depth}@x.com,E0,,Engineering\n".encode()

    preview = analyze_csv(HEADER + rows)

    assert preview.accepted_count == depth + 1
    assert len(preview.cycle_members) == depth + 1
