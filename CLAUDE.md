# CLAUDE.md — OrgLens

Guidance for any AI agent (and future-me) working in this repo. Read this before writing or changing code.

## What this is

OrgLens is a small Django web app for the Diversio "Engineer I" take-home exercise. A user uploads an HRIS CSV export in the browser and gets back an in-memory "import preview": a readable summary a non-engineer in Client Success can act on before any data is written to a real platform. No database, no auth, no JS framework.

The single most important context: this is graded on **technical ownership**, not just a working app. A required ≤10-minute narrated video walks through the code. Code without that explanation is treated as AI-generated work with no ownership and marked incomplete. So every decision in this repo must be one the human author can explain in their own words. Prefer clear, traceable code over clever code. When there is a choice between "impressive" and "explainable", choose explainable.

## Hard constraints (the brief says do NOT build these)

Do not add any of the following, even if asked to "improve" the project. Adding them signals poor scoping under a timebox, which is being tested.

- Authentication or user accounts
- Production deployment config
- Database persistence
- A JavaScript frontend framework
- Elaborate styling (plain, clear HTML is explicitly fine and preferred)
- Any feature not related to the import preview

Restraint scores higher here than polish. A plain page with correct logic beats a pretty page with a subtle bug.

## Tech choices

- Python, Django (chosen because it matches Diversio's stack).
- Standard library `csv` for parsing. No pandas, no heavy deps.
- Keep the dependency list minimal and justifiable.

## Architecture: pure core, thin web layer

The brief requires parsing and hierarchy logic to be testable without a browser. This drives the whole layout. The core is plain Python that imports nothing from Django. The web layer only moves bytes in and renders a result out.

```
orglens/
├── core/                 # pure Python, ZERO Django imports
│   ├── types.py          # dataclasses: Employee, RowError, ImportPreview
│   ├── parsing.py        # bytes -> rows -> normalize -> identity validation
│   ├── hierarchy.py      # manager resolution + cycle detection
│   └── analysis.py       # analyze_csv(raw_bytes) -> ImportPreview (orchestrator)
├── preview/              # Django app (thin)
│   ├── views.py          # GET = upload form; POST = call analyze_csv, render
│   ├── urls.py
│   └── templates/preview/{upload.html,result.html}
├── orglens_site/         # Django project (settings, urls, wsgi)
├── tests/
│   ├── test_parsing.py
│   └── test_hierarchy.py
├── sample_hris.csv
├── manage.py
└── README.md
```

The one entry point into the core is `analyze_csv(raw_bytes) -> ImportPreview`. `views.py` must stay tiny: read `request.FILES`, call `analyze_csv`, hand the result to the template. If parsing logic ever reaches into `request` or `request.FILES`, that is a design failure. The web layer never touches CSV internals.

## Core pipeline (fixed order, this is the story the video tells)

1. **Read and normalize** (`parsing.py`): decode bytes as UTF-8, stripping a BOM if present. Feed to `csv.DictReader`, which handles quoted commas and any header order for free. For each row: trim every field, lowercase `email` and `manager_email`, keep `employee_id` case-sensitive. Remember the original 1-based source row number for every row.
2. **Identity validation** (`parsing.py`): count occurrences of each normalized `employee_id` and each normalized `email` first, then validate. This must be count-first, not first-write-wins (see trap rules). Valid rows become `Employee`; invalid ones become `RowError`.
3. **Hierarchy** (`hierarchy.py`): build two lookups (id -> employee, email -> employee) from accepted employees, resolve each employee's manager reference, then detect cycles over the resulting edges.
4. **Aggregate** (`analysis.py`): assemble the `ImportPreview` with total rows, accepted count, error list, roots, manager direct-report counts, and cycle members.

## Data shapes (`types.py`)

- `Employee`: the six normalized fields plus `source_row: int`.
- `RowError`: `source_row: int`, `kind: str` (e.g. `duplicate_id`, `duplicate_email`, `missing_required`, `manager_not_found`, `manager_conflict`, `self_management`), and a human-readable `message`.
- `ImportPreview`: everything the template renders. Total source rows, accepted employees, list of `RowError`, roots, managers with direct-report counts, cycle members.

Define these first; every stage depends on them.

## Normalization rules

- Trim surrounding whitespace on every value.
- Lowercase `email` and `manager_email`.
- `employee_id` stays case-sensitive.
- Support UTF-8 with or without a BOM.
- Use real CSV parsing so quoted values with commas (e.g. `"Alvarez, Renée"`) work. Headers may appear in any order.

## Identity rules

- `employee_id` and `email` are both required.
- Both must be unique after normalization.
- If an id or email is duplicated, EVERY row sharing it is invalid, not just the later ones.
- Invalid-identity rows are excluded from all manager lookup and hierarchy analysis.

## Manager rules (rows may appear in any order, so resolve in a second pass)

- Both manager fields blank: employee is a **root**.
- Only `manager_id`: look up by employee id.
- Only `manager_email`: look up by normalized email.
- Both present: both must resolve to the SAME employee, else it is a conflict error.
- Report a clear error for: manager not found, conflicting references, or an employee managing itself.
- An employee with a manager error stays **accepted**, but produces NO reporting relationship and is NOT a root.

## Cycle rules

- Identify employees who are MEMBERS of a reporting cycle.
- Do not flag an employee as cyclic just because they report INTO a cycle. Reaching a cycle is not the same as being on one.
- Each employee has at most one manager, so the manager graph is a functional graph (out-degree <= 1). A node is on a cycle iff following its manager chain returns to itself. Use this property.
- Self-management is a manager error, not a one-node cycle. Handle it under manager rules before cycle detection so it never shows up as a cycle.

## The four trap rules (most submissions fail at least one)

These are seeded deliberately. Getting all four right is what separates a strong submission. Guard each with a test.

1. Duplicates are count-first: every row sharing a duplicated id/email is invalid, not first-write-wins.
2. A manager-error employee is accepted but is NOT a root and has no relationship.
3. Reporting INTO a cycle does not make you cyclic; only being on the cycle does.
4. Self-management is a manager error, not a cycle.

## Error handling

Malformed uploads must fail with a clear message, never a 500 or stack trace. Cover at minimum: empty file, wrong or missing headers, non-UTF-8 bytes, and rows with the wrong number of columns. The user-facing message should be plain, e.g. "This file does not look like an HRIS export." Validate shape before analysis.

## Complexity target

Must be explainable for files approaching 100,000 employees. The whole pipeline is O(n) time and O(n) space: counting, lookups, resolution, and functional-graph cycle detection are each a linear pass with dict lookups. Cycle detection must be ITERATIVE with an explicit per-node state marker (unvisited / in-progress / done). Do not use recursion: a deep manager chain would blow Python's recursion limit at this scale. This recursion-to-iteration decision is a good one to narrate in the video.

## Testing

At least two focused automated tests, driving the pure core (pass bytes to a function), never a browser test client. Priority coverage: the duplicate-identity rule, cycle detection, manager conflict, self-management, and the manager-error-is-not-a-root case. Tests should import from `core/`, which proves the separation of concerns is real.

## Maintain a build log (`DECISIONS.md`)

Create and continuously update a file called `DECISIONS.md` at the repo root as you build. This is the most important artifact for the author's video walkthrough and second-round interview, because it captures reasoning at write-time instead of reconstructing it later. Update it after every meaningful slice of work, not at the end.

The author, not the agent, owns the explanations. Keep entries short and factual so the author can expand each one in their own words on camera. Do not write it in a way that sounds AI-authored; write terse decision notes, not prose essays.

Structure it with these sections and append to them as you go:

1. **Decision log**: one line per non-trivial decision, in the form "Chose X over Y because Z." Examples to capture as they happen: count-first duplicate detection, iterative (not recursive) cycle detection, standard-library csv over pandas, dataclasses as the inter-stage contract, thin-view design.
2. **AI-usage log**: every notable agent suggestion, tagged ACCEPTED, CHANGED, or REJECTED, each with a one-line reason. The video requires at least one of each, so make sure all three tags appear. Example: "REJECTED: agent proposed recursive DFS for cycles; rejected because a deep manager chain blows Python's recursion limit near 100k rows."
3. **Data-flow trace**: a few lines describing how a row changes as it moves through decode/normalize -> identity validation -> manager resolution -> cycle detection -> ImportPreview. This is the spine of point 2 in the walkthrough.
4. **Edge cases handled**: the specific cases the code guards, with a pointer to the test that covers each.
5. **Known limitations / next steps**: real trade-offs (for example: whole file loaded into memory, fine to ~100k but not streaming multi-GB files). Feeds the README and the "with more time" part of the video.
6. **Complexity notes**: one or two lines stating the O(n) time and space claim and why, phrased so the author can defend it.

Whenever a decision maps to one of the seven video points, note which point in the entry. Keep `DECISIONS.md` and the README consistent; the README's assumptions, limitations, time-spent, and AI-tools sections should draw directly from this log.

## Sample data ground truth (`sample_hris.csv`)

Use these as test anchors. Verified from the supplied file:

- 25 data rows (excluding header); all have valid, unique identity, so 25 accepted.
- Exactly 1 root: DIV-1001 (Avery Morgan), both manager fields blank.
- Manager-not-found error: DIV-1600 (Casey Bell) points at DIV-9999, which does not exist.
- Manager-conflict error: DIV-1601 (Riley Cooper) has `manager_id` DIV-1100 (Priya Shah) but `manager_email` resolving to DIV-1200 (Mateo Rivera). Different employees, so conflict.
- Reporting cycle of three in Research: DIV-1702 -> DIV-1703 -> DIV-1701 -> DIV-1702.
- Quoted-name test: DIV-1412 is "Alvarez, Renée" with a comma inside the quoted name.
- Both-references-agree cases that must NOT error: DIV-1300, DIV-1412, DIV-1113 each supply id and email pointing at the same manager.

The two manager errors (DIV-1600, DIV-1601) stay accepted, are not roots, and add no direct-report counts to anyone.

## README must include

Setup and run instructions, test instructions, assumptions and known limitations, approximate time spent, and which AI tools were used. Be honest in "known limitations"; naming real trade-offs reads as more mature than claiming perfection.

## Coding conventions

- Prose and comments: natural, plain style, no em dashes.
- Small, single-purpose functions with names that say what they do.
- Comments explain WHY, not what, especially at each trap rule.
- No premature abstraction. Match the size of the problem.
