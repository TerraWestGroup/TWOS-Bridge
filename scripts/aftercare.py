"""
The After-Care milestone engine (TWOS-AC-001 v1.1 / QJT-001 v1.1).

Turns MechanicDesk job evidence into the DN06 operating position the
Bridge renders. Three responsibilities, deliberately separated:

  build_schedule()   one job's Day 0 -> its seven dated milestones
  merge_register()   today's finalised jobs -> the persistent cycle register
  compute_position() the register + today -> tiles, due list, exceptions

WHY A PERSISTENT REGISTER
The daily Job Report only contains jobs touched that day. A cycle that
started in September is simply absent from October's report. So the
register accumulates in data/twos-state.json and is appended to, never
rebuilt from a single day's file -- losing it would lose every Day 0
that is not in today's export.

WHAT THIS ENGINE WILL NOT DO
It does not infer that an obligation was met. MechanicDesk has no field
recording that a 72-hour call happened or how it went, so "overdue"
here means a due date passed with no recorded outcome -- not a proven
miss. The outcomes map is read if present and stays empty until an
outcome-capture path is chosen; the Bridge states this limitation
rather than implying the stronger meaning.
"""

import copy
import datetime

# --- v1.1 journey -----------------------------------------------------
# Changed from v1.0 after the 4-11 September team review (Luke Bleasdale,
# Jelena Gordon, Mitch Cooper, Drew Baker): first contact moved 24h -> 72h,
# the 3-month call was removed, 18 months became passive, the voucher moved
# to 24 months at 6 months' validity, and 24-36 months became ambient
# marketing only.
MILESTONES = [
    {
        "code": "72H", "label": "72 hours", "tier": "active",
        "offset": {"hours": 72},
        "type": "Genuine Build Consultant call",
        "owner": "Build Consultant",
        "rule": "Confirm satisfaction, function, understanding and concerns. No answer -> approved SMS, attempt recorded.",
        "dueRule": "Next business day where 72 hours lands on a weekend or public holiday",
        "conditional": False,
    },
    {
        "code": "1M", "label": "1 month", "tier": "active",
        "offset": {"months": 1},
        "type": "Conditional technical follow-up",
        "owner": "Workshop / Consultant",
        "rule": "Retorque or technical follow-up where applicable. Fixed at 1 month -- no km-based trigger exists in MechanicDesk. A completed required retorque satisfies and suppresses this milestone.",
        "dueRule": "Next business day; technical requirement takes precedence",
        "conditional": True,
    },
    {
        "code": "6M", "label": "6 months", "tier": "active",
        "offset": {"months": 6},
        "type": "Build Health Check + consultation",
        "owner": "Consultant + Fitter",
        "rule": "Morning preferred; allow customer 30 minutes; fitter allocation approximately 15 minutes; complimentary general inspection and coffee.",
        "dueRule": "Six-month business-day milestone",
        "conditional": False,
    },
    {
        "code": "12M", "label": "12 months", "tier": "active",
        "offset": {"months": 12},
        "type": "Build Health Check + consultation",
        "owner": "Consultant + Fitter",
        "rule": "Relationship-led physical check under the Build Health Check standard.",
        "dueRule": "Twelve-month business-day milestone",
        "conditional": False,
    },
    {
        "code": "18M", "label": "18 months", "tier": "passive",
        "offset": {"months": 18},
        "type": "Conditional contact only",
        "owner": "Build Consultant",
        "rule": "No scheduled obligation. Raised only where vehicle history, an open concern or a recorded customer trigger warrants it.",
        "dueRule": "Passive -- generates no consultant obligation",
        "conditional": False,
    },
    {
        "code": "24M", "label": "24 months", "tier": "active",
        "offset": {"months": 24},
        "type": "Build Health Check + 10% voucher",
        "owner": "Consultant + Fitter",
        "rule": "Final active touchpoint. 10% voucher issued here, valid 6 months, not combinable with other offers.",
        "dueRule": "Twenty-four-month business-day milestone",
        "conditional": False,
    },
    {
        "code": "AMBIENT", "label": "24-36 months", "tier": "ambient",
        "offset": {"months": 36},
        "type": "Newsletter and referral credit only",
        "owner": "DN06 Growth",
        "rule": "No consultant obligation generated. Customer remains in ordinary marketing contact, subject to do-not-contact and unresolved-issue suppression.",
        "dueRule": "Window from the 24-month milestone to cycle end",
        "conditional": False,
    },
]

