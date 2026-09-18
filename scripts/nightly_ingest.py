"""
Nightly TWOS Bridge ingestion.

1. Authenticates to Microsoft Graph (app-only, client credentials).
2. Finds the most recent day's MechanicDesk Income / Productivity /
   Efficiency report emails for both Bunbury and Busselton in the
   "Mechanic Desk Reports" folder.
3. Downloads and parses each .xls attachment.
4. Merges the results into data/twos-state.json, updating only the
   fields we have real evidence for (locations.*.revenue,
   locations.*.workshop, latestAccountableDate, sources.mechanicDesk),
   leaving every other section of the file (podium, wave2Shadow,
   afterCare, financialControl, etc.) untouched.

This script does not commit or push; the calling workflow does that,
so this stays testable/runnable in isolation.
"""

import os
import sys
import re
import json
import base64
import datetime
import tempfile
import urllib.parse

sys.path.insert(0, os.path.dirname(__file__))
from graph_client import get_access_token, graph_get, graph_get_all_pages
from parsers.income_report import parse_income_report
from parsers.productivity_report import parse_productivity_report
from parsers.efficiency_report import parse_efficiency_report
from parsers.podium_digest import parse_podium_digest
from parsers.worldline_settlement import parse_worldline_settlement
from parsers.quote_report import parse_quote_report
from parsers.job_report import parse_job_report
from parsers.job_wip_report import parse_job_wip_report
import aftercare
from xero_client import get_access_token as xero_get_access_token, get_tenant_id as xero_get_tenant_id, xero_get
from xero_financial import get_bank_account_balance, get_payables_summary, get_profit_and_loss_summary, BANK_ACCOUNT_OF_INTEREST

STATE_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "twos-state.json")

# Subject lines look like: "ATTN: Bunbury 4x4, Your daily Income Report"
SUBJECT_PATTERN = re.compile(r"ATTN:\s*(Bunbury 4x4|Busselton 4x4),\s*Your daily (.+?) Report\s*$")

LOCATION_KEY = {
    "Bunbury 4x4": "bunbury",
    "Busselton 4x4": "busselton",
}

REPORT_PARSERS = {
    "Income": parse_income_report,
    "Productivity": parse_productivity_report,
    "Efficiency": parse_efficiency_report,
}

# DN06 Customer After-Care reads two further MechanicDesk reports. They
# are kept separate from REPORT_PARSERS above so that the mechanicDesk
# source-health count keeps meaning exactly what it meant before
# (6/6 Income/Productivity/Efficiency), and an After-Care parse failure
# degrades only the After-Care control rather than the whole close.
AFTER_CARE_PARSERS = {
    "Job": parse_job_report,
    "Job Wip": parse_job_wip_report,
}


def normalise_report_type(report_type):
    """
    MechanicDesk's subject lines are not perfectly consistent in case
    ("Job Wip" vs "Job WIP"), so report types are matched case-folded
    and mapped back to the canonical key used by the parser tables.
    """
    folded = " ".join(report_type.split()).casefold()
    for known in list(REPORT_PARSERS) + list(AFTER_CARE_PARSERS):
        if known.casefold() == folded:
            return known
    return None


def get_env(name):
    value = os.environ.get(name)
    if not value:
        print(f"ERROR: missing required environment variable {name}", file=sys.stderr)
        sys.exit(1)
    return value


def perth_date(utc_iso):
    """Converts a UTC ISO timestamp to its Australia/Perth (UTC+8, no DST) calendar date."""
    dt = datetime.datetime.fromisoformat(utc_iso.replace("Z", "+00:00"))
    perth_dt = dt + datetime.timedelta(hours=8)
    return perth_dt.date().isoformat()


