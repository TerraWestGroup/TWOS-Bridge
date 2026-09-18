"""
Self-contained checks for the After-Care ingestion path.

Run with:  python scripts/test_aftercare.py

Covers the parts that would otherwise fail silently and wrongly: the
milestone arithmetic, the WA business-day shift, the Day 0 rule from
QJT-001 clause 34, the re-tag supersession rule, and both new parsers
against synthetic workbooks built to the documented column layout.

The synthetic workbooks verify parser LOGIC, not MechanicDesk's exact
header spelling -- that is why the parsers resolve columns tolerantly
and raise an error naming every column found. Run
scripts/probe_job_reports.py against the live mailbox to confirm the
real headers before trusting a first production run.
"""

import datetime
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(__file__))

import xlwt

from aftercare import (
    build_schedule, merge_register, compute_position, refresh_schedules,
    add_months, next_business_day, is_business_day, wa_holidays,
    retorque_applicable,
)
from parsers.job_report import parse_job_report
from parsers.job_wip_report import parse_job_wip_report

FAILURES = []


def check(label, actual, expected):
    if actual == expected:
        print(f"  ok    {label}")
    else:
        print(f"  FAIL  {label}\n          expected: {expected!r}\n          actual:   {actual!r}")
        FAILURES.append(label)


def section(title):
    print(f"\n{title}")


# --------------------------------------------------------------------
section("Calendar arithmetic")

check("31 Jan + 1 month clamps to 28 Feb (non-leap)",
      add_months(datetime.date(2027, 1, 31), 1), datetime.date(2027, 2, 28))
check("31 Jan + 1 month clamps to 29 Feb (leap)",
      add_months(datetime.date(2028, 1, 31), 1), datetime.date(2028, 2, 29))
check("17 Sep 2026 + 24 months",
      add_months(datetime.date(2026, 9, 17), 24), datetime.date(2028, 9, 17))

section("WA business days")

check("Sat 19 Sep 2026 is not a business day", is_business_day(datetime.date(2026, 9, 19)), False)
check("Sun 20 Sep 2026 shifts to Mon 21 Sep",
      next_business_day(datetime.date(2026, 9, 20)), datetime.date(2026, 9, 21))
check("Anzac Day 2026 is a holiday", datetime.date(2026, 4, 25) in wa_holidays(2026), True)
check("Good Friday 2027 is 26 Mar", datetime.date(2027, 3, 26) in wa_holidays(2027), True)
check("Christmas 2027 (Sat) observed Mon 27 Dec",
      datetime.date(2027, 12, 27) in wa_holidays(2027), True)
check("Proclaimed King's Birthday 28 Sep 2026 is a holiday",
      datetime.date(2026, 9, 28) in wa_holidays(2026), True)
check("Milestone landing on the 2026 King's Birthday shifts to Tue 29 Sep",
      next_business_day(datetime.date(2026, 9, 28)), datetime.date(2026, 9, 29))

# --------------------------------------------------------------------
section("Milestone schedule -- BUSJOB2202, Day 0 = 17 Sep 2026")

schedule = build_schedule("2026-09-17", "ROLLER SHUTTER, SUPPLY AND FIT", ["SLIDEAWAY", "AFTER-CARE"])
by_code = {m["code"]: m for m in schedule}

check("seven milestones generated", len(schedule), 7)
check("72h raw date is Sun 20 Sep", by_code["72H"]["rawDue"], "2026-09-20")
check("72h shifts to Mon 21 Sep", by_code["72H"]["due"], "2026-09-21")
check("72h is flagged as shifted", by_code["72H"]["shifted"], True)
check("1 month raw date is Sat 17 Oct", by_code["1M"]["rawDue"], "2026-10-17")
check("1 month shifts to Mon 19 Oct", by_code["1M"]["due"], "2026-10-19")
check("6 months lands Wed 17 Mar 2027 unshifted", by_code["6M"]["due"], "2027-03-17")
check("12 months lands Fri 17 Sep 2027", by_code["12M"]["due"], "2027-09-17")
check("24 months (Sun) shifts to Mon 18 Sep 2028", by_code["24M"]["due"], "2028-09-18")
check("18 months is passive", by_code["18M"]["tier"], "passive")
check("18 months raises no obligation", by_code["18M"]["generatesObligation"], False)
check("ambient window ends 36 months out", by_code["AMBIENT"]["windowEnd"], "2029-09-17")
check("ambient raises no obligation", by_code["AMBIENT"]["generatesObligation"], False)
check("a fitted roller shutter DOES get the 1-month check", by_code["1M"]["applicable"], True)
check("and therefore raises an obligation", by_code["1M"]["generatesObligation"], True)

