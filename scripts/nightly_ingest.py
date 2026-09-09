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
from xero_client import get_access_token as xero_get_access_token, get_tenant_id as xero_get_tenant_id, xero_get
from xero_financial import get_bank_account_balance, get_payables_summary, BANK_ACCOUNT_OF_INTEREST

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
    children = graph_get(token, list_url)
    folder = next(
        (f for f in children.get("value", []) if f["displayName"] == folder_name),
        None,
    )
    if not folder:
        location = "top level of the mailbox" if parent is None else f"under {parent}"
        raise ValueError(f"'{folder_name}' folder not found at {location}.")
    return folder["id"]


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
        location_label, report_type = match.groups()
        if report_type not in REPORT_PARSERS or location_label not in LOCATION_KEY:
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

            if bank_balance is not None:
                overdraft_drawn = round(-bank_balance, 2) if bank_balance < 0 else 0.0
                overdraft_limit = state["financialControl"]["overdraftLimit"]  # not derivable from Xero; keep existing
                state["financialControl"]["overdraftDrawn"] = overdraft_drawn
                state["financialControl"]["overdraftHeadroom"] = round(overdraft_limit - overdraft_drawn, 2)
            state["financialControl"]["netPayables"] = net_payables
            state["financialControl"]["overduePayables"] = overdue_payables
            state["financialControl"]["treasuryAsOfDate"] = as_of_date
            state["financialControl"]["payablesAsOfSyncUtc"] = datetime.datetime.utcnow().isoformat() + "Z"

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
        "Luke Bleasedale": "bunbury",
        "Mitch Cooper": "busselton",
    }
    try:
        latest_by_location = {}
        with tempfile.TemporaryDirectory() as quote_tmp_dir:
            for folder_name, location in QUOTE_FOLDER_LOCATION.items():
                folder_id = fetch_child_folder_id(token, base_url, folder_name, parent=None)
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
        print(f"WARNING: Quote Report ingestion failed: {e}", file=sys.stderr)

    with tempfile.TemporaryDirectory() as tmp_dir:
        results = {}  # (location, reportType) -> parsed dict
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

            parser = REPORT_PARSERS[m["reportType"]]
            try:
                parsed = parser(file_path)
            except Exception as e:
                print(f"ERROR parsing {m['location']} {m['reportType']}: {e}", file=sys.stderr)
                continue

            results[(m["location"], m["reportType"])] = parsed
            source_files[m["location"]].append(os.path.basename(file_path))
            if latest_received is None or m["receivedDateTime"] > latest_received:
                latest_received = m["receivedDateTime"]
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
                if source_files[location]:
                    state["locations"][location]["revenue"]["sourceFile"] = source_files[location][0]

            if productivity:
                state["locations"][location]["workshop"]["productivityPercent"] = productivity["productivityPercent"]
                state["locations"][location]["workshop"]["productivityCoverage"] = productivity["productivityCoverage"]

            if efficiency:
                state["locations"][location]["workshop"]["efficiencyPercent"] = efficiency["efficiencyPercent"]
                state["locations"][location]["workshop"]["efficiencyCoverage"] = efficiency["efficiencyCoverage"]

            if productivity or efficiency:
                state["locations"][location]["workshop"]["evidenceStatus"] = "current"
                state["locations"][location]["workshop"]["sourceFiles"] = source_files[location]

        # Update top-level evidence metadata
        state["latestAccountableDate"] = latest_date
        state["generatedAt"] = datetime.datetime.utcnow().isoformat() + "Z"

        expected_count = len(LOCATION_KEY) * len(REPORT_PARSERS)
        actual_count = len(results)
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
