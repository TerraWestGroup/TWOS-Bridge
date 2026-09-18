# TWOS Bridge

The TerraWest Operating System (TWOS) Bridge — a static control-centre site for TerraWest Group, deployed to Azure Static Web Apps.

## How it works

- `index.html` — the display layer. Fetches `data/twos-state.json` at runtime and renders the Bridge (Operating Board, Owner Command, Wave 2 Shadow, Cash Flow & Budget, Podium, After-Care, Control Room, etc).
- `data/twos-state.json` — the persistent evidence state. This is the file that must be refreshed with new evidence (MechanicDesk, Xero/dataSights, Podium, ANZ Worldline) for the Bridge to show current information. Right now this is a manually-updated snapshot.
- `assets/` — brand assets (logo).
- `.github/workflows/azure-static-web-apps-deploy.yml` — deploys the site to Azure Static Web Apps on every push to `main`, using the `AZURE_STATIC_WEB_APPS_API_TOKEN` repo secret.

## Deployment

This repo deploys directly via the Azure Static Web Apps deployment token (not a linked GitHub integration created through the Azure portal). The token lives in this repo's **Settings → Secrets and variables → Actions** as `AZURE_STATIC_WEB_APPS_API_TOKEN`.

Any push to `main` re-deploys the whole site, including whatever is currently in `data/twos-state.json`.

## Nightly ingestion

`.github/workflows/nightly-ingest.yml` runs at 11:30 UTC (19:30 AWST), after the ~18:10 AWST MechanicDesk delivery. It runs `scripts/nightly_ingest.py`, which reads the MechanicDesk reports, the Podium Daily Digest, the ANZ Worldline settlement PDF, the manually-sent Quote Reports and Xero, merges what it has real evidence for into `data/twos-state.json`, commits, and deploys.

Each source degrades independently: a failure in one leaves the rest of the state refreshed and the failed section unchanged, rather than taking the whole close down.

## DN04 workshop evidence

Productivity and Efficiency are ratios, so they hide their own denominator. 100% productivity over 5.09 recorded hours and 100% over a full workshop day are the same number and very different facts. On the 17 September 2026 close Bunbury read 100% productivity and 157.17% efficiency, struck over 5.09 hours from one fitter, and the Divisions Overview showed only the percentages.

Both parsers now return `recordedHours`, and `assess_workshop_coverage()` in `nightly_ingest.py` publishes it alongside `fitterCount` so every percentage carries what it was computed over: `100.0% · 5.09 h · 1 fitter`.

**No threshold is set on what counts as enough.** These exports carry no rostered hours or capacity data, so the code does not decide that a thin day means missing timesheets — it publishes the hours and leaves the judgement to whoever reads them. Fitter count alone means nothing either: a genuinely quiet day worked by one fitter is complete evidence, and the reports routinely carry two or three fitters (three at Bunbury on 9 September).

The one thing it does assert is a contradiction: a fitter named on the day's jobs who recorded no hours at all. That marks the evidence `partial` and `attributionSafe` false. It is the test the 2 September 2026 Wave 2 Shadow assessment applied by hand, concluding "performance attribution is prohibited until coverage is reconciled". `scripts/test_workshop_coverage.py` pins the rule.

Wave 2 Shadow itself remains a hand-written snapshot that no job refreshes; connecting it is separate and larger work.

## DN06 Customer After-Care

The After-Care control (TWOS-AC-001 v1.1, tag control QJT-001 v1.1) is driven entirely by the GOLD `AFTER-CARE` tag in MechanicDesk — there is no separate customer database.

| File | Role |
| --- | --- |
| `scripts/parsers/job_report.py` | Day 0 detection — tag present **and** invoice finalised **and** a real finished date |
| `scripts/parsers/job_wip_report.py` | Enrolment awaiting Day 0, owner attribution, open reworks |
| `scripts/aftercare.py` | The milestone engine — journey definition, WA business-day shifting, the cycle register, the operating position |
| `scripts/test_aftercare.py` | Checks for all of the above, including both parsers against synthetic workbooks |
| `scripts/probe_job_reports.py` | Read-only diagnostic against the live mailbox; prints the real report headers and what it finds. Self-contained — it carries its own small parsing logic rather than importing the production parsers, so it can be added or run on its own |
| `scripts/seed_aftercare_state.py` | One-off seed of cycles that pre-date this feed |