section("Conditional 1-month applicability -- applies by default")

# The rule is deliberately inverted from an earlier version: anything
# fitted gets the check, and only an explicit SUPPLY ONLY tag suppresses
# it. A missed retorque on a bolted accessory is a safety matter; an
# unnecessary check is a phone call.
check("suspension work applies",
      retorque_applicable("Rear Suspension Upgrade", ["SUSPENSION"])[0], True)
check("GVM work applies", retorque_applicable("GVM Upgrade - Pre-Rego", ["GVM UPGRADE"])[0], True)
check("a bull bar applies", retorque_applicable("Deluxe Bull Bar", ["BULLBAR"])[0], True)
check("a roller shutter applies", retorque_applicable("ROLLER SHUTTER, SUPPLY AND FIT", ["SLIDEAWAY"])[0], True)
check("a canopy applies", retorque_applicable("Premium Raid Canopy", ["CANOPY"])[0], True)
check("a drawer system applies", retorque_applicable("Drawers, Cargo Barrier", ["DRAWER SYSTEM"])[0], True)
check("a UHF fit still applies -- it was fitted",
      retorque_applicable("UHF", ["MISC"])[0], True)
check("SUPPLY ONLY is the one thing that suppresses it",
      retorque_applicable("Bull Bar -- SUPPLY ONLY", ["BULLBAR", "SUPPLY ONLY"])[0], False)

check("load-bearing fitment is named as a retorque",
      "retorque required" in retorque_applicable("Deluxe Bull Bar", ["BULLBAR"])[1], True)
check("other fitment is named as an adjustment check",
      "adjustment" in retorque_applicable("UHF", ["MISC"])[1], True)
check("a suppressed milestone says why",
      "nothing was fitted" in retorque_applicable("Bar", ["SUPPLY ONLY"])[1], True)

supply_only = {m["code"]: m for m in build_schedule("2026-09-17", "Bull Bar", ["SUPPLY ONLY", "AFTER-CARE"])}
check("a supply-only 1M raises no obligation", supply_only["1M"]["generatesObligation"], False)

# --------------------------------------------------------------------
section("Register merge and the Day 0 rule")

finalised = [{
    "job": "BUSJOB2202", "invoiceNo": "BUSINV3475", "finishedDate": "2026-09-17",
    "customer": "Marc Papalia", "mobile": "0408109984",
    "vehicle": "2026 BYD SHARK", "registration": "1JBF666",
    "description": "ROLLER SHUTTER, SUPPLY AND FIT",
    "tags": ["SLIDEAWAY", "AFTER-CARE"], "mechanics": "Jayden Brookes",
    "createdBy": "Mitch Cooper",
}]

register, added, superseded = merge_register([], finalised, "busselton", "2026-09-18")
check("one cycle enrolled", added, ["BUSJOB2202"])
check("nothing superseded on a first enrolment", superseded, [])
check("Day 0 taken from the finished date", register[0]["dayZero"], "2026-09-17")
check("owner falls back to the booking creator", register[0]["owner"], "Mitch Cooper")

register2, added2, _ = merge_register(register, finalised, "busselton", "2026-09-19")
check("re-running the same day is idempotent", added2, [])
check("register still holds one cycle", len(register2), 1)

retag = [dict(finalised[0], job="BUSJOB2400", invoiceNo="BUSINV3600", finishedDate="2027-02-10")]
register3, added3, superseded3 = merge_register(register2, retag, "busselton", "2027-02-11")
check("a later qualifying build enrols", added3, ["BUSJOB2400"])
check("the earlier cycle on that rego is superseded", superseded3, ["BUSJOB2202"])
check("the superseded cycle is retained, not deleted", len(register3), 2)

section("Rule changes reach cycles enrolled before them")

