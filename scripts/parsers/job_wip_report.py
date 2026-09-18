"""
Parses a MechanicDesk daily Job WIP Report (.xls) -- the open-job book.

Two things the After-Care control needs from here and nowhere else:

1. ENROLMENT AHEAD OF DAY 0. A job carrying the AFTER-CARE tag that has
   not yet been finalised is an incomplete Day 0 under QJT-001 clause 34
   and must not generate obligations -- but it is real forward workload
   and belongs on the Bridge as "awaiting Day 0" rather than being
   invisible until the invoice lands.

2. THE OWNER. TWOS-AC-001 makes the Build Consultant the relationship
   owner, but MechanicDesk's Saleperson column is filled on only a
   minority of open jobs -- 17 of Bunbury's 98 and none of Busselton's
   35 at the 17 September 2026 close, and on none of the jobs then in or
   awaiting the programme. So this parser returns the Saleperson where
   there is one, falls back to the booking's Created By, and records
   which of the two it used. The Bridge shows a fallback as a proxy,
   never as a confirmed assignment.

The WIP book is also where an open Rework - Review Required tag is
visible, which is what "unresolved concerns" tests against.

Real column layout (confirmed against exported reports for both
locations, 16 and 17 September 2026):

Job No.# | Created By | Created At | Order No.# | Description |
Start Date | Mechanics | Customer | Vehicle | Make | Model | Year |
Color | Registration Number | Fleet Number | Total Labour Cost |
Total Stock Cost | Total Buyin Cost | Total Non Stock Cost | Total WIP |
Remaining Timesheet Amount | Invoice Total | Saleperson | Tags |
Related Bills

A NOTE ON TAG TRUNCATION, SINCE IT COST A WRONG ANSWER ONCE
Read through a text extraction of this report, the Tags column is cut at
roughly 26 characters, which hid AFTER-CARE on two Bunbury jobs whose
other tags ran long (BUNJOB3465 and BUNJOB3510) and produced a count of
6 where the truth was 8. Reading the raw .xls cell, as this parser does,
returns the whole string -- confirmed against both locations by the
18 September 2026 probe run. So no truncation compensation belongs here:
the counts this parser produces are totals, not floors. Never read these
reports through an extracted-text path.

This module only parses; it does not fetch or write anything itself.
"""

import datetime
import xlrd

from .columns import build_index, resolve, find_header_row

AFTER_CARE_TAG = "AFTER-CARE"
REWORK_TAG_PREFIX = "REWORK"


def _parse_date(value, datemode=0):
    if value is None or value == "":
        return None
    if isinstance(value, float):
        try:
            parts = xlrd.xldate_as_tuple(value, datemode)
            return datetime.date(parts[0], parts[1], parts[2])
        except Exception:
            return None
    text = str(value).strip().split(" ")[0]
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d/%m/%y"):
        try:
            return datetime.datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _split_tags(raw):
    return [t.strip() for t in str(raw or "").split(",") if t.strip()]


def parse_job_wip_report(file_path):
    """
    Returns:
      {
        "openJobs": int,
        "afterCareOpen": [ ...job dicts... ],
        "reworkOpen":    [ ...job dicts... ],
        "salespersonPopulated": int,   # rows with a real Saleperson value
      }
    """
    wb = xlrd.open_workbook(file_path)
    sheet = wb.sheet_by_index(0)
    datemode = wb.datemode

    header_row_idx, header = find_header_row(sheet, ["Job No", "Tags"])
    idx = build_index(header)

    c_job = resolve(idx, ["Job No.#", "Job No."], header)
    c_tags = resolve(idx, ["Tags"], header)
    c_customer = resolve(idx, ["Customer"], header)

    c_created_by = resolve(idx, ["Created By"], header, required=False)
    c_sales = resolve(idx, ["Saleperson", "Salesperson"], header, required=False)
    c_desc = resolve(idx, ["Description"], header, required=False)
    c_start = resolve(idx, ["Start Date"], header, required=False)
    c_vehicle = resolve(idx, ["Vehicle"], header, required=False)
    c_rego = resolve(idx, ["Registration Number"], header, required=False)
    c_total = resolve(idx, ["Invoice Total"], header, required=False)
    c_mech = resolve(idx, ["Mechanics"], header, required=False)

    def cell(r, c):
        if c is None:
            return ""
        return str(sheet.cell_value(r, c) or "").strip()

    after_care_open = []
    rework_open = []
    open_jobs = 0
    salesperson_populated = 0

    for r in range(header_row_idx + 1, sheet.nrows):
        job_no = cell(r, c_job)
        if not job_no or job_no.lower().startswith("total"):
            continue
        open_jobs += 1

        tags = _split_tags(sheet.cell_value(r, c_tags))

        sales = cell(r, c_sales)
        if sales:
            salesperson_populated += 1

        start = _parse_date(sheet.cell_value(r, c_start), datemode) if c_start is not None else None
        record = {
            "job": job_no,
            "description": cell(r, c_desc),
            "customer": cell(r, c_customer),
            "vehicle": cell(r, c_vehicle),
            "registration": cell(r, c_rego),
            "tags": tags,
            "startDate": start.isoformat() if start else None,
            "invoiceTotal": float(sheet.cell_value(r, c_total) or 0) if c_total is not None else None,
            "mechanics": cell(r, c_mech),
            "owner": sales or cell(r, c_created_by),
            "ownerBasis": "salesperson" if sales else "created_by",
        }

        if any(t.upper() == AFTER_CARE_TAG for t in tags):
            after_care_open.append(record)
        if any(t.upper().startswith(REWORK_TAG_PREFIX) for t in tags):
            rework_open.append(record)

    return {
        "openJobs": open_jobs,
        "afterCareOpen": after_care_open,
        "reworkOpen": rework_open,
        "salespersonPopulated": salesperson_populated,
    }


if __name__ == "__main__":
    import sys
    import json
    print(json.dumps(parse_job_wip_report(sys.argv[1]), indent=2))
