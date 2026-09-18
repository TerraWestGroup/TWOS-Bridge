"""
One-off seed of the After-Care state block: moves TWOS-AC-001 from v1.0
to v1.1 and plants the cycles that already exist in MechanicDesk but
cannot be recovered by the nightly run.

WHY A SEED IS NEEDED AT ALL
The nightly ingestion reads the day's Job Report, which contains only
that day's jobs. BUSJOB2202 was finalised with the AFTER-CARE tag on
17 September 2026; by the time the first nightly run with After-Care
support executes, that job is no longer in any report it reads. Without
seeding it, TerraWest's first real After-Care cycle would be lost and
Marc Papalia's 72-hour call would never be raised.

Every seeded value below was read directly from the MechanicDesk
exports for the 17 September close (Busselton Job Report, and both
locations' Job WIP books) -- nothing here is estimated.

Run once:  python scripts/seed_aftercare_state.py
It is idempotent; re-running will not duplicate the cycle.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import aftercare

STATE_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "twos-state.json")
SEED_DATE = "2026-09-18"

# Busselton, 17 Sep 2026 close: tagged SLIDEAWAY, AFTER-CARE with
# Invoice Finalized = TRUE. The only completed After-Care enrolment in
# the group to date.
SEED_FINALISED = [{
    "job": "BUSJOB2202",
    "invoiceNo": "BUSINV3475",
    "finishedDate": "2026-09-17",
    "customer": "MARC PAPALIA",
    "mobile": "0408109984",
    "phone": "0408109984",
    "vehicle": "2026 BYD SHARK",
    "registration": "1JBF666",
    "description": "ROLLER SHUTTER, SUPPLY AND FIT",
    "tags": ["SLIDEAWAY", "AFTER-CARE"],
    "mechanics": "Jayden Brookes",
    "createdBy": "Mitch Cooper",
}]

# Bunbury Job WIP, 17 Sep 2026 close: tagged AFTER-CARE, not yet
# finalised. Refreshed from the WIP book on every subsequent run; seeded
# here only so the Bridge is correct before the first nightly run.
#
# All eight were confirmed by the 18 September 2026 probe run reading the
# raw .xls. Two of them -- BUNJOB3510 and BUNJOB3465 -- are the jobs an
# earlier reading missed, because their tag strings were long enough to
# push AFTER-CARE past a text extraction's truncation point.
SEED_AWAITING = [
    {"job": "BUNJOB3395", "customer": "Pambou Constructions", "vehicle": "2025 FORD RANGER WILDTRAK Dual Cab",
     "description": "TC Boxes Tray & Canopy Build", "startDate": "2026-09-14", "invoiceTotal": 30838.43,
     "owner": "Jelena Gordon", "ownerBasis": "created_by", "location": "bunbury",
     "tags": ["TC BOXES BUILD", "AFTER-CARE"]},
    {"job": "BUNJOB3430", "customer": "Marty Lamb", "vehicle": "2025 FORD RANGER WILDTRAK",
     "description": "Premium Raid Canopy & Raid Roof Rack", "startDate": "2026-09-17", "invoiceTotal": 9089.49,
     "owner": "Luke Bleasdale", "ownerBasis": "created_by", "location": "bunbury",
     "tags": ["CANOPY", "ROOF RACK", "AFTER-CARE"]},
    {"job": "BUNJOB3473", "customer": "Tyler Woolcock", "vehicle": "2023 Isuzu D-Max",
     "description": "Deluxe Bull Bar", "startDate": "2026-09-18", "invoiceTotal": 2934.40,
     "owner": "Luke Bleasdale", "ownerBasis": "created_by", "location": "bunbury",
     "tags": ["BULLBAR", "AFTER-CARE"]},
    {"job": "BUNJOB3502", "customer": "Josh Krome", "vehicle": "2026 BYD SHARK 6 Performance 2.0L",
     "description": "Suspension", "startDate": "2026-09-22", "invoiceTotal": 3835.00,
     "owner": "Jelena Gordon", "ownerBasis": "created_by", "location": "bunbury",
     "tags": ["SUSPENSION", "ORDERED", "AFTER-CARE"]},
    {"job": "BUNJOB3510", "customer": "Jack Biondi", "vehicle": "2017 Mitsubishi Triton",
     "description": "Long Range Fuel Tank", "startDate": "2026-09-22", "invoiceTotal": 2304.75,
     "owner": "Jelena Gordon", "ownerBasis": "created_by", "location": "bunbury",
     "tags": ["LONG RANGE FUEL TANK", "ORDERED", "AFTER-CARE"]},
    {"job": "BUNJOB3465", "customer": "Rob Nielsen", "vehicle": "2012 TOYOTA 200S LANDCRUISER GX",
     "description": "Drawers, Cargo Barrier, Fridge Cage, Neo Spot Lights", "startDate": "2026-09-24",
     "invoiceTotal": 6322.60,
     "owner": "Jelena Gordon", "ownerBasis": "created_by", "location": "bunbury",
     "tags": ["ORDERED", "DRAWER SYSTEM", "AFTER-CARE"]},
    {"job": "BUNJOB3514", "customer": "Bradley Mitchem", "vehicle": "2022 ISUZU MUX",
     "description": "Bull Bar", "startDate": "2026-10-02", "invoiceTotal": 4014.00,
     "owner": "Jelena Gordon", "ownerBasis": "created_by", "location": "bunbury",
     "tags": ["ORDERED", "BULLBAR", "AFTER-CARE"]},
    {"job": "BUNJOB3462", "customer": "Garret Piper", "vehicle": "2024 RAM 1500 DT LARAMIE",
     "description": "Long Range Tank", "startDate": "2026-10-07", "invoiceTotal": 3653.82,
     "owner": "Jelena Gordon", "ownerBasis": "created_by", "location": "bunbury",
     "tags": ["LONG RANGE FUEL TANK", "AFTER-CARE"]},
]

# Governance matters that no report can detect. Marked origin=declared so
# the nightly run preserves them instead of overwriting them with its own
# computed exceptions.
DECLARED_EXCEPTIONS = [{
    "id": "handout-v10",
    "origin": "declared",
    "severity": "critical",
    "owner": "DN06 Growth",
    "title": "The customer handout still promises the v1.0 journey",
    "detail": "TerraWest_After-Care_Customer_Handout_v1.0 promises a 24-hour check, a 3-month call and a "
              "36-month Build Health Check. v1.1 moves first contact to 72 hours, removes the 3-month call "
              "and downgrades 24-36 months to ambient marketing. Any handout already given to a customer is "
              "a promise TWOS will not schedule. Reissue the handout against v1.1, or restore the 3-month "
              "call to v1.1, before the programme's automated obligations go live.",
}]


def main():
    with open(STATE_PATH) as f:
        state = json.load(f)

    ac = state["afterCare"]

    ac["program"]["version"] = "1.1"
    ac["program"]["status"] = "Executive Standard — Approved Design"
    ac["program"]["sourceDocument"] = "TWOS_Customer_After-Care_Program_v1.1.docx"
    ac["program"]["tagStandard"] = "QJT-001 v1.1 (GOLD - AFTER-CARE)"
    ac["program"]["changeNote"] = (
        "v1.1 follows the 4-11 September 2026 team review: first contact 24h -> 72h, the 3-month call "
        "removed, 18 months passive, the 10% voucher moved to 24 months at 6 months' validity, and "
        "24-36 months ambient marketing only."
    )

    ac["milestones"] = [
        {
            "code": m["code"], "label": m["label"], "tier": m["tier"],
            "type": m["type"], "owner": m["owner"], "rule": m["rule"],
            "dueRule": m["dueRule"], "conditional": m["conditional"],
        }
        for m in aftercare.MILESTONES
    ]

    ac["eligibility"] = {
        "decisionOwner": "Build Consultant",
        "trigger": "GOLD AFTER-CARE tag applied at job finalisation (QJT-001 7A)",
        "dayZero": "Job finalisation date, where the tag is present and the invoice is finalised",
        "minimumReference": "Bull-bar installation scale/significance; consultant judgement applies",
        "restartRule": "A later qualifying build on the same vehicle starts a new Day 0 and supersedes the "
                       "earlier schedule without deleting vehicle history",
        "incompleteDayZeroRule": "A job tagged AFTER-CARE without a finalisation date must not drive "
                                 "scheduled obligations (QJT-001 clause 34)",
    }

    register, added, _ = aftercare.merge_register(
        ac.get("register") or [], SEED_FINALISED, "busselton", SEED_DATE
    )
    for cycle in register:
        if cycle["job"] in added:
            cycle["seededFrom"] = "Busselton Job Report, 17 Sep 2026 close"

    ac["register"] = register
    ac["awaitingDayZero"] = SEED_AWAITING
    ac["reworkOpen"] = ac.get("reworkOpen") or []
    ac["outcomes"] = ac.get("outcomes") or {}

    position = aftercare.compute_position(
        register, SEED_AWAITING, ac["reworkOpen"], SEED_DATE, ac["outcomes"]
    )
    ac["operatingPosition"] = {
        "activeCycles": position["activeCycles"],
        "dueToday": position["dueToday"],
        "completedToday": position["completedToday"],
        "overdue": position["overdue"],
        "unresolvedConcerns": position["unresolvedConcerns"],
        "awaitingDayZero": position["awaitingDayZero"],
        "recurringQualitySignals": None,
        "ownerAttentionRequired": False,
        "ownerAttentionReason": "No After-Care matter requires Owner attention; obligations sit with the "
                                "accountable consultants.",
    }
    ac["dueTodayList"] = position["dueTodayList"]
    ac["overdueList"] = position["overdueList"]
    ac["upcomingList"] = position["upcomingList"]
    ac["suppressedList"] = position["suppressedList"]
    ac["concernList"] = position["concernList"]

    ac["integration"] = {
        "status": "connected_tested",
        "statusLabel": "MechanicDesk AFTER-CARE tag and job finalisation connected",
        "requiredSource": "MechanicDesk daily Job Report (tag + finalisation) and Job WIP Report "
                          "(enrolment, owner, open rework)",
        "requiredAdapter": "scripts/parsers/job_report.py, scripts/parsers/job_wip_report.py, scripts/aftercare.py",
        "lastSuccessfulIngestionAt": "2026-09-18T03:02:00Z",
        "locationsRead": ["bunbury", "busselton"],
        "holidayTableCoverageTo": aftercare.HOLIDAY_TABLE_COVERAGE_TO,
        "seededAt": SEED_DATE,
    }

    # Tracked separately from the tag feed on purpose: the Bridge can now
    # raise obligations, but nothing in MechanicDesk records that one was
    # met, so it must not imply it knows.
    ac["outcomeCapture"] = {
        "status": "not_connected",
        "statusLabel": "Obligations can be raised; outcomes cannot yet be recorded",
        "limitation": "MechanicDesk holds no field for a completed After-Care contact, so Overdue currently "
                      "means a due date passed without a recorded outcome -- not a proven missed obligation.",
        "options": [
            "A MechanicDesk note convention the ingestion parses",
            "A Planner task per obligation, closed by the consultant",
            "A TWOS-side outcome store written back to twos-state.json",
        ],
        "decisionOwner": "Lindsay Allan / DN06",
    }

    existing_ids = {e.get("id") for e in (ac.get("exceptions") or [])}
    ac["exceptions"] = [e for e in (ac.get("exceptions") or []) if e.get("origin") == "declared"]
    for exception in DECLARED_EXCEPTIONS:
        if exception["id"] not in existing_ids:
            ac["exceptions"].append(exception)

    with open(STATE_PATH, "w") as f:
        json.dump(state, f, indent=2)
        f.write("\n")

    print(f"Seeded After-Care state: {position['activeCycles']} active cycle(s), "
          f"{position['awaitingDayZero']} awaiting Day 0, "
          f"{len(ac['exceptions'])} declared exception(s).")
    for m in register[0]["schedule"]:
        flag = "" if m["generatesObligation"] else "   (no obligation)"
        print(f"  {register[0]['job']} {m['label']:<12} due {m['due']}{flag}")


if __name__ == "__main__":
    main()
