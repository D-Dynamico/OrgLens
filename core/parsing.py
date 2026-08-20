"""
Stage 1 and 2 of the pipeline: raw bytes to normalized rows to identity checks.

Nothing in here knows about HTTP, uploads or Django. The input is bytes and
the output is dataclasses.
"""

import csv
import io
from collections import defaultdict
from dataclasses import dataclass

from .types import Employee, InvalidHRISFile, RowError

# UTF-8 byte order mark. Excel writes this on "CSV UTF-8" export, so HRIS
# files from a client very often start with it.
BOM = "\ufeff"


def decode_bytes(raw: bytes) -> str:
    """
    Turn the uploaded bytes into text, or fail with a message a non-engineer
    can act on.

    Both failures here are whole-file problems, so they raise instead of
    becoming RowError. There are no rows yet to attach an error to.
    """
    if not raw or not raw.strip():
        raise InvalidHRISFile("The uploaded file is empty.")

    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise InvalidHRISFile(
            "This file is not valid UTF-8 text. If it was exported from Excel, "
            "re-save it as CSV UTF-8 and upload it again."
        ) from None

    # Strip the BOM rather than rejecting it. If we left it in, the first
    # header would be read as "\ufeffemployee_id" and the header check below
    # would reject a perfectly good file.
    return text.removeprefix(BOM)


# The six columns an HRIS export has to have. Order does not matter because
# DictReader keys off the header line, not position.
REQUIRED_COLUMNS = (
    "employee_id",
    "employee_name",
    "email",
    "manager_id",
    "manager_email",
    "department",
)

# Sentinels handed to DictReader so a row with the wrong number of columns is
# detectable instead of silently padded. Extra cells land in a list under
# _EXTRA_COLUMNS, missing cells come back as _MISSING_CELL.
_EXTRA_COLUMNS = "__extra__"
_MISSING_CELL = None


@dataclass(frozen=True)
class RawRow:
    """
    One data row straight off the CSV reader, before normalization.

    Deliberately not in types.py: this never leaves parsing.py, so it is not
    part of the contract between stages.
    """

    values: dict[str, str]
    source_row: int


def read_rows(text: str) -> tuple[list[RawRow], list[RowError]]:
    """
    Split decoded text into rows, checking the shape of the file first.

    Uses csv.DictReader so quoted values containing commas (for example
    "Alvarez, Renee") and any header order both work without extra code.

    A wrong-shaped file raises. A wrong-shaped row is only that row's problem,
    so it becomes a RowError and the rest of the file is still previewed.
    """
    reader = csv.DictReader(
        io.StringIO(text),
        restkey=_EXTRA_COLUMNS,
        restval=_MISSING_CELL,
    )

    if reader.fieldnames is None:
        raise InvalidHRISFile("The uploaded file is empty.")

    headers = [(name or "").strip().lower() for name in reader.fieldnames]
    missing = [column for column in REQUIRED_COLUMNS if column not in headers]
    if missing:
        raise InvalidHRISFile(
            "This file does not look like an HRIS export. It is missing these "
            "columns: " + ", ".join(missing) + "."
        )

    rows: list[RawRow] = []
    errors: list[RowError] = []

    for offset, values in enumerate(reader):
        # Header is line 1, so the first data row is line 2.
        source_row = offset + 2

        if _EXTRA_COLUMNS in values:
            errors.append(
                RowError(
                    source_row=source_row,
                    kind="malformed_row",
                    message=(
                        f"Row {source_row} has more values than there are columns. "
                        "Check for an unescaped comma or a stray quote."
                    ),
                )
            )
            continue

        if any(values.get(column) is _MISSING_CELL for column in REQUIRED_COLUMNS):
            errors.append(
                RowError(
                    source_row=source_row,
                    kind="malformed_row",
                    message=(
                        f"Row {source_row} has fewer values than there are columns."
                    ),
                )
            )
            continue

        rows.append(RawRow(values=values, source_row=source_row))

    if not rows and not errors:
        raise InvalidHRISFile(
            "This file has column headers but no employee rows."
        )

    return rows, errors