# The register stores Day 0; the schedule is derived. A cycle carrying a
# schedule built under superseded rules must be rebuilt, or every
# existing customer silently stays on the old journey.
stale = [dict(register[0])]
stale[0]["schedule"] = [m for m in stale[0]["schedule"] if m["code"] != "1M"]
check("a stale schedule is detected and rebuilt", refresh_schedules(stale), 1)
check("the rebuilt schedule has all seven milestones", len(stale[0]["schedule"]), 7)
check("rebuilding is idempotent once current", refresh_schedules(stale), 0)
check("Day 0 is untouched by a rebuild", stale[0]["dayZero"], "2026-09-17")

# --------------------------------------------------------------------
section("Operating position")

awaiting = [{"job": f"BUNJOB{n}"} for n in range(3390, 3398)]
position = compute_position(register, awaiting, [], "2026-09-18")
check("active cycles", position["activeCycles"], 1)
check("due today", position["dueToday"], 0)
check("overdue", position["overdue"], 0)
check("unresolved concerns", position["unresolvedConcerns"], 0)
check("awaiting Day 0", position["awaitingDayZero"], 8)
check("next upcoming obligation is the 72-hour call",
      position["upcomingList"][0]["code"], "72H")
check("next upcoming obligation is dated Mon 21 Sep",
      position["upcomingList"][0]["due"], "2026-09-21")

on_the_day = compute_position(register, awaiting, [], "2026-09-21")
check("on 21 Sep the 72-hour call is due today", on_the_day["dueToday"], 1)

later = compute_position(register, awaiting, [], "2026-09-25")
check("unactioned, it becomes overdue", later["overdue"], 1)
check("overdue carries days late", later["overdueList"][0]["daysLate"], 4)

closed = compute_position(
    register, awaiting, [], "2026-09-25",
    outcomes={"BUSJOB2202|72H": {"outcome": "Contacted", "at": "2026-09-21T10:15:00+08:00", "by": "Mitch Cooper"}},
)
check("a recorded outcome clears the overdue item", closed["overdue"], 0)

concern = compute_position(
    register, awaiting,
    [{"job": "BUSJOB2500", "registration": "1JBF666", "tags": ["Rework - Review Required"]}],
    "2026-09-25",
)
check("a rework on an enrolled vehicle is an unresolved concern",
      concern["unresolvedConcerns"], 1)

unrelated = compute_position(
    register, awaiting,
    [{"job": "BUSJOB2196", "registration": "AU35957", "tags": ["Rework - Review Required"]}],
    "2026-09-25",
)
check("a rework on a vehicle outside the programme is not",
      unrelated["unresolvedConcerns"], 0)

expired = compute_position(register, awaiting, [], "2029-10-01")
check("a cycle past 36 months is no longer active", expired["activeCycles"], 0)

# --------------------------------------------------------------------
section("Parsers against synthetic workbooks")

JOB_HEADER = [
    "Booking Created At", "Booking Created By", "Booking Date", "Job Date",
    "Finished Date", "Job No.", "Order No.", "Description", "Job Types",
    "Tags", "Mechanics", "Customer", "Phone", "Mobile", "Special",
    "Account No.", "Source Of Business", "Vehicle", "Registration Number",
    "Fleet Number", "Invoice No.", "Invoice Finalized",
]
JOB_ROWS = [
    ["02/09/2026 11:46", "Mitch Cooper", "17/09/2026", "17/09/2026",
     "17/09/2026 13:10", "BUSJOB2202", "", "ROLLER SHUTTER, SUPPLY AND FIT",
     "ROLLER SHUTTER", "SLIDEAWAY, AFTER-CARE", "Jayden Brookes",
     "MARC PAPALIA", "0408109984", "0408109984", "", "", "",
     "2026 BYD SHARK 1JBF666", "1JBF666", "", "BUSINV3475", "TRUE"],
    # Tagged but NOT finalised -- must not start a cycle (QJT-001 cl.34)
    ["09/09/2026 16:05", "Luke Bleasdale", "18/09/2026", "18/09/2026",
     "", "BUNJOB3473", "", "Deluxe Bull Bar", "BULLBAR",
     "BULLBAR, AFTER-CARE", "Samuel Sutton", "Tyler Woolcock", "", "", "",
     "", "", "2023 Isuzu D-Max", "", "", "BUNINV5600", "FALSE"],
    # Finalised but untagged -- consultant judged it does not qualify
    ["16/09/2026 08:00", "Jelena Gordon", "16/09/2026", "16/09/2026",
     "16/09/2026 16:30", "BUNJOB3481", "", "UHF", "UHF", "MISC",
     "Samuel Sutton", "Lisa Jolly", "0437783620", "0437783620", "", "", "",
     "MY23 SUBARU FORRESTER", "1QCG328", "", "BUNINV5528", "TRUE"],
]

