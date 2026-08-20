"""
Tests for reading and validating rows.

Every test hands raw bytes to the core and reads dataclasses back. There is no
Django test client anywhere in this directory, which is the point: if these
tests can run, the parsing logic really is independent of the web layer.
"""

import pytest

from core.analysis import analyze_csv
from core.types import InvalidHRISFile

HEADER = b"employee_id,employee_name,email,manager_id,manager_email,department\n"


def kinds(preview):
    """The error kinds in the preview, in file order."""
    return [error.kind for error in preview.errors]


def test_both_rows_of_a_duplicate_id_are_rejected():
    """
    Trap rule 1. The obvious one-pass implementation keeps the last row and
    silently drops the first. Both rows have to be reported instead, because
    only a human can say which one is the real Ann.
    """
    preview = analyze_csv(
        HEADER
        + b"DIV-1,Ann,ann@x.com,,,Engineering\n"
        + b"DIV-2,Bob,bob@x.com,,,Engineering\n"
        + b"DIV-1,Ann Smith,ann.smith@x.com,,,Finance\n"
    )

    assert [employee.employee_id for employee in preview.employees] == ["DIV-2"]
    assert kinds(preview) == ["duplicate_id", "duplicate_id"]
    # Both source rows named, not just the second one.
    assert [error.source_row for error in preview.errors] == [2, 4]


def test_duplicate_email_is_caught_across_different_cases():
    """Normalization lowercases email before counting, so these two collide."""
    preview = analyze_csv(
        HEADER
        + b"DIV-1,Ann,SHARED@x.com,,,Engineering\n"
        + b"DIV-2,Bob,shared@x.com,,,Engineering\n"
    )

    assert preview.accepted_count == 0
    assert kinds(preview) == ["duplicate_email", "duplicate_email"]


def test_missing_identity_fields_are_reported_per_row():
    preview = analyze_csv(
        HEADER
        + b"DIV-1,Ann,,,,Engineering\n"
        + b",Bob,bob@x.com,,,Engineering\n"
        + b"DIV-3,Cleo,cleo@x.com,,,Engineering\n"
    )

    assert [employee.employee_id for employee in preview.employees] == ["DIV-3"]
    assert kinds(preview) == ["missing_required", "missing_required"]


def test_missing_employee_name_is_still_imported():
    """Only id and email are identity. A blank name is a data quality note."""
    preview = analyze_csv(HEADER + b"DIV-1,,ann@x.com,,,Engineering\n")

    assert preview.accepted_count == 1
    assert preview.errors == []


def test_quoted_name_containing_a_comma_stays_one_field():
    preview = analyze_csv(
        HEADER + 'DIV-1,"Alvarez, Ren\u00e9e",r@x.com,,,Operations\n'.encode("utf-8")
    )

    assert preview.employees[0].employee_name == "Alvarez, Renée"
    assert preview.employees[0].department == "Operations"


def test_headers_may_appear_in_any_order():
    preview = analyze_csv(
        b"department,email,employee_id,manager_email,employee_name,manager_id\n"
        b"Engineering,ann@x.com,DIV-1,,Ann,\n"
    )

    assert preview.employees[0].employee_id == "DIV-1"
    assert preview.employees[0].employee_name == "Ann"


def test_values_are_trimmed_and_emails_lowercased_but_ids_are_not():
    preview = analyze_csv(
        HEADER + b"  DIV-1a  ,  Ann  ,  ANN@X.COM  ,,,  Engineering  \n"
    )
    employee = preview.employees[0]

    assert employee.employee_id == "DIV-1a"
    assert employee.email == "ann@x.com"
    assert employee.department == "Engineering"


def test_utf8_byte_order_mark_is_stripped():
    """Excel's CSV UTF-8 export writes a BOM. Without stripping it the first
    header reads as "\ufeffemployee_id" and a good file gets rejected."""
    preview = analyze_csv(b"\xef\xbb\xbf" + HEADER + b"DIV-1,Ann,ann@x.com,,,Engineering\n")

    assert preview.accepted_count == 1


def test_row_with_wrong_column_count_does_not_cost_the_other_rows():
    preview = analyze_csv(
        HEADER
        + b"DIV-1,Ann,ann@x.com,,,Engineering\n"
        + b"DIV-2,Bob,bob@x.com,,\n"
        + b"DIV-3,Cleo,cleo@x.com,,,Engineering,extra\n"
    )

    assert [employee.employee_id for employee in preview.employees] == ["DIV-1"]
    assert kinds(preview) == ["malformed_row", "malformed_row"]
    assert preview.total_rows == 3


