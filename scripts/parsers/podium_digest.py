"""
Parses a Podium Daily Digest email (HTML body, no attachment) into the
structure used by twos-state.json's "podium" section.

The email covers BOTH locations in one message, with:
- Group-level totals (Inbox: New Leads / Total active conversations /
  Open Messages; plus group Reviews / Feedback / Payments totals)
- Per-location breakdowns repeated across four sections (Response Time,
  Reviews, Feedback/CSAT, Payment Requests), each preceded by a heading
  "Bunbury 4x4 ... <street address>" or "Busselton 4x4 ... <street address>"

Strategy: strip HTML tags to get linear text, then:
  - group figures use label text that is textually distinct from the
    per-location labels (e.g. group "Review invites sent" vs
    per-location "Sent Invites"), so a single regex search each is
    enough and unambiguous.
  - per-location figures are extracted from the text block immediately
    following each location heading occurrence (up to the next heading),
    with the location identified from the heading match itself. This
    naturally handles the repeated headings (one occurrence per section)
    without needing to track which section is "currently" being parsed.
"""

import re
import html as html_module
from datetime import date

HEADING_PATTERN = re.compile(
    r"(Bunbury 4x4|Busselton 4x4)\s+\d+[^,]*Street,\s*(?:Bunbury|Busselton),\s*WA\s*\d{4},\s*Australia"
)

RESPONSE_TIME_PATTERN = re.compile(r"(?:(\d+)\s*hr\s*)?(\d+)\s*min\s*Response Time")
LOCATION_REVIEWS_PATTERN = re.compile(r"(\d+)\s*New Reviews\D*?(\d+)\s*Sent Invites")
LOCATION_CSAT_PATTERN = re.compile(r"(--|\d+(?:\.\d+)?)\s*CSAT")
LOCATION_PAYMENTS_PATTERN = re.compile(r"(\d+)\s*Unpaid\D*?(\d+)\s*Sent\b")

MONTHS = {
    "JANUARY": 1, "FEBRUARY": 2, "MARCH": 3, "APRIL": 4, "MAY": 5, "JUNE": 6,
    "JULY": 7, "AUGUST": 8, "SEPTEMBER": 9, "OCTOBER": 10, "NOVEMBER": 11, "DECEMBER": 12,
}


def _strip_html(raw_html):
    text = re.sub(r"<[^>]+>", " ", raw_html)
    text = html_module.unescape(text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _parse_reporting_date(subject):
    """Subject looks like 'Podium Daily Digest (SEPTEMBER 07, 2026)'."""
    match = re.search(r"\(([A-Z]+)\s+(\d{1,2}),\s*(\d{4})\)", subject)
    if not match:
        return None
    month_name, day, year = match.groups()
    month = MONTHS.get(month_name.upper())
    if not month:
        return None
    return date(int(year), month, int(day)).isoformat()


def _to_int_or_none(value):
    return None if value == "--" else int(value)


def _to_float_or_none(value):
    return None if value == "--" else float(value)


def parse_podium_digest(raw_html, subject):
    text = _strip_html(raw_html)

    # --- group-level figures (unique label text, single unambiguous match each) ---
    new_leads = re.search(r"(\d+)\s*New Leads", text)
    total_active = re.search(r"(\d+)\s*Total active conversations", text)
    open_messages = re.search(r"(\d+)\s*Open Messages", text)
    group_reviews = re.search(r"(\d+)\s*New Reviews.*?(\d+)\s*Review invites sent", text, re.DOTALL)
    group_csat = re.search(r"(--|\d+(?:\.\d+)?)\s*Customer Satisfaction", text)
    group_feedback_responses = re.search(r"Customer Satisfaction.*?(\d+)\s*Responses received", text, re.DOTALL)
    group_payments_received = re.search(r"\$?([\d,]+\.\d{2})\s*Received", text)
    group_payment_requests_sent = re.search(r"(\d+)\s*Sent requests", text)

    for label, m in [
        ("New Leads", new_leads), ("Total active conversations", total_active),
        ("Open Messages", open_messages), ("group Reviews", group_reviews),
        ("group CSAT", group_csat), ("group feedback responses", group_feedback_responses),
        ("group payments received", group_payments_received),
        ("group payment requests sent", group_payment_requests_sent),
    ]:
        if not m:
            raise ValueError(f"Could not find expected Podium digest field: {label}")

    result = {
        "reportingDate": _parse_reporting_date(subject),
        "inbox": {
            "newLeads": int(new_leads.group(1)),
            "totalActiveConversations": int(total_active.group(1)),
            "openMessages": int(open_messages.group(1)),
        },
        "group": {
            "newReviews": int(group_reviews.group(1)),
            "reviewInvitesSent": int(group_reviews.group(2)),
            "feedbackResponses": int(group_feedback_responses.group(1)),
            "customerSatisfaction": _to_float_or_none(group_csat.group(1)),
            "paymentsReceived": float(group_payments_received.group(1).replace(",", "")),
            "paymentRequestsSent": int(group_payment_requests_sent.group(1)),
        },
        "locations": {
            "bunbury": {
                "medianResponseMinutes": None, "newReviews": None, "reviewInvitesSent": None,
                "feedbackResponses": None, "customerSatisfaction": None,
                "paymentsReceived": None, "paymentRequestsSent": None, "unpaidPaymentRequests": None,
            },
            "busselton": {
                "medianResponseMinutes": None, "newReviews": None, "reviewInvitesSent": None,
                "feedbackResponses": None, "customerSatisfaction": None,
                "paymentsReceived": None, "paymentRequestsSent": None, "unpaidPaymentRequests": None,
            },
        },
    }

    headings = list(HEADING_PATTERN.finditer(text))
    for i, m in enumerate(headings):
        location = "bunbury" if m.group(1) == "Bunbury 4x4" else "busselton"
        block_start = m.end()
        block_end = headings[i + 1].start() if i + 1 < len(headings) else len(text)
        block = text[block_start:block_end]

        rt = RESPONSE_TIME_PATTERN.search(block)
        if rt:
            hours = int(rt.group(1)) if rt.group(1) else 0
            minutes = int(rt.group(2))
            result["locations"][location]["medianResponseMinutes"] = hours * 60 + minutes

        rv = LOCATION_REVIEWS_PATTERN.search(block)
        if rv:
            result["locations"][location]["newReviews"] = int(rv.group(1))
            result["locations"][location]["reviewInvitesSent"] = int(rv.group(2))

        csat = LOCATION_CSAT_PATTERN.search(block)
        if csat:
            result["locations"][location]["customerSatisfaction"] = _to_float_or_none(csat.group(1))

        pay = LOCATION_PAYMENTS_PATTERN.search(block)
        if pay:
            result["locations"][location]["unpaidPaymentRequests"] = int(pay.group(1))
            result["locations"][location]["paymentRequestsSent"] = int(pay.group(2))

    return result


if __name__ == "__main__":
    import sys
    import json
    with open(sys.argv[1], encoding="utf-8") as f:
        raw = f.read()
    subject = sys.argv[2] if len(sys.argv) > 2 else "Podium Daily Digest (SEPTEMBER 01, 2026)"
    print(json.dumps(parse_podium_digest(raw, subject), indent=2))