WIP_HEADER = [
    "Job No.#", "Created By", "Created At", "Order No.#", "Description",
    "Start Date", "Mechanics", "Customer", "Vehicle", "Make", "Model",
    "Year", "Color", "Registration Number", "Fleet Number",
    "Total Labour Cost", "Total Stock Cost", "Total Buyin Cost",
    "Total Non Stock Cost", "Total WIP", "Remaining Timesheet Amount",
    "Invoice Total", "Saleperson", "Tags",
]
WIP_ROWS = [
    ["BUNJOB3473", "Luke Bleasdale", "04/09/2026 15:36", "", "Deluxe Bull Bar",
     "18/09/2026", "Samuel Sutton", "Tyler Woolcock", "2023 Isuzu D-Max",
     "Isuzu", "D-Max", "2023", "", "", "", 0, 0, 1138.72, 0, 1138.72, 0,
     2934.40, "", "BULLBAR, AFTER-CARE"],
    ["BUSJOB2196", "Mitch Cooper", "31/08/2026 09:42", "", "AIRBAG REFIT / REINSTALL?",
     "22/09/2026", "", "Voyager Estate", "2024 TOYOTA HILUX", "TOYOTA",
     "HILUX", "2024", "", "AU35957", "", 0, 0, 0, 0, 0, 0, 0, "",
     "Rework - Review Required, AIR BAGS"],
    ["BUNJOB3437", "Luke Bleasdale", "21/08/2026 12:52", "", "Side Steps",
     "30/10/2026", "", "Tim Cooper", "2020 Nissan Patrol", "Nissan",
     "Patrol", "2020", "", "", "", 0, 0, 572.35, 0, 572.35, 0, 1249.40,
     "Luke Bleasdale", "SIDE STEPS"],
]


def write_workbook(path, header, rows):
    book = xlwt.Workbook()
    sheet = book.add_sheet("Sheet1")
    for c, name in enumerate(header):
        sheet.write(0, c, name)
    for r, row in enumerate(rows, start=1):
        for c, value in enumerate(row):
            sheet.write(r, c, value)
    book.save(path)


with tempfile.TemporaryDirectory() as tmp:
    job_path = os.path.join(tmp, "job.xls")
    wip_path = os.path.join(tmp, "wip.xls")
    write_workbook(job_path, JOB_HEADER, JOB_ROWS)
    write_workbook(wip_path, WIP_HEADER, WIP_ROWS)

    jr = parse_job_report(job_path)
    check("job report reads all rows", len(jr["jobs"]), 3)
    check("only the finalised tagged job starts a cycle",
          [j["job"] for j in jr["afterCareFinalised"]], ["BUSJOB2202"])
    check("finished date parsed from a dd/mm/yyyy hh:mm cell",
          jr["afterCareFinalised"][0]["finishedDate"], "2026-09-17")
    check("mobile captured for the SMS fallback path",
          jr["afterCareFinalised"][0]["mobile"], "0408109984")
    check("tag split handles a multi-tag cell",
          jr["jobs"][0]["tags"], ["SLIDEAWAY", "AFTER-CARE"])
    check("an untagged finalised job is not enrolled",
          jr["jobs"][2]["hasAfterCareTag"], False)

    wip = parse_job_wip_report(wip_path)
    check("wip counts every open job", wip["openJobs"], 3)
    check("wip finds the tagged unfinalised job",
          [j["job"] for j in wip["afterCareOpen"]], ["BUNJOB3473"])
    check("wip owner falls back to created by",
          wip["afterCareOpen"][0]["ownerBasis"], "created_by")
    check("wip finds the open rework",
          [j["job"] for j in wip["reworkOpen"]], ["BUSJOB2196"])
    check("wip counts populated salesperson cells", wip["salespersonPopulated"], 1)
    check("a long multi-tag string is read whole, not truncated",
          wip["afterCareOpen"][0]["tags"], ["BULLBAR", "AFTER-CARE"])

# --------------------------------------------------------------------
print()
if FAILURES:
    print(f"{len(FAILURES)} check(s) FAILED: {', '.join(FAILURES)}")
    sys.exit(1)
print("All After-Care checks passed.")
