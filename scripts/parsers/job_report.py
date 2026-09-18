"""
Parses a MechanicDesk daily Job Report (.xls) -- the report that carries
job finalisation, and therefore After-Care Day 0.

Per QJT-001 v1.1 section 7A, the GOLD AFTER-CARE tag is applied by the
Build Consultant AT JOB FINALISATION, and "job finalisation + AFTER-CARE
tag present = Day 0 of the three-year After-Care cycle". Section 12
clause 34 is equally explicit that a job tagged AFTER-CARE WITHOUT a
finalisation date is an incomplete Day 0 and must not drive scheduled
obligations. Both halves of that rule live here: this parser reports
what each job's tag and finalisation state actually are, and refuses to
infer either.

Real column layout (confirmed against exported reports for both
locations, 16 and 17 September 2026):

Booking Created At | Booking Created By | Booking Date | Job Date |
Finished Date | Job No. | Order No. | Description | Job Types | Tags |
Mechanics | Customer | Phone | Mobile | Special | Account No. |
Source Of Business | Vehicle | Registration Number | Fleet Number |
Invoice No. | Invoice Finalized | Total Labour(Timesheet) Hours |
Total Labour(Timesheet) Charged | Estimate Hours | Total | Paid |
Outstanding | Quoted Total | COGS | Profit Margin Percentage |
Estimate Efficiency

Only the identity, tag, contact and finalisation columns are read; the
financial columns already reach the Bridge through the Income Report
and are deliberately not duplicated here.

This module only parses; it does not fetch or write anything itself.
"""

import datetime
import xlrd

from .columns import build_index, resolve, find_header_row

AFTER_CARE_TAG = "AFTER-CARE"


def _parse_date(value, datemode=0):
    """
    MechanicDesk writes dates either as dd/mm/yyyy text (sometimes with a
    trailing time) or as a real Excel serial. Returns a date, or None.
    """
    if value is None or value == "":
        return None
    if isinstance(value, float):
        try:
            parts = xlrd.xldate_as_tuple(value, datemode)
            return datetime.date(parts[0], parts[1], parts[2])
        except Exception:
            return None
    text = str(value).strip()
    if not text:
        return None
    text = text.split(" ")[0]
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d/%m/%y"):
        try:
            return datetime.datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _truthy(value):
    """Invoice Finalized comes through as TRUE/FALSE text or a boolean."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().upper() in ("TRUE", "YES", "Y", "1")


def _split_tags(raw):
    return [t.strip() for t in str(raw or "").split(",") if t.strip()]


def parse_job_report(file_path):
    """
    Returns {"jobs": [...], "afterCareFinalised": [...]} where each job is:

      job, orderNo, description, tags, hasAfterCareTag, invoiceNo,
      invoiceFinalised, finishedDate (ISO or None), jobDate, customer,
      phone, mobile, vehicle, registration, mechanics, createdBy

    afterCareFinalised is the subset that satisfies BOTH halves of the
    Day 0 rule -- tag present AND invoice finalised AND a real finished
    date. Those are the only rows entitled to start a cycle.
    """
    wb = xlrd.open_workbook(file_path)
    sheet = wb.sheet_by_index(0)
    datemode = wb.datemode

    header_row_idx, header = find_header_row(sheet, ["Job No", "Tags"])
    idx = build_index(header)

    c_job = resolve(idx, ["Job No.#", "Job No."], header)
    c_tags = resolve(idx, ["Tags"], header)
    c_final = resolve(idx, ["Invoice Finalized", "Invoice Finalised"], header)
    c_finished = resolve(idx, ["Finished Date"], header)
    c_customer = resolve(idx, ["Customer"], header)

    # Optional columns: present in every export seen so far, but the
    # cycle is still valid without them, so a missing one degrades the
    # record rather than failing the whole ingestion.
    c_desc = resolve(idx, ["Description"], header, required=False)
    c_invoice = resolve(idx, ["Invoice No.#", "Invoice No."], header, required=False)
    c_jobdate = resolve(idx, ["Job Date"], header, required=False)
    c_phone = resolve(idx, ["Phone"], header, required=False)
    c_mobile = resolve(idx, ["Mobile"], header, required=False)
    c_vehicle = resolve(idx, ["Vehicle"], header, required=False)
    c_rego = resolve(idx, ["Registration Number"], header, required=False)
    c_mech = resolve(idx, ["Mechanics"], header, required=False)
    c_created = resolve(idx, ["Booking Created By", "Created By"], header, required=False)
    c_order = resolve(idx, ["Order No.#", "Order No."], header, required=False)

    def cell(r, c):
        if c is None:
            return ""
        return str(sheet.cell_value(r, c) or "").strip()

    jobs = []
    for r in range(header_row_idx + 1, sheet.nrows):
        job_no = cell(r, c_job)
        if not job_no or job_no.lower().startswith("total"):
            continue
        tags = _split_tags(sheet.cell_value(r, c_tags))
        finished = _parse_date(sheet.cell_value(r, c_finished), datemode)
        jobs.append({
            "job": job_no,
            "orderNo": cell(r, c_order),
            "description": cell(r, c_desc),
            "tags": tags,
            "hasAfterCareTag": any(t.upper() == AFTER_CARE_TAG for t in tags),
            "invoiceNo": cell(r, c_invoice),
            "invoiceFinalised": _truthy(sheet.cell_value(r, c_final)),
            "finishedDate": finished.isoformat() if finished else None,
            "jobDate": (lambda d: d.isoformat() if d else None)(
                _parse_date(sheet.cell_value(r, c_jobdate), datemode) if c_jobdate is not None else None
            ),
            "customer": cell(r, c_customer),
            "phone": cell(r, c_phone),
            "mobile": cell(r, c_mobile),
            "vehicle": cell(r, c_vehicle),
            "registration": cell(r, c_rego),
            "mechanics": cell(r, c_mech),
            "createdBy": cell(r, c_created),
        })

    after_care_finalised = [
        j for j in jobs
        if j["hasAfterCareTag"] and j["invoiceFinalised"] and j["finishedDate"]
    ]

    return {"jobs": jobs, "afterCareFinalised": after_care_finalised}


if __name__ == "__main__":
    import sys
    import json
    print(json.dumps(parse_job_report(sys.argv[1]), indent=2))