# The 1-month technical follow-up APPLIES BY DEFAULT to anything the
# workshop physically fitted. An earlier version of this engine had it
# the other way round -- an allowlist of suspension, GVM and airbag work,
# suppressing everything else -- which was wrong: a roller shutter, bull
# bar, canopy, roof rack, drawer system or towbar is bolted to the
# vehicle and its fasteners want checking and adjusting after a month of
# real use just as much as a leaf pack does. Getting that default
# backwards means silently skipping a check on most qualifying builds.
#
# The asymmetry matters. An unnecessary check costs a phone call; a
# missed one on a bolted structural accessory is a quality and safety
# matter. So the milestone stands unless the job is evidenced as
# non-fitment work.
#
# Suppression requires the controlled SUPPLY ONLY tag (QJT-001 section 7,
# BLUE - SUPPLY ONLY: "No workshop fitment is required") -- not a guess
# from the description. These items simply raise the check from routine
# to safety-critical in the stated basis, so the consultant and fitter
# know which kind of visit it is.
HIGH_TORQUE_KEYWORDS = (
    "SUSPENSION", "GVM", "AIRBAG", "AIR BAG", "LEAF", "SHOCK", "COIL",
    "STRUT", "LIFT KIT", "TORSION", "ADD-A-LEAF", "ADD A LEAF",
    "BULLBAR", "BULL BAR", "TOWBAR", "TOW BAR", "RECOVERY POINT",
    "ROOF RACK", "WHEEL", "BRAKE",
)

SUPPLY_ONLY_TAG = "SUPPLY ONLY"

# Western Australian public holidays. New Year's Day, Australia Day,
# Anzac Day, Christmas and Boxing Day are computed (with the standard
# substitute-day rule); Labour Day and WA Day are computed from their
# fixed Monday rules. The King's Birthday in WA is PROCLAIMED ANNUALLY
# and moves -- it is listed explicitly and must be extended each year.
# HOLIDAY_TABLE_COVERAGE_TO is published into the state file so the
# Bridge can say when this table needs maintaining instead of silently
# shifting a milestone onto a public holiday.
PROCLAIMED_HOLIDAYS = {
    2026: ["2026-09-28"],
    2027: ["2027-09-27"],
    2028: ["2028-09-25"],
    2029: ["2029-09-24"],
}
HOLIDAY_TABLE_COVERAGE_TO = "2029-12-31"