def fetch_child_folder_id(token, base_url, folder_name, parent="Inbox"):
    """
    parent="Inbox" (default) looks under Inbox's child folders, matching
    Mechanic Desk Reports / Podium / ANZ Worldline. parent=None looks at
    the mailbox's top-level folders instead (siblings of Inbox itself),
    which is where the Quote Report folders (Luke Bleasedale, Mitch
    Cooper) turned out to actually live.
    """
    list_url = f"{base_url}/mailFolders?$top=100" if parent is None else f"{base_url}/mailFolders/{parent}/childFolders?$top=50"
    children = graph_get(token, list_url).get("value", [])
    target = folder_name.strip().casefold()
    folder = next(
        (f for f in children if f["displayName"].strip().casefold() == target),
        None,
    )
    if not folder:
        location = "top level of the mailbox" if parent is None else f"under {parent}"
        available = ", ".join(f"'{f['displayName']}'" for f in children) or "(none)"
        raise ValueError(
            f"'{folder_name}' folder not found at {location}. "
            f"Folders actually present there: {available}"
        )
    return folder["id"]


def fetch_child_folder_id_anywhere(token, base_url, folder_name, parents=(None, "Inbox")):
    """
    Tries each parent location in turn (None = top level of the mailbox,
    or a folder name whose child folders are searched) and returns the
    first match. Folder locations have moved before (the Quote Report
    folders), so this avoids re-hardcoding an assumption that could go
    stale again -- if none match, the error lists what was actually
    found in every location tried.
    """
    attempts = []
    for parent in parents:
        try:
            return fetch_child_folder_id(token, base_url, folder_name, parent=parent)
        except ValueError as e:
            attempts.append(str(e))
    raise ValueError(" | ".join(attempts))
   
def fetch_recent_messages(token, base_url, folder_id, top=50):
    folder_id_enc = urllib.parse.quote(folder_id, safe="")
    query = urllib.parse.urlencode({
        "$top": str(top),
        "$orderby": "receivedDateTime desc",
        "$select": "id,subject,receivedDateTime,hasAttachments",
    })
    return graph_get_all_pages(
        token,
        f"{base_url}/mailFolders/{folder_id_enc}/messages?{query}",
        max_pages=2,
    )


def download_attachment(token, base_url, message_id, tmp_dir, extension=".xls"):
    """
    Downloads the first attachment matching the given extension on the
    given message and returns the local file path (named after the
    real attachment filename).
    """
    msg_id_enc = urllib.parse.quote(message_id, safe="")
    attachments = graph_get(token, f"{base_url}/messages/{msg_id_enc}/attachments")
    for att in attachments.get("value", []):
        name = att.get("name", "")
        if name.lower().endswith(extension) and "contentBytes" in att:
            path = os.path.join(tmp_dir, name)
            with open(path, "wb") as f:
                f.write(base64.b64decode(att["contentBytes"]))
            return path
    return None