### The standard is the authority, not this code

`TWOS-AC-001 Customer After-Care Program v1.1` lives in SharePoint under `01 Controlled Standards/Carté Marketing Knowledge`. It carries a sensitivity label, so tooling that cannot decrypt it will not be able to read it — check it in the browser, and check the engine against it rather than the other way round. Two rules in it are easy to get wrong and were got wrong here first:

- **The 1-month milestone is unconditional.** Section 4: it "always occurs at 1 month regardless of any earlier suspension retorque". Section 5: no km-based trigger, no early-completion skip. Nothing suppresses it — not supply-only, not an absence of load-bearing parts, not a retorque already done at 500 km. `aftercare.py` therefore contains no suppression logic for it at all, and `LOAD_BEARING_KEYWORDS` only describes what kind of visit it is.
- **Milestones move to the NEAREST business day, not the next.** Section 5. A Saturday milestone moves back to the Friday; a Sunday one moves forward to the Monday. Moving everything forward puts every Saturday milestone two days late. Ties resolve forward so nothing is actioned before it matures.

### Two rules that matter

**The register is appended to, never rebuilt.** The daily Job Report contains only that day's jobs, so a cycle that started last month is simply absent from today's export. `afterCare.register` in the state file is the system of record for Day 0 — losing it loses every cycle not in today's report. This is why `seed_aftercare_state.py` exists: BUSJOB2202 was finalised on 17 September 2026, before this feed was built, and would otherwise never have raised its 72-hour call.

**A tagged job is not a cycle.** QJT-001 clause 34: a job tagged `AFTER-CARE` without a finalisation date is an incomplete Day 0 and must not drive obligations. Those appear on the Bridge as *Awaiting Day 0* and enrol the moment their invoice is finalised.

### What is connected and what is not

The tag and finalisation feed is connected (`afterCare.integration`). Outcome capture is not (`afterCare.outcomeCapture`) — MechanicDesk holds no field recording that a 72-hour call happened or how it went. Until one of the three options listed in that block is chosen, **Overdue means a due date passed with no recorded outcome, not a proven missed obligation**, and the Bridge says so on screen. These are deliberately two separate statuses so the Bridge cannot imply it knows something it does not.

### Before trusting a changed report

Run the **Probe Job Reports** workflow from the Actions tab. It prints the live reports' real header rows and what it finds, and writes nothing. The production parsers resolve columns tolerantly and raise an error naming every column actually present, so a spelling change surfaces as a diagnosable warning rather than a silently missing field — but the probe tells you before a nightly run does.

Run `python scripts/test_aftercare.py` for the engine checks; it needs `xlrd` and `xlwt` and touches nothing outside the process.

### Never read these reports through extracted text

The 18 September 2026 probe settled this. Read through a text extraction, the Job WIP report's Tags column is cut at roughly 26 characters, which hid `AFTER-CARE` on two Bunbury jobs whose other tags ran long and produced an enrolment count of 6 against a true 8. Read from the raw `.xls` attachment, as the parsers do, the whole tag string comes through. Any future tooling that reaches these reports by any other route needs checking against this.

The same run corrected a second assumption worth recording: MechanicDesk's `Saleperson` field is not universally empty — it was filled on 17 of Bunbury's 98 open jobs and none of Busselton's 35 — but it was empty on every job then in or awaiting the programme. That is why the owner-proxy exception is computed from the programme's own jobs rather than from a global count, which would never have fired.

### Maintaining the holiday table

`aftercare.PROCLAIMED_HOLIDAYS` carries the WA King's Birthday, which is proclaimed annually and moves. Everything else (including Easter) is computed. The table currently covers to the date published as `afterCare.integration.holidayTableCoverageTo`; extend it before that date passes, or milestones may be scheduled onto a public holiday.

## Note: logo asset

`assets/terrawest-group-logo.png` is referenced by `index.html` but not yet included in this repo — add the actual TerraWest Group logo file there (same filename) to restore the sidebar/header branding.