def _easter(year):
    """Anonymous Gregorian algorithm -- returns Easter Sunday."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return datetime.date(year, month, day)


def _nth_weekday(year, month, weekday, n):
    """n-th given weekday of a month (weekday: Monday=0)."""
    d = datetime.date(year, month, 1)
    offset = (weekday - d.weekday()) % 7
    return d + datetime.timedelta(days=offset + 7 * (n - 1))


def _substitute(d):
    """A fixed-date holiday falling on a weekend is observed on Monday."""
    if d.weekday() == 5:
        return d + datetime.timedelta(days=2)
    if d.weekday() == 6:
        return d + datetime.timedelta(days=1)
    return d


def wa_holidays(year):
    """Returns the set of observed WA public holidays for a year."""
    easter = _easter(year)
    days = {
        _substitute(datetime.date(year, 1, 1)),                 # New Year's Day
        _substitute(datetime.date(year, 1, 26)),                # Australia Day
        _nth_weekday(year, 3, 0, 1),                            # Labour Day (1st Mon in March)
        easter - datetime.timedelta(days=2),                    # Good Friday
        easter,                                                 # Easter Sunday
        easter + datetime.timedelta(days=1),                    # Easter Monday
        datetime.date(year, 4, 25),                             # Anzac Day (not substituted in WA)
        _nth_weekday(year, 6, 0, 1),                            # WA Day (1st Mon in June)
        _substitute(datetime.date(year, 12, 25)),               # Christmas Day
        _substitute(datetime.date(year, 12, 26)),               # Boxing Day
    }
    for iso in PROCLAIMED_HOLIDAYS.get(year, []):
        days.add(datetime.date.fromisoformat(iso))
    return days


def is_business_day(d):
    return d.weekday() < 5 and d not in wa_holidays(d.year)


def next_business_day(d):
    """
    Bunbury and Busselton do not trade a normal weekday on weekends, so a
    milestone landing on a weekend or public holiday moves forward to the
    next trading day rather than being actioned on a day nobody is there.
    """
    shifted = d
    guard = 0
    while not is_business_day(shifted):
        shifted += datetime.timedelta(days=1)
        guard += 1
        if guard > 30:
            break
    return shifted


def add_months(d, months):
    """
    Calendar months, not 30-day blocks. Clamps to the end of a shorter
    target month: 31 January + 1 month = 28 (or 29) February.
    """
    total = d.month - 1 + months
    year = d.year + total // 12
    month = total % 12 + 1
    day = d.day
    while True:
        try:
            return datetime.date(year, month, day)
        except ValueError:
            day -= 1


def retorque_applicable(description, tags):
    """
    Decides whether the conditional 1-month technical follow-up applies,
    and returns (applicable, basis).

    Applies by default: anything fitted to the vehicle gets a physical
    check. Only an explicit SUPPLY ONLY tag suppresses it, because that
    is the one controlled signal that the workshop fitted nothing. The
    basis string is carried into the state either way, so the consultant
    sees the reasoning and can overrule it.
    """
    tag_list = [str(t).strip().upper() for t in (tags or [])]
    if SUPPLY_ONLY_TAG in tag_list:
        return False, "Tagged SUPPLY ONLY -- nothing was fitted by us, so there is nothing to check"

    haystack = " ".join([str(description or "")] + tag_list).upper()
    hits = sorted({k for k in HIGH_TORQUE_KEYWORDS if k in haystack})
    if hits:
        return True, (
            f"Load-bearing fitment ({', '.join(h.title() for h in hits)}) -- "
            f"retorque required, not just an adjustment check"
        )
    return True, "Fitted work -- check fastener torque, alignment and adjustment after a month's use"


def build_schedule(day_zero_iso, description="", tags=None):
    """
    Returns the full dated milestone schedule for one cycle.

    Day 0 is the job's finalisation date (QJT-001 7A). Raw offsets are
    reported alongside the business-day-shifted date so the shift is
    visible rather than implied.
    """
    day_zero = datetime.date.fromisoformat(day_zero_iso)
    applicable, basis = retorque_applicable(description, tags)
    schedule = []

    for m in MILESTONES:
        if "hours" in m["offset"]:
            # Day 0 is held as a date, so an hours offset is expressed in
            # whole days (72h = 3 days) rather than pretending to a
            # precision the finalisation timestamp does not survive with.
            raw = day_zero + datetime.timedelta(days=m["offset"]["hours"] // 24)
        else:
            raw = add_months(day_zero, m["offset"]["months"])

        entry = {
            "code": m["code"],
            "label": m["label"],
            "tier": m["tier"],
            "type": m["type"],
            "owner": m["owner"],
            "rawDue": raw.isoformat(),
            "due": next_business_day(raw).isoformat(),
            "conditional": m["conditional"],
        }
        entry["shifted"] = entry["due"] != entry["rawDue"]

        if m["code"] == "AMBIENT":
            entry["windowStart"] = next_business_day(add_months(day_zero, 24)).isoformat()
            entry["windowEnd"] = entry["due"]
            entry["generatesObligation"] = False
        else:
            entry["generatesObligation"] = m["tier"] == "active"

        if m["conditional"]:
            entry["applicable"] = applicable
            entry["applicabilityBasis"] = basis
            if not applicable:
                entry["generatesObligation"] = False

        schedule.append(entry)

    return schedule


def merge_register(existing, finalised_jobs, location, today_iso):
    """
    Appends newly finalised AFTER-CARE jobs to the persistent register.

    Idempotent: re-running the same day's report changes nothing. Handles
    the QJT-001 7A re-tag rule -- a new qualifying build on the same
    registration starts a new Day 0 and supersedes the earlier schedule
    without deleting the earlier record, so vehicle history is preserved.

    Returns (register, added_job_numbers, superseded_job_numbers).
    """
    # Deep copy: supersession mutates existing cycle records, and a
    # shallow copy would reach back into the caller's own list. The
    # register is small (one record per qualifying build) so the cost is
    # irrelevant next to keeping this a pure function.
    register = copy.deepcopy(list(existing or []))
    by_job = {c["job"]: c for c in register}
    added, superseded = [], []

    for job in finalised_jobs:
        job_no = job["job"]
        if job_no in by_job:
            continue

        rego = (job.get("registration") or "").strip().upper()
        if rego:
            for cycle in register:
                if (cycle.get("registration") or "").strip().upper() == rego \
                        and not cycle.get("supersededBy"):
                    cycle["supersededBy"] = job_no
                    cycle["supersededAt"] = today_iso
                    superseded.append(cycle["job"])

        cycle = {
            "job": job_no,
            "location": location,
            "invoiceNo": job.get("invoiceNo") or None,
            "dayZero": job["finishedDate"],
            "customer": job.get("customer") or None,
            "mobile": job.get("mobile") or job.get("phone") or None,
            "vehicle": job.get("vehicle") or None,
            "registration": job.get("registration") or None,
            "description": job.get("description") or None,
            "tags": job.get("tags") or [],
            "owner": job.get("owner") or job.get("createdBy") or None,
            "ownerBasis": job.get("ownerBasis") or "created_by",
            "fitter": job.get("mechanics") or None,
            "enrolledAt": today_iso,
            "supersededBy": None,
            "schedule": build_schedule(
                job["finishedDate"], job.get("description"), job.get("tags")
            ),
        }
        register.append(cycle)
        by_job[job_no] = cycle
        added.append(job_no)

    register.sort(key=lambda c: (c["dayZero"], c["job"]), reverse=True)
    return register, added, superseded


def refresh_schedules(register):
    """
    Recomputes every cycle's schedule from its Day 0.

    The register stores FACTS -- Day 0, customer, vehicle, owner, the tags
    the job carried at finalisation. The schedule is DERIVED from those,
    so it is rebuilt on every run rather than frozen at enrolment. Without
    this, a change to the journey or to a milestone's applicability rule
    would apply only to cycles enrolled after the change, and every
    existing customer would silently stay on the superseded schedule --
    which is exactly what happened when the 1-month rule was corrected.

    Safe to do because nothing cycle-specific lives in the schedule:
    recorded outcomes are held separately in afterCare.outcomes, keyed by
    job and milestone code.

    Returns the number of cycles whose schedule actually changed.
    """
    changed = 0
    for cycle in register:
        if not cycle.get("dayZero"):
            continue
        rebuilt = build_schedule(
            cycle["dayZero"], cycle.get("description"), cycle.get("tags")
        )
        if rebuilt != cycle.get("schedule"):
            cycle["schedule"] = rebuilt
            changed += 1
    return changed


def _outcome_key(job, code):
    return f"{job}|{code}"


def compute_position(register, awaiting, rework_open, today_iso, outcomes=None):
    """
    Derives the operating position the Bridge tiles show.

    active cycles       cycles with a Day 0, not superseded, inside 36 months
    due today           active obligations dated today with no recorded outcome
    overdue             active obligations dated before today with no outcome
    unresolved concerns open Rework - Review Required jobs on a vehicle that
                        is in an active cycle
    awaiting Day 0      tagged but unfinalised jobs (QJT-001 clause 34)
    """
    outcomes = outcomes or {}
    today = datetime.date.fromisoformat(today_iso)

    active_cycles, due_today, overdue, upcoming, suppressed = [], [], [], [], []

    for cycle in register:
        if cycle.get("supersededBy"):
            continue
        day_zero = datetime.date.fromisoformat(cycle["dayZero"])
        if add_months(day_zero, 36) < today:
            continue
        active_cycles.append(cycle)

        for m in cycle["schedule"]:
            if not m.get("generatesObligation"):
                # A conditional milestone that was judged not applicable is
                # surfaced rather than dropped, with its reasoning, so the
                # consultant can see what was suppressed and overrule it.
                # Passive and ambient tiers are by design, not suppression.
                if m.get("conditional") and m.get("applicable") is False:
                    suppressed.append({
                        "job": cycle["job"],
                        "location": cycle["location"],
                        "customer": cycle["customer"],
                        "vehicle": cycle["vehicle"],
                        "registration": cycle["registration"],
                        "mobile": cycle["mobile"],
                        "owner": cycle["owner"],
                        "ownerBasis": cycle["ownerBasis"],
                        "milestone": m["label"],
                        "code": m["code"],
                        "type": m["type"],
                        "due": m["due"],
                        "basis": m.get("applicabilityBasis"),
                    })
                continue
            if outcomes.get(_outcome_key(cycle["job"], m["code"])):
                continue
            due = datetime.date.fromisoformat(m["due"])
            item = {
                "job": cycle["job"],
                "location": cycle["location"],
                "customer": cycle["customer"],
                "vehicle": cycle["vehicle"],
                "registration": cycle["registration"],
                "mobile": cycle["mobile"],
                "owner": cycle["owner"],
                "ownerBasis": cycle["ownerBasis"],
                "fitter": cycle["fitter"],
                "milestone": m["label"],
                "code": m["code"],
                "type": m["type"],
                "due": m["due"],
                "dayZero": cycle["dayZero"],
            }
            if due < today:
                item["daysLate"] = (today - due).days
                overdue.append(item)
            elif due == today:
                due_today.append(item)
            else:
                item["daysAway"] = (due - today).days
                upcoming.append(item)

    active_regos = {
        (c.get("registration") or "").strip().upper()
        for c in active_cycles if c.get("registration")
    }
    active_jobs = {c["job"] for c in active_cycles}
    concerns = [
        r for r in (rework_open or [])
        if (r.get("registration") or "").strip().upper() in active_regos
        or r.get("job") in active_jobs
    ]

    upcoming.sort(key=lambda x: x["due"])
    overdue.sort(key=lambda x: x["due"])
    suppressed.sort(key=lambda x: x["due"])

    return {
        "activeCycles": len(active_cycles),
        "dueToday": len(due_today),
        "completedToday": len([
            k for k, v in outcomes.items() if (v or {}).get("at", "").startswith(today_iso)
        ]),
        "overdue": len(overdue),
        "unresolvedConcerns": len(concerns),
        "awaitingDayZero": len(awaiting or []),
        "dueTodayList": due_today,
        "overdueList": overdue,
        "upcomingList": upcoming[:10],
        "suppressedList": suppressed,
        "concernList": concerns,
    }