def update_after_care(state, results, latest_date):
    """
    Merges the day's Job and Job WIP evidence into state["afterCare"].

    Deliberately conservative about what it claims:

    * A cycle starts only where the AFTER-CARE tag AND a finalised
      invoice AND a real finished date are all present (QJT-001 7A,
      clause 34). Tagged-but-open jobs are reported as awaiting Day 0,
      never as active cycles.
    * The register is appended to and never truncated, because the daily
      Job Report only contains that day's jobs.
    * The tagged-job feed being connected does NOT mean outcome capture
      is connected. They are tracked as two separate statuses so the
      Bridge cannot imply it knows a call was made when nothing records
      that.
    * Where a location's reports are missing from this close, that
      location's awaiting-Day-0 figure is left as it was rather than
      being silently reported as zero.
    """
    ac = state.get("afterCare")
    if not ac:
        raise ValueError("state has no afterCare block to update")

    register = ac.get("register") or []
    awaiting = list(ac.get("awaitingDayZero") or [])
    rework_open = []
    added_total, superseded_total = [], []
    locations_read = []
    salesperson_populated = 0
    after_care_open_by_location = {}

    for location in ("bunbury", "busselton"):
        job = results.get((location, "Job"))
        wip = results.get((location, "Job Wip"))
        if job is None and wip is None:
            continue
        locations_read.append(location)

        if job is not None:
            register, added, superseded = aftercare.merge_register(
                register, job["afterCareFinalised"], location, latest_date
            )
            added_total.extend(added)
            superseded_total.extend(superseded)

        if wip is not None:
            for record in wip["afterCareOpen"]:
                record = dict(record, location=location)
                after_care_open_by_location.setdefault(location, []).append(record)
            rework_open.extend(dict(r, location=location) for r in wip["reworkOpen"])
            salesperson_populated += wip["salespersonPopulated"]

    # Rebuild awaiting-Day-0 only for the locations actually read this
    # close, so a missing Busselton report cannot erase Busselton's
    # pending enrolments.
    if after_care_open_by_location or locations_read:
        kept = [
            a for a in awaiting
            if a.get("location") not in after_care_open_by_location
            and a.get("location") not in locations_read
        ]
        refreshed = []
        for location in locations_read:
            refreshed.extend(after_care_open_by_location.get(location, []))
        awaiting = kept + refreshed

    position = aftercare.compute_position(
        register, awaiting, rework_open, latest_date, ac.get("outcomes") or {}
    )

    # The journey itself is published from the engine rather than kept as
    # a separate hand-maintained copy in the state file, so the table the
    # Bridge shows and the dates it calculates can never drift apart.
    ac["milestones"] = [
        {
            "code": m["code"], "label": m["label"], "tier": m["tier"],
            "type": m["type"], "owner": m["owner"], "rule": m["rule"],
            "dueRule": m["dueRule"], "conditional": m["conditional"],
        }
        for m in aftercare.MILESTONES
    ]

    ac["register"] = register
    ac["awaitingDayZero"] = awaiting
    ac["reworkOpen"] = rework_open
    ac["operatingPosition"] = {
        "activeCycles": position["activeCycles"],
        "dueToday": position["dueToday"],
        "completedToday": position["completedToday"],
        "overdue": position["overdue"],
        "unresolvedConcerns": position["unresolvedConcerns"],
        "awaitingDayZero": position["awaitingDayZero"],
        "recurringQualitySignals": None,
        "ownerAttentionRequired": bool(
            position["unresolvedConcerns"] > 0 or position["overdue"] >= 3
        ),
        "ownerAttentionReason": (
            f"{position['unresolvedConcerns']} unresolved After-Care concern(s) open on an enrolled vehicle."
            if position["unresolvedConcerns"] > 0
            else f"{position['overdue']} After-Care obligations are past their due date with no recorded outcome."
            if position["overdue"] >= 3
            else "No After-Care matter requires Owner attention; obligations sit with the accountable consultants."
        ),
    }
    ac["dueTodayList"] = position["dueTodayList"]
    ac["overdueList"] = position["overdueList"]
    ac["upcomingList"] = position["upcomingList"]
    ac["suppressedList"] = position["suppressedList"]
    ac["concernList"] = position["concernList"]

    ac["integration"]["status"] = "connected_tested" if locations_read else "connected_degraded"
    ac["integration"]["statusLabel"] = (
        "MechanicDesk AFTER-CARE tag and job finalisation connected"
        if locations_read else "After-Care reports missing from this close"
    )
    ac["integration"]["lastSuccessfulIngestionAt"] = (
        datetime.datetime.utcnow().isoformat() + "Z" if locations_read
        else ac["integration"].get("lastSuccessfulIngestionAt")
    )
    ac["integration"]["locationsRead"] = locations_read
    ac["integration"]["holidayTableCoverageTo"] = aftercare.HOLIDAY_TABLE_COVERAGE_TO

    # Computed exceptions are rebuilt each run; declared ones (governance
    # matters recorded in the state file by hand) are preserved.
    declared = [e for e in (ac.get("exceptions") or []) if e.get("origin") == "declared"]
    computed = []

    # Keyed off the programme's own jobs, not a global count. Saleperson
    # is populated on a minority of the wider job book (17 of Bunbury's
    # 98 at the 17 Sep 2026 close), so a "zero across both locations"
    # test would never fire while every After-Care obligation was still
    # being assigned by proxy.
    proxied = [c for c in register if not c.get("supersededBy") and c.get("ownerBasis") != "salesperson"]
    proxied += [a for a in awaiting if a.get("ownerBasis") != "salesperson"]
    if proxied:
        total = len(register) + len(awaiting)
        computed.append({
            "id": "owner-proxy", "origin": "computed", "severity": "warning",
            "owner": "DN02 Sales",
            "title": "Build Consultant is assigned by proxy",
            "detail": f"{len(proxied)} of {total} jobs in or awaiting the programme have no Saleperson recorded "
                      f"in MechanicDesk, so their After-Care obligations are assigned from the booking's "
                      f"Created By instead. The 72-hour call may land with someone who never met the customer. "
                      f"Across the wider open job book, Saleperson is filled on {salesperson_populated} rows.",
        })
    ac["exceptions"] = declared + computed

    print(
        f"After-Care: {position['activeCycles']} active cycle(s), "
        f"{position['dueToday']} due today, {position['overdue']} overdue, "
        f"{position['unresolvedConcerns']} unresolved concern(s), "
        f"{position['awaitingDayZero']} awaiting Day 0 "
        f"(+{len(added_total)} enrolled, {len(superseded_total)} superseded this run)"
    )


