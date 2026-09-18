"""
Read-only probe of the MechanicDesk Job and Job WIP reports.

Purpose: confirm the DN06 Customer After-Care feed can actually read what
it will depend on -- the GOLD AFTER-CARE tag and the job finalisation
date -- before anything is built on top of them.

It downloads the latest Job and Job WIP report for both locations,
prints each file's real header row and what it finds, and stops. No
state file is written, no commit is made, nothing is deployed.

Self-contained on purpose: it reuses the Graph helpers already in
nightly_ingest.py, but carries its own small column-matching and parsing
logic so it can be added as a single file. The production parsers come
later; this run exists to prove the column names first.

Run it from the Actions tab ("Probe Job Reports").
"""

import os
import sys
import tempfile
import urllib.parse

sys.path.insert(0, os.path.dirname(__file__))

import xlrd

from nightly_ingest import (
    SUBJECT_PATTERN, LOCATION_KEY, get_env, perth_date,
    fetch_child_folder_id, fetch_recent_messages, download_attachment,
)
from graph_client import get_access_token

AFTER_CARE_TAG = "AFTER-CARE"
REPORT_TYPES = ("Job", "Job Wip")


def normalise(name):
    """Lowercase and strip punctuation so header spellings can be compared."""
    text = str(name or "").strip().lower()
    for ch in ".#:_-()":
        text = text.replace(ch, " ")
    return " ".join(text.split())


def find_column(header, aliases):
    """Returns the index of the first alias present, or None."""
    index = {normalise(h): i for i, h in enumerate(header) if normalise(h)}
    for alias in aliases:
        key = normalise(alias)
        if key in index:
            return index[key]
    for alias in aliases:
        key = normalise(alias)
        for present, i in index.items():
            if present.startswith(key) or key.startswith(present):
                return i
    return None


def match_report_type(raw):
    folded = " ".join(raw.split()).casefold()
    for known in REPORT_TYPES:
        if known.casefold() == folded:
            return known
    return None


def cell(sheet, r, c):
    return "" if c is None else str(sheet.cell_value(r, c) or "").strip()


def has_after_care(tags_text):
    return any(t.strip().upper() == AFTER_CARE_TAG for t in str(tags_text or "").split(","))


def report_on(sheet, report_type):
    """Prints the real header row, then what this report contains."""
    header = [sheet.cell_value(0, c) for c in range(sheet.ncols)]

    print("  REAL HEADER ROW:")
    for c, name in enumerate(header):
        if str(name).strip():
            print(f"    [{c:>2}] {name}")

    c_job = find_column(header, ["Job No.#", "Job No."])
    c_tags = find_column(header, ["Tags"])
    c_customer = find_column(header, ["Customer"])

    if c_job is None or c_tags is None:
        print("  *** Could not find a Job No. and/or Tags column -- "
              "the names above are what the parsers must match. ***")
        return False

    if report_type == "Job":
        c_final = find_column(header, ["Invoice Finalized", "Invoice Finalised"])
        c_finished = find_column(header, ["Finished Date"])
        c_invoice = find_column(header, ["Invoice No.#", "Invoice No."])
        c_mobile = find_column(header, ["Mobile"])
        if c_final is None or c_finished is None:
            print("  *** No Invoice Finalized and/or Finished Date column -- "
                  "Day 0 cannot be detected from this report. ***")
            return False

        rows = finalised = tagged_open = 0
        for r in range(1, sheet.nrows):
            job = cell(sheet, r, c_job)
            if not job or job.lower().startswith("total"):
                continue
            rows += 1
            if not has_after_care(sheet.cell_value(r, c_tags)):
                continue
            is_final = cell(sheet, r, c_final).upper() in ("TRUE", "YES", "1")
            finished = cell(sheet, r, c_finished)
            if is_final and finished:
                finalised += 1
                print(f"    DAY 0  {job} · {cell(sheet, r, c_customer)} · finished {finished} "
                      f"· {cell(sheet, r, c_invoice)} · mobile {cell(sheet, r, c_mobile) or '(none)'}")
            else:
                tagged_open += 1
                print(f"    tagged but not finalised (correctly excluded): {job} · "
                      f"{cell(sheet, r, c_customer)}")
        print(f"  {rows} job rows · {finalised} would start a cycle · {tagged_open} excluded")
    else:
        c_sales = find_column(header, ["Saleperson", "Salesperson"])
        c_created = find_column(header, ["Created By"])
        rows = tagged = rework = sales_filled = 0
        for r in range(1, sheet.nrows):
            job = cell(sheet, r, c_job)
            if not job or job.lower().startswith("total"):
                continue
            rows += 1
            tags_text = str(sheet.cell_value(r, c_tags) or "")
            if cell(sheet, r, c_sales):
                sales_filled += 1
            if has_after_care(tags_text):
                tagged += 1
                owner = cell(sheet, r, c_sales) or cell(sheet, r, c_created) or "(none)"
                basis = "salesperson" if cell(sheet, r, c_sales) else "created by"
                print(f"    AWAITING DAY 0  {job} · {cell(sheet, r, c_customer)} · "
                      f"owner {owner} ({basis})")
            if "REWORK" in tags_text.upper():
                rework += 1
        print(f"  {rows} open jobs · {tagged} tagged AFTER-CARE · {rework} open rework")
        print(f"  rows with a populated Saleperson: {sales_filled} of {rows}")
    return True


def main():
    token = get_access_token(
        get_env("GRAPH_TENANT_ID"), get_env("GRAPH_CLIENT_ID"), get_env("GRAPH_CLIENT_SECRET")
    )
    base_url = f"https://graph.microsoft.com/v1.0/users/{urllib.parse.quote(get_env('MAILBOX_ADDRESS'))}"

    folder_id = fetch_child_folder_id(token, base_url, "Mechanic Desk Reports")
    messages = fetch_recent_messages(token, base_url, folder_id, top=50)
    print(f"Fetched {len(messages)} recent messages from Mechanic Desk Reports.\n")

    wanted = {}
    for m in messages:
        match = SUBJECT_PATTERN.match(m["subject"].strip())
        if not match:
            continue
        location_label, raw_type = match.groups()
        report_type = match_report_type(raw_type)
        if report_type and location_label in LOCATION_KEY:
            # newest first, so the first hit for each pair is the latest
            wanted.setdefault((LOCATION_KEY[location_label], report_type), m)

    if not wanted:
        print("No Job or Job Wip report emails matched. Subjects actually in the folder:")
        for m in messages[:25]:
            print(f"  {m['subject']}")
        sys.exit(1)

    problems = 0
    with tempfile.TemporaryDirectory() as tmp:
        for (location, report_type), m in sorted(wanted.items()):
            print("=" * 70)
            print(f"{location.upper()} · {report_type} · received {m['receivedDateTime']} "
                  f"(Perth {perth_date(m['receivedDateTime'])})")
            print("=" * 70)
            path = download_attachment(token, base_url, m["id"], tmp)
            if not path:
                print("  no .xls attachment could be downloaded\n")
                problems += 1
                continue
            sheet = xlrd.open_workbook(path).sheet_by_index(0)
            print(f"  file: {os.path.basename(path)}  ({sheet.nrows} rows x {sheet.ncols} cols)")
            if not report_on(sheet, report_type):
                problems += 1
            print()

    print("=" * 70)
    if problems:
        print(f"{problems} report(s) had a column problem -- the real header rows above are what "
              f"the After-Care parsers need to match.")
        sys.exit(1)
    print("All Job and Job WIP reports read successfully. Nothing was written.")


if __name__ == "__main__":
    main()