def test_errors_are_listed_in_file_order():
    """The user reads errors against the file, not against our pipeline order."""
    preview = analyze_csv(
        HEADER
        + b"DIV-1,Ann,ann@x.com,DIV-404,,Engineering\n"
        + b"DIV-2,Bob,bob@x.com,,\n"
        + b",Cleo,cleo@x.com,,,Engineering\n"
    )

    assert [error.source_row for error in preview.errors] == [2, 3, 4]


@pytest.mark.parametrize(
    "raw, expected",
    [
        (b"", "empty"),
        (b"   \n  ", "empty"),
        ("employee_id,email\nDIV-1,a@x.com\n".encode("utf-16"), "UTF-8"),
        (b"name,age\nAnn,30\n", "does not look like an HRIS export"),
        (HEADER, "no employee rows"),
        # and gives up. Without a guard this is a traceback, not a message.
    ],
)
def test_unusable_files_raise_a_readable_message(raw, expected):
    """Whole-file problems raise. The message is shown to the user as-is, so it
    has to read like a sentence, not like an exception."""
    with pytest.raises(InvalidHRISFile) as caught:
        analyze_csv(raw)

    assert expected in str(caught.value)


@pytest.mark.parametrize(
    "raw",
    [
        # No line breaks anywhere, so the reader sees the whole export as one
        # field and gives up while reading the header.
        b"employee_id,employee_name,email,manager_id,manager_email,department," + b"x" * 200_000,
        # A single value past the reader's 128 KB field limit, hit mid-file.
        HEADER + b"DIV-1," + b"x" * 200_000 + b",a@x.com,,,Engineering\n",
    ],
    ids=["no-line-breaks", "oversized-field"],
)
def test_a_file_the_csv_reader_cannot_read_gives_a_message_not_a_traceback(raw):
    """The brief is explicit that a malformed upload never shows a stack trace,
    and csv.Error is the one failure that escapes the row-level checks."""
    with pytest.raises(InvalidHRISFile) as caught:
        analyze_csv(raw)

    assert "could not be read as a CSV" in str(caught.value)


def test_people_who_share_a_name_are_kept_apart():
    """
    Two real people can genuinely share a name. Identity is employee_id and
    email, never the name, so all three of these import and stay distinct. The
    result page prints the id beside every name for exactly this reason.
    """
    preview = analyze_csv(
        HEADER
        + b"DIV-1,Ann Lee,ann.lee@x.com,,,Engineering\n"
        + b"DIV-2,Ann Lee,a.lee@x.com,DIV-1,,Engineering\n"
        + b"DIV-3,Ann Lee,annlee@x.com,DIV-2,,Engineering\n"
    )

    assert preview.accepted_count == 3
    assert preview.errors == []
    assert [employee.employee_id for employee in preview.employees] == [
        "DIV-1",
        "DIV-2",
        "DIV-3",
    ]
    # Each is a manager in their own right, not merged into one Ann Lee.
    assert [entry.manager.employee_id for entry in preview.managers] == ["DIV-1", "DIV-2"]


def test_headers_may_be_capitalised_or_padded():
    """
    Real exports write Employee_ID as often as employee_id, and a stray space
    after a comma is common. Header names are normalized the same way values
    are, and the normalized names become the keys the rows are read by. Checking
    the tidy form but reading by the raw form would let the file pass the header
    check and then make every row look broken.
    """
    preview = analyze_csv(
        b"Employee_ID , Employee_Name,EMAIL,Manager_ID,manager_email , Department\n"
        b"DIV-1,Ann,ANN@x.com,,,Engineering\n"
    )

    assert preview.accepted_count == 1
    assert preview.errors == []
    assert preview.employees[0].employee_id == "DIV-1"
    assert preview.employees[0].email == "ann@x.com"


def test_a_repeated_column_name_is_refused_rather_than_guessed():
    """One of the two columns would silently win and the user would never know
    which, so the file is refused instead."""
    with pytest.raises(InvalidHRISFile) as caught:
        analyze_csv(
            b"employee_id,employee_id,email,employee_name,manager_id,manager_email,department\n"
            b"DIV-1,DIV-2,a@x.com,Ann,,,Engineering\n"
        )

    assert "more than one column called employee_id" in str(caught.value)


def test_row_numbers_follow_the_lines_the_user_can_see():
    """
    A quoted value containing a line break is one row spread over two lines.
    Row numbers are the whole mechanism for finding a problem in the file, so
    they count lines, not rows: Bob is on line 4 even though he is the second
    employee.
    """
    preview = analyze_csv(
        HEADER + b'DIV-1,"Ann\nLee",ann@x.com,,,Engineering\n' + b"DIV-2,Bob,,,,Engineering\n"
    )

    assert preview.employees[0].employee_name == "Ann\nLee"
    assert [error.source_row for error in preview.errors] == [4]