def normalize_row(row: RawRow) -> dict[str, str]:
    """
    Clean one row's values so later stages can compare them directly.

    Every later stage (duplicate counting, manager lookup) compares strings.
    Doing the cleanup once here means no stage downstream has to remember to
    call .strip() or .lower(), which is exactly the kind of thing that gets
    forgotten in one place and produces a bug that looks like bad data.
    """
    values = {column: (row.values.get(column) or "").strip() for column in REQUIRED_COLUMNS}

    # Email is case-insensitive in practice, and the sample file mixes cases
    # (DEMO.SOFIA.CHEN@... vs demo.sofia.chen@...) on purpose. Lowercase both
    # email fields so a manager reference matches its employee.
    values["email"] = values["email"].lower()
    values["manager_email"] = values["manager_email"].lower()

    # employee_id stays case-sensitive. It is an opaque key from the client's
    # HRIS, not something we get to decide the casing rules for. Lowercasing it
    # could merge two genuinely different people.

    return values


def _group_source_rows(normalized: list[tuple[dict[str, str], int]], column: str) -> dict[str, list[int]]:
    """Map each non-blank value in a column to every source row that used it."""
    groups: dict[str, list[int]] = defaultdict(list)
    for values, source_row in normalized:
        value = values[column]
        if value:
            groups[value].append(source_row)
    return groups


def validate_identity(rows: list[RawRow]) -> tuple[list[Employee], list[RowError]]:
    """
    Normalize every row, then decide which rows have a usable identity.

    This runs in two passes on purpose, and that is the whole point of the
    function. The first pass only counts. The second pass decides.

    A single pass that wrote `by_id[employee_id] = employee` would let a later
    duplicate silently overwrite an earlier one: the preview would show 24 clean
    employees and no problems at all, while one real person had been dropped
    without anyone being told. Counting first means every row sharing a
    duplicated id or email is reported, and a human picks the winner.
    """
    normalized = [(normalize_row(row), row.source_row) for row in rows]

    # Pass one: count only, decide nothing.
    rows_by_id = _group_source_rows(normalized, "employee_id")
    rows_by_email = _group_source_rows(normalized, "email")

    employees: list[Employee] = []
    errors: list[RowError] = []

    # Pass two: now that the counts are complete, judge each row.
    for values, source_row in normalized:
        employee_id = values["employee_id"]
        email = values["email"]

        missing = [
            column
            for column in ("employee_id", "email")
            if not values[column]
        ]
        if missing:
            errors.append(
                RowError(
                    source_row=source_row,
                    kind="missing_required",
                    message=(
                        f"Row {source_row} is missing a required value: "
                        + " and ".join(missing)
                        + "."
                    ),
                )
            )
            continue

        # One error per row keeps the error count equal to the number of
        # unusable rows. If a row duplicates both id and email, reporting the
        # id is enough to send the user to the same place in the file.
        if len(rows_by_id[employee_id]) > 1:
            errors.append(
                _duplicate_error(source_row, "duplicate_id", "employee_id", employee_id, rows_by_id[employee_id])
            )
            continue

        if len(rows_by_email[email]) > 1:
            errors.append(
                _duplicate_error(source_row, "duplicate_email", "email", email, rows_by_email[email])
            )
            continue

        employees.append(
            Employee(
                employee_id=employee_id,
                employee_name=values["employee_name"],
                email=email,
                manager_id=values["manager_id"],
                manager_email=values["manager_email"],
                department=values["department"],
                source_row=source_row,
            )
        )

    return employees, errors


def _duplicate_error(source_row: int, kind: str, column: str, value: str, all_rows: list[int]) -> RowError:
    """Build a duplicate error that names the other rows sharing the value."""
    other_rows = [row for row in all_rows if row != source_row]
    label = "row" if len(other_rows) == 1 else "rows"
    others = ", ".join(str(row) for row in other_rows)
    return RowError(
        source_row=source_row,
        kind=kind,
        message=(
            f"Row {source_row} has {column} '{value}', which also appears on "
            f"{label} {others}. Every row sharing it is excluded until the "
            "duplicate is resolved."
        ),
    )


def parse(raw: bytes) -> tuple[int, list[Employee], list[RowError]]:
    """
    Run the whole parsing stage: bytes in, accepted employees and errors out.

    Returns the total number of data rows seen as well, because the preview
    reports "25 rows read, 23 accepted" and the caller cannot work that out
    from the two lists alone: a malformed row is neither an Employee nor
    something with an identity.
    """
    text = decode_bytes(raw)
    rows, shape_errors = read_rows(text)
    employees, identity_errors = validate_identity(rows)

    total_rows = len(rows) + len(shape_errors)
    # Sorted by source row so the user reads the error list in file order,
    # not grouped by which check happened to run first.
    errors = sorted(shape_errors + identity_errors, key=lambda error: error.source_row)

    return total_rows, employees, errors
