"""
Checks for the workshop evidence-coverage test.

Run with:  python scripts/test_workshop_coverage.py

Productivity and Efficiency aggregate over whoever recorded hours. If one
fitter logs time and three worked, the location reads 100% productivity
and that number is true of one person. These checks pin the rule that
decides whether such a figure may stand as a location measure.

The 2 September 2026 Wave 2 Shadow assessment applied this test by hand
and concluded "performance attribution is prohibited until coverage is
reconciled". The same condition was still true on 17 September, at both
locations, with nothing reporting it.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from nightly_ingest import assess_workshop_coverage

FAILURES = []


def check(label, actual, expected):
    if actual == expected:
        print(f"  ok    {label}")
    else:
        print(f"  FAIL  {label}\n          expected: {expected!r}\n          actual:   {actual!r}")
        FAILURES.append(label)


def prod(names, hours=8.0):
    return {"productivityPercent": 100.0, "productivityCoverage": names, "recordedHours": hours}


def eff(names, hours=8.0):
    return {"efficiencyPercent": 150.0, "efficiencyCoverage": names, "recordedHours": hours}


def jobs(*mechanics):
    return {"jobs": [{"mechanics": m} for m in mechanics]}


print("\nFull coverage")
r = assess_workshop_coverage(
    prod("Daniel Fowles, Jack Rooney, Samuel Sutton"),
    eff("Daniel Fowles, Jack Rooney, Samuel Sutton"),
    jobs("Daniel Fowles", "Jack Rooney, Samuel Sutton"),
)
check("three covered, three worked -> current", r["evidenceStatus"], "current")
check("attribution is safe", r["attributionSafe"], True)
check("fitter count travels with the figure", r["fitterCount"], 3)

print("\nA fitter worked but logged no hours")
r = assess_workshop_coverage(
    prod("Zach Hutchison"),
    eff("Zach Hutchison"),
    jobs("Aaron Leonard, Jayden Brookes", "Zach Hutchison"),
)
check("-> partial", r["evidenceStatus"], "partial")
check("attribution is not safe", r["attributionSafe"], False)
check("the missing fitters are named",
      "Aaron Leonard" in r["coverageNote"] and "Jayden Brookes" in r["coverageNote"], True)

print("\nA quiet day worked by one fitter is complete, not partial")
# The real 17 Sep 2026 Bunbury position: Daniel Fowles recorded 5.09
# hours and is the only fitter named on the day's jobs. Nothing
# contradicts the evidence, so it is not partial -- but the percentage
# must carry the 5.09 hours it was struck over, because a ratio hides
# its own denominator.
r = assess_workshop_coverage(prod("Daniel Fowles", 5.09), eff("Daniel Fowles", 5.09),
                             jobs("Daniel Fowles", "Daniel Fowles", ""))
check("-> current, not partial", r["evidenceStatus"], "current")
check("no missing fitter is alleged", r["attributionSafe"], True)
check("recorded hours travel with the figure", r["recordedHours"], 5.09)
check("and appear in the note", "5.09 recorded hours" in r["coverageNote"], True)
check("fitter count is 1", r["fitterCount"], 1)

print("\nNo threshold is invented for what counts as enough hours")
r = assess_workshop_coverage(prod("Daniel Fowles", 0.25), eff("Daniel Fowles", 0.25),
                             jobs("Daniel Fowles"))
check("a quarter-hour day is still not called partial", r["evidenceStatus"], "current")
check("the hours are simply published", r["recordedHours"], 0.25)

print("\nEdge cases")
r = assess_workshop_coverage(prod("Aaron Leonard, Jayden Brookes"), None, jobs("aaron leonard"))
check("name matching ignores case", r["attributionSafe"], True)
check("two covered with no contradiction -> current", r["evidenceStatus"], "current")
check("hours survive a missing efficiency report", r["recordedHours"], 8.0)

r = assess_workshop_coverage(None, None, jobs("Daniel Fowles"))
check("no reports at all -> hours are null, not zero", r["recordedHours"], None)

r = assess_workshop_coverage(prod("Aaron Leonard, Jayden Brookes"), None, jobs("", None))
check("jobs with no mechanic recorded raise nothing", r["evidenceStatus"], "current")

r = assess_workshop_coverage(prod("Aaron Leonard, Jayden Brookes"), None, None)
check("a missing Job report cannot clear coverage", r["evidenceStatus"], "current")
check("and does not invent a missing fitter", r["attributionSafe"], True)

print()
if FAILURES:
    print(f"{len(FAILURES)} check(s) FAILED: {', '.join(FAILURES)}")
    sys.exit(1)
print("All workshop-coverage checks passed.")