def main():
    tenant_id = get_env("GRAPH_TENANT_ID")
    client_id = get_env("GRAPH_CLIENT_ID")
    client_secret = get_env("GRAPH_CLIENT_SECRET")
    mailbox = get_env("MAILBOX_ADDRESS")

    print(f"Authenticating for mailbox: {mailbox}")
    token = get_access_token(tenant_id, client_id, client_secret)
    base_url = f"https://graph.microsoft.com/v1.0/users/{urllib.parse.quote(mailbox)}"

    folder_id = fetch_child_folder_id(token, base_url, "Mechanic Desk Reports")
    messages = fetch_recent_messages(token, base_url, folder_id, top=50)
    print(f"Fetched {len(messages)} recent messages from Mechanic Desk Reports.")

    # Match each message against the expected subject pattern, keep only
    # Income/Productivity/Efficiency, and only the most recent Perth date
    # that has at least one match (the latest accountable close).
    matched = []
    for m in messages:
        match = SUBJECT_PATTERN.match(m["subject"].strip())
        if not match:
            continue
        location_label, raw_report_type = match.groups()
        report_type = normalise_report_type(raw_report_type)
        if report_type is None or location_label not in LOCATION_KEY:
            continue
        matched.append({
            "id": m["id"],
            "receivedDateTime": m["receivedDateTime"],
            "perthDate": perth_date(m["receivedDateTime"]),
            "location": LOCATION_KEY[location_label],
            "reportType": report_type,
            "hasAttachments": m["hasAttachments"],
        })

    if not matched:
        print("ERROR: no matching Income/Productivity/Efficiency report emails found.", file=sys.stderr)
        sys.exit(1)

    latest_date = max(m["perthDate"] for m in matched)
    todays = [m for m in matched if m["perthDate"] == latest_date]
    print(f"Latest accountable date detected: {latest_date} ({len(todays)} matching report emails)")

    with open(STATE_PATH) as f:
        state = json.load(f)

    # --- Podium Daily Digest: now filed into its own "Podium" subfolder
    # under Inbox (moved there by an Outlook rule), same reliable
    # pattern as Mechanic Desk Reports -- no more scanning-window
    # guesswork against a busy Inbox. ---
    podium_folder_id = fetch_child_folder_id(token, base_url, "Podium")
    podium_folder_id_enc = urllib.parse.quote(podium_folder_id, safe="")
    podium_query = urllib.parse.urlencode({
        "$top": "10",
        "$orderby": "receivedDateTime desc",
        "$select": "id,subject,receivedDateTime,from",
    })
    podium_messages = graph_get(
        token, f"{base_url}/mailFolders/{podium_folder_id_enc}/messages?{podium_query}"
    ).get("value", [])
    if podium_messages:
        podium_msg = podium_messages[0]
        podium_msg_id_enc = urllib.parse.quote(podium_msg["id"], safe="")
        full_msg = graph_get(
            token,
            f"{base_url}/messages/{podium_msg_id_enc}?$select=subject,receivedDateTime,body",
        )
        try:
            podium_result = parse_podium_digest(full_msg["body"]["content"], full_msg["subject"])
            state["podium"]["inbox"] = podium_result["inbox"]
            state["podium"]["group"] = podium_result["group"]
            state["podium"]["locations"] = podium_result["locations"]
            state["podium"]["reportingDate"] = podium_result["reportingDate"]
            state["sources"]["podiumDailyDigest"]["status"] = "connected_tested"
            state["sources"]["podiumDailyDigest"]["receivedAt"] = full_msg["receivedDateTime"]
            state["sources"]["podiumDailyDigest"]["reportingDate"] = podium_result["reportingDate"]
            state["sources"]["podiumDailyDigest"]["provenance"] = f"{full_msg['subject']} received in Outlook"
            print(f"Parsed Podium Daily Digest: reportingDate={podium_result['reportingDate']}, "
                  f"newLeads={podium_result['inbox']['newLeads']}")
        except Exception as e:
            print(f"WARNING: failed to parse Podium Daily Digest: {e}", file=sys.stderr)
    else:
        print("WARNING: no Podium Daily Digest email found in Inbox.")

    # --- Xero financial data (DN03 Cash Flow & Budget), via direct Xero
    # Custom Connection API access -- bypasses dataSights, which has no
    # scriptable/unattended access path. Best-effort: if Xero credentials
    # are absent or the API call fails, the rest of the state file still
    # updates normally. ---
    xero_client_id = os.environ.get("XERO_CLIENT_ID")
    xero_client_secret = os.environ.get("XERO_CLIENT_SECRET")
    if xero_client_id and xero_client_secret:
        try:
            xero_token = xero_get_access_token(xero_client_id, xero_client_secret)
            xero_tenant_id = xero_get_tenant_id(xero_token)
            as_of_date = latest_date  # same accountable date as the MechanicDesk close

            bank_balance = get_bank_account_balance(
                xero_get, xero_token, xero_tenant_id, as_of_date, BANK_ACCOUNT_OF_INTEREST
            )
            net_payables, overdue_payables = get_payables_summary(
                xero_get, xero_token, xero_tenant_id, as_of_date
            )
           

            month_start = datetime.date.fromisoformat(as_of_date).replace(day=1).isoformat()
            pnl = get_profit_and_loss_summary(xero_get, xero_token, xero_tenant_id, month_start, as_of_date)

            state["financialControl"]["monthToDateRevenue"] = pnl["revenue"]
            state["financialControl"]["monthToDateGrossProfit"] = pnl["grossProfit"]
            state["financialControl"]["monthToDateGrossProfitMarginPercent"] = pnl["grossProfitMarginPercent"]
            state["financialControl"]["monthToDatePeriodStart"] = month_start
            state["financialControl"]["monthToDatePeriodEnd"] = as_of_date

            print(f"Parsed Xero P&L: revenue={pnl['revenue']}, grossProfit={pnl['grossProfit']}, "
                  f"gpMarginPercent={pnl['grossProfitMarginPercent']} ({month_start} to {as_of_date})")
            if bank_balance is not None:
                overdraft_drawn = round(-bank_balance, 2) if bank_balance < 0 else 0.0
                overdraft_limit = state["financialControl"]["overdraftLimit"]  # not derivable from Xero; keep existing
                state["financialControl"]["overdraftDrawn"] = overdraft_drawn
                state["financialControl"]["overdraftHeadroom"] = round(overdraft_limit - overdraft_drawn, 2)
            state["financialControl"]["netPayables"] = net_payables
            state["financialControl"]["overduePayables"] = overdue_payables
            state["financialControl"]["treasuryAsOfDate"] = as_of_date
            state["financialControl"]["payablesAsOfSyncUtc"] = datetime.datetime.utcnow().isoformat() + "Z"

            # Also refresh the freshness banner text shown on Bridge Home --
            # this was previously never updated by this script, so it kept
            # showing the original snapshot's sync time even after real
            # nightly Xero pulls started succeeding.
            now_utc = datetime.datetime.utcnow()
            now_awst = now_utc + datetime.timedelta(hours=8)
            display_freshness = now_awst.strftime("%-d %b %Y \u00b7 %-I:%M %p AWST")
            state["sources"]["xeroDataSights"]["displayFreshness"] = display_freshness
            state["sources"]["xeroDataSights"]["lastAccountTransactionsSyncUtc"] = now_utc.isoformat() + "Z"
            state["sources"]["xeroDataSights"]["syncStatus"] = "SUCCESS"

            print(f"Parsed Xero financial data: bank_balance={bank_balance}, "
                  f"netPayables={net_payables}, overduePayables={overdue_payables}")
        except Exception as e:
            print(f"WARNING: Xero financial ingestion failed: {e}", file=sys.stderr)
    else:
        print("WARNING: XERO_CLIENT_ID/XERO_CLIENT_SECRET not set, skipping financial ingestion.")

    # --- ANZ Worldline settlement report: filed into its own "Worldline"
    # Inbox subfolder (same pattern as Podium). PDF attachment, not .xls.
    # Only the top summary block is promoted (unambiguous); a location
    # mismatch between the addressee and the transaction-details heading
    # is surfaced as a data-quality flag rather than silently resolved. ---
    try:
        worldline_folder_id = fetch_child_folder_id(token, base_url, "ANZ Worldline")
        worldline_folder_id_enc = urllib.parse.quote(worldline_folder_id, safe="")
        worldline_query = urllib.parse.urlencode({
            "$top": "5",
            "$orderby": "receivedDateTime desc",
            "$select": "id,subject,receivedDateTime,hasAttachments",
        })
        worldline_messages = graph_get(
            token, f"{base_url}/mailFolders/{worldline_folder_id_enc}/messages?{worldline_query}"
        ).get("value", [])
        if worldline_messages:
            wl_msg = worldline_messages[0]
            with tempfile.TemporaryDirectory() as worldline_tmp_dir:
                pdf_path = download_attachment(token, base_url, wl_msg["id"], worldline_tmp_dir, extension=".pdf")
                if pdf_path:
                    worldline_result = parse_worldline_settlement(pdf_path)
                    state["sources"]["anzWorldline"]["status"] = "connected_tested"
                    state["sources"]["anzWorldline"]["receivedAt"] = wl_msg["receivedDateTime"]
                    state["sources"]["anzWorldline"]["settlementDate"] = worldline_result["settlementDate"]
                    state["sources"]["anzWorldline"]["netSettledAmount"] = worldline_result["netSettledAmount"]
                    state["sources"]["anzWorldline"]["totalValueOfTxns"] = worldline_result["totalValueOfTxns"]
                    state["sources"]["anzWorldline"]["txnFeesExclGst"] = worldline_result["txnFeesExclGst"]
                    state["sources"]["anzWorldline"]["gst"] = worldline_result["gst"]
                    if worldline_result["locationMismatch"]:
                        state["sources"]["anzWorldline"]["statusDetail"] = (
                            f"Settlement parsed successfully, but a data-quality issue was detected: "
                            f"{worldline_result['locationMismatch']}. Net settled amount is real; "
                            f"per-location attribution should not be assumed from this document."
                        )
                    else:
                        state["sources"]["anzWorldline"]["statusDetail"] = (
                            f"Settlement report parsed successfully for {worldline_result['settlementDate']}."
                        )
                    print(f"Parsed Worldline settlement: netSettledAmount={worldline_result['netSettledAmount']}, "
                          f"locationMismatch={worldline_result['locationMismatch']}")
                else:
                    print("WARNING: Worldline email found but no .pdf attachment could be downloaded.")
        else:
            print("WARNING: no Worldline settlement email found in the Worldline folder.")
    except Exception as e:
        print(f"WARNING: Worldline ingestion failed: {e}", file=sys.stderr)

    # --- Quote Reports: sent manually by sales staff (Luke for Bunbury,
    # Mitch for Busselton), not on MechanicDesk's own schedule -- filed
    # into a shared "Quote Reports" Inbox subfolder, identified by
    # sender rather than subject since the two staff use different
    # subject lines. Each report is a full running log; only the most
    # recent date present is promoted (handled inside the parser). ---
    # --- Quote Reports: sent manually by sales staff (Luke for Bunbury,
    # Mitch for Busselton), not on MechanicDesk's own schedule -- each
    # filed into their own named Inbox subfolder. Each report is a full
    # running log; only the most recent date present is promoted
    # (handled inside the parser). ---
    QUOTE_FOLDER_LOCATION = {
        "Luke Bleasdale": "bunbury",
        "Mitch Cooper": "busselton",
    }
    
    latest_by_location = {}
    with tempfile.TemporaryDirectory() as quote_tmp_dir:
        for folder_name, location in QUOTE_FOLDER_LOCATION.items():
            try:
                folder_id = fetch_child_folder_id_anywhere(token, base_url, folder_name, parents=(None, "Inbox"))
                folder_id_enc = urllib.parse.quote(folder_id, safe="")
                quote_query = urllib.parse.urlencode({
                    "$top": "5",
                    "$orderby": "receivedDateTime desc",
                    "$select": "id,subject,receivedDateTime,hasAttachments",
                })
                messages = graph_get(
                    token, f"{base_url}/mailFolders/{folder_id_enc}/messages?{quote_query}"
                ).get("value", [])
                if not messages:
                    print(f"WARNING: no Quote Report email found in the '{folder_name}' folder.")
                    continue

                m = messages[0]
                latest_by_location[location] = m
                if not m["hasAttachments"]:
                    print(f"WARNING: quote report email for {location} has no attachment, skipping.")
                    continue
                file_path = download_attachment(token, base_url, m["id"], quote_tmp_dir, extension=".xls")
                if not file_path:
                    print(f"WARNING: could not download .xls attachment for {location} quote report.")
                    continue
                quote_result = parse_quote_report(file_path)
                state["locations"][location]["quotes"] = {
                    "reportDate": quote_result["reportDate"],
                    "quoteCount": quote_result["quoteCount"],
                    "totalValue": quote_result["totalValue"],
                    "tagBreakdown": quote_result["tagBreakdown"],
                    "sourceFile": os.path.basename(file_path),
                    "receivedAt": m["receivedDateTime"],
                }
                print(f"Parsed {location} Quote Report: {quote_result}")
            except Exception as e:
                print(f"WARNING: Quote Report ingestion failed for '{folder_name}' ({location}): {e}", file=sys.stderr)

    with tempfile.TemporaryDirectory() as tmp_dir:
        results = {}  # (location, reportType) -> parsed dict
        result_files = {}  # (location, reportType) -> filename, so sourceFile is never guessed from list order
        source_files = {"bunbury": [], "busselton": []}
        latest_received = None

        for m in todays:
            if not m["hasAttachments"]:
                print(f"WARNING: {m['location']} {m['reportType']} report has no attachment, skipping.")
                continue
            file_path = download_attachment(token, base_url, m["id"], tmp_dir)
            if not file_path:
                print(f"WARNING: could not download .xls attachment for {m['location']} {m['reportType']}.")
                continue

            is_after_care = m["reportType"] in AFTER_CARE_PARSERS
            parser = AFTER_CARE_PARSERS[m["reportType"]] if is_after_care else REPORT_PARSERS[m["reportType"]]
            try:
                parsed = parser(file_path)
            except Exception as e:
                # An After-Care parse failure must not take the close down
                # with it -- the revenue and workshop evidence is
                # independent of it.
                level = "WARNING" if is_after_care else "ERROR"
                print(f"{level} parsing {m['location']} {m['reportType']}: {e}", file=sys.stderr)
                continue

            results[(m["location"], m["reportType"])] = parsed
            result_files[(m["location"], m["reportType"])] = os.path.basename(file_path)
            source_files[m["location"]].append(os.path.basename(file_path))
            if latest_received is None or m["receivedDateTime"] > latest_received:
                latest_received = m["receivedDateTime"]

            if m["reportType"] == "Job":
                print(f"Parsed {m['location']} Job: {len(parsed['jobs'])} jobs, "
                      f"{len(parsed['afterCareFinalised'])} AFTER-CARE finalised")
            elif m["reportType"] == "Job Wip":
                print(f"Parsed {m['location']} Job Wip: {parsed['openJobs']} open, "
                      f"{len(parsed['afterCareOpen'])} AFTER-CARE awaiting Day 0, "
                      f"{len(parsed['reworkOpen'])} rework open")
            else:
                print(f"Parsed {m['location']} {m['reportType']}: {parsed}")

        # Merge into state, per location
        for location in ("bunbury", "busselton"):
            income = results.get((location, "Income"))
            productivity = results.get((location, "Productivity"))
            efficiency = results.get((location, "Efficiency"))

            if income:
                state["locations"][location]["revenue"]["net"] = income["net"]
                state["locations"][location]["revenue"]["grossProfit"] = income["grossProfit"]
                state["locations"][location]["revenue"]["grossMarginPercent"] = income["grossMarginPercent"]
                income_file = result_files.get((location, "Income"))
                if income_file:
                    state["locations"][location]["revenue"]["sourceFile"] = income_file

            if productivity:
                state["locations"][location]["workshop"]["productivityPercent"] = productivity["productivityPercent"]
                state["locations"][location]["workshop"]["productivityCoverage"] = productivity["productivityCoverage"]

            if efficiency:
                state["locations"][location]["workshop"]["efficiencyPercent"] = efficiency["efficiencyPercent"]
                state["locations"][location]["workshop"]["efficiencyCoverage"] = efficiency["efficiencyCoverage"]

            if productivity or efficiency:
                state["locations"][location]["workshop"]["evidenceStatus"] = "current"
                state["locations"][location]["workshop"]["sourceFiles"] = source_files[location]

        # --- DN06 Customer After-Care (TWOS-AC-001 v1.1 / QJT-001 v1.1) ---
        # Day 0 comes from the Job Report (tag present AND invoice
        # finalised); enrolment-in-waiting and open reworks come from the
        # Job WIP book. The cycle register is APPENDED to, never rebuilt:
        # a cycle that started last month is not in today's export.
        try:
            update_after_care(state, results, latest_date)
        except Exception as e:
            print(f"WARNING: After-Care ingestion failed: {e}", file=sys.stderr)

        # Update top-level evidence metadata
        state["latestAccountableDate"] = latest_date
        state["generatedAt"] = datetime.datetime.utcnow().isoformat() + "Z"

        expected_count = len(LOCATION_KEY) * len(REPORT_PARSERS)
        actual_count = len([k for k in results if k[1] in REPORT_PARSERS])
        state["sources"]["mechanicDesk"]["status"] = "connected_tested" if actual_count == expected_count else "connected_degraded"
        state["sources"]["mechanicDesk"]["receivedAt"] = latest_received or state["sources"]["mechanicDesk"].get("receivedAt")
        state["sources"]["mechanicDesk"]["reportCount"] = actual_count
        state["sources"]["mechanicDesk"]["statusDetail"] = (
            f"Automated nightly ingestion: {actual_count}/{expected_count} Income/Productivity/Efficiency "
            f"reports parsed for {latest_date}."
        )
        state["sources"]["mechanicDesk"]["automationStatus"] = "scheduled_github_actions"

    with open(STATE_PATH, "w") as f:
        json.dump(state, f, indent=2)
        f.write("\n")

    print(f"\nUpdated {STATE_PATH} with {actual_count}/{expected_count} reports for {latest_date}.")
    if actual_count < expected_count:
        print("WARNING: not all expected reports were found/parsed -- state is only partially refreshed.")


if __name__ == "__main__":
    main()
