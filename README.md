# OrgLens

A small Django app for previewing an HRIS CSV export before any of it is
imported. You upload a file, and it tells you who would be imported, who sits at
the top of the org, who manages whom, and which rows have problems. Nothing is
written anywhere: the file is read, summarised, and discarded.

The audience is a Client Success person checking a client's export, not an
engineer, so every message names a row number and says what it means.

## Setup

Python 3.12 or newer.

```bash
python -m venv .venv
.venv/Scripts/activate        # Windows
# source .venv/bin/activate   # macOS and Linux

pip install -r requirements.txt
```

## Run

```bash
python manage.py runserver
```

Open http://127.0.0.1:8000/ and upload `sample_hris.csv` from the repo root.

There is no database, so there is no `migrate` step. That is deliberate, not an
omission.

## Test

```bash
pip install -r requirements-dev.txt
pytest
```

34 tests, no browser and no Django test client. Every test hands raw bytes to
`analyze_csv` and reads dataclasses back, which is what proves the parsing and
hierarchy logic is genuinely independent of the web layer. The suite runs with
Django installed but never imports it.

## How it is put together

```
core/          pure Python, zero Django imports
  types.py       the dataclasses every stage passes around
  parsing.py     bytes -> rows -> normalized -> identity checked
  hierarchy.py   manager resolution, then cycle detection
  analysis.py    analyze_csv(bytes) -> ImportPreview, the only public function
preview/       the Django app: one view, three templates
tests/         drives core/ directly
```

The web layer reads the uploaded bytes, calls `analyze_csv`, and renders the
result. It catches exactly one exception. It does not know what a CSV is.

## The rules it implements

Identity:

- `employee_id` and `email` are both required, and both must be unique after
  trimming and lowercasing the email.
- If an id or email is duplicated, **every** row sharing it is rejected, not
  just the later one. Keeping the last row would silently drop a real person and
  show a preview with no problems in it. A human has to pick the winner.

Managers, resolved in a second pass because rows arrive in any order:

- Both manager fields blank means the person is at the top of the org.
- One field given is looked up by id or by email.
- Both given must point at the same person, otherwise it is reported as a
  conflict rather than guessed at.
- Someone listed as their own manager is a data entry mistake, reported as such.
- A person whose manager cannot be resolved is **still imported**, but gets no
  reporting relationship and is **not** listed at the top of the org. "We could
  not place this person" is a different claim from "this person runs the
  company".

Cycles:

- Only people actually **on** a reporting loop are listed. Someone who reports
  into a loop from outside is not on it and is not flagged.
- Every person has at most one manager, so following managers from anyone is a
  single path. That makes the check simple: you are on a loop if walking your
  manager chain comes back to you.

## Assumptions

- The six expected columns are `employee_id`, `employee_name`, `email`,
  `manager_id`, `manager_email`, `department`. Extra columns are ignored, and
  header order does not matter.
- Files are UTF-8. A byte order mark is stripped, since Excel's "CSV UTF-8"
  export writes one.
- `employee_id` is case-sensitive and treated as an opaque key from the client's
  system. Emails are case-insensitive.
- A blank `employee_name` is a data quality note, not a reason to refuse to
  import someone. Only id and email are identity.
- A manager reference pointing at a row that was rejected reads as "manager not
  found", because that person is not being imported.
- Reporting loops are reported, not repaired. Deciding who really manages whom
  is the client's call.

## Known limitations

- The whole file is read into memory. That is fine into the low hundreds of
  thousands of rows and is the reason for the 20 MB upload cap, but it is not a
  streaming importer and would not suit a multi-gigabyte export.
- Results are not persisted. Closing the page loses the preview, and there is no
  way to link someone to a previous one.
- Nothing is exported. A user who wants to send the problem list to a client has
  to copy it off the page.
- Only the first problem per row is reported. A row that duplicates both an id
  and an email is listed once, under the id.
- No authentication, so anyone who can reach the server can upload. Fine for a
  local review tool, not for anything shared.
- The reporting structure is summarised rather than drawn. There is no org chart
  and no depth or span-of-control analysis.
- The 20 MB cap is enforced after Django has already buffered the upload, so a
  very large file still costs the disk write before it is refused.

## Time spent

About five hours, roughly: an hour on setup and the data shapes, two hours on
parsing and the hierarchy rules, an hour on the web layer, and an hour on tests
and documentation.

## AI tools used

Claude Code (Claude Opus) was used throughout, as an implementation assistant
rather than an author. The working pattern was one reviewed slice at a time:
scaffold, data shapes, parsing, hierarchy, entry point, web layer, tests.

Three of its suggestions were rejected outright, and those rejections are the
ones worth knowing about:

1. It first wrote identity validation as a single pass that let a later
   duplicate overwrite an earlier one. That silently drops a person, so it was
   replaced with a counting pass followed by a judgement pass.
2. It proposed a recursive DFS for cycle detection. That raises `RecursionError`
   on a deep manager chain in a large file, and it also flags everyone on the
   path into a cycle as being on the cycle, which is simply the wrong answer. It
   was replaced with an iterative walk that flags only the loop itself.
3. Its tests asserted on the exact wording of error messages. Those messages are
   user-facing prose that should stay free to improve, so the tests now assert on
   the error kind and the row number instead.

A running build log of every decision, including which suggestions were
accepted, changed, or rejected, was kept during development.
