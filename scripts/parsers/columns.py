"""
Tolerant column resolution for MechanicDesk .xls exports.

The existing parsers (income, quote, productivity, efficiency) match
header names exactly, which is fine for reports whose schema we have
pinned against a real file. The Job and Job WIP reports are wider and
their headers vary in small ways that would break an exact match --
"Job No." vs "Job No.#", "Invoice Finalized" vs "Invoice Finalised",
trailing whitespace, inconsistent casing.

Rather than guess one spelling and fail silently on the other, these
helpers normalise headers and accept a list of acceptable aliases, and
raise an error that names every column actually present when nothing
matches. A wrong guess therefore surfaces as a loud, diagnosable
ingestion warning rather than a quietly missing field.
"""


def normalise(name):
    """Lowercases, strips punctuation/whitespace noise for comparison."""
    text = str(name or "").strip().lower()
    for ch in ".#:_-()":
        text = text.replace(ch, " ")
    return " ".join(text.split())


def build_index(header_row):
    """Maps normalised header name -> column index."""
    index = {}
    for idx, name in enumerate(header_row):
        key = normalise(name)
        if key and key not in index:
            index[key] = idx
    return index


def resolve(index, aliases, header_row, required=True):
    """
    Returns the column index for the first alias present, trying exact
    normalised match first and then a whole-word containment match.
    Returns None when not found and required=False.
    """
    for alias in aliases:
        key = normalise(alias)
        if key in index:
            return index[key]
    for alias in aliases:
        key = normalise(alias)
        for present, idx in index.items():
            if present.startswith(key) or key.startswith(present):
                return idx
    if required:
        found = ", ".join(f"'{h}'" for h in header_row if str(h).strip())
        raise ValueError(
            f"None of the expected columns {aliases} were found. "
            f"Columns actually present: {found}"
        )
    return None


def find_header_row(sheet, must_contain, max_scan=10):
    """
    MechanicDesk exports usually put headers on row 0, but some reports
    carry a title/date banner first. Scans the first few rows for the
    one containing the given marker columns and returns
    (row_index, header_values).
    """
    wanted = [normalise(m) for m in must_contain]
    for r in range(min(max_scan, sheet.nrows)):
        values = [sheet.cell_value(r, c) for c in range(sheet.ncols)]
        present = {normalise(v) for v in values}
        if all(any(p.startswith(w) or w.startswith(p) for p in present if p) for w in wanted):
            return r, values
    raise ValueError(
        f"Could not find a header row containing {must_contain} in the first "
        f"{max_scan} rows of the sheet."
    )
