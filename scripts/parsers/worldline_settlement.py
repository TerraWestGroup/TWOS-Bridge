"""
Parses an ANZ Worldline settlement report PDF (the top summary block
only -- reliable and unambiguous) into figures for
twos-state.json's sources.anzWorldline section.

Deliberately does NOT attempt to attribute the settlement to a specific
location. The per-terminal "Transactions details" breakdown in these
reports has been observed to carry a Partner ID / location label that
does not match the addressee on the same document (e.g. a report
addressed to "BUNBURY 4X4" whose transaction breakdown is headed
"Partner ID: 77231 BUSSELTON 4X4"). Rather than silently picking one
location, this parser surfaces that mismatch as a data-quality flag
and only promotes the top summary block, which is unambiguous and
appears once per document.
"""

import re
import pdfplumber


def parse_worldline_settlement(pdf_path):
    with pdfplumber.open(pdf_path) as pdf:
        text = pdf.pages[0].extract_text()

    settlement_date = re.search(r"Settlement date:\s*(\d{2}/\d{2}/\d{4})", text)
    processed_account = re.search(r"Processed to account no\.:\s*([\d/]+)", text)
    net_settled = re.search(r"Processed to account no\.:\s*[\d/]+\s+AUD\s+([\d,]+\.\d{2})", text)
    total_value = re.search(r"Total Value of Txns\*\s+([\d,]+\.\d{2})", text)
    txn_fees = re.search(r"Txn Fees \(excl GST\)\*\s+(-?[\d,]+\.\d{2})", text)
    gst = re.search(r"GST\*\s+(-?[\d,]+\.\d{2})", text)

    # Addressee (top of document) vs. the Partner ID/location label on the
    # transaction-details breakdown -- these have been observed to disagree.
    addressee_match = re.search(r"Partner ID\s+(\d+)\s*\n(BUNBURY 4X4|BUSSELTON 4X4)", text)
    breakdown_match = re.search(r"Partner ID:\s*(\d+)\s+(BUNBURY 4X4|BUSSELTON 4X4)", text)

    required = {
        "Settlement date": settlement_date, "Processed to account no.": processed_account,
        "Net settled amount": net_settled, "Total Value of Txns": total_value,
        "Txn Fees": txn_fees, "GST": gst,
    }
    for label, m in required.items():
        if not m:
            raise ValueError(f"Could not find expected Worldline settlement field: {label}")

    def to_float(s):
        return float(s.replace(",", ""))

    location_mismatch = None
    if addressee_match and breakdown_match:
        addressee_partner_id, addressee_location = addressee_match.groups()
        breakdown_partner_id, breakdown_location = breakdown_match.groups()
        if addressee_partner_id != breakdown_partner_id or addressee_location != breakdown_location:
            location_mismatch = (
                f"Document addressed to Partner ID {addressee_partner_id} ({addressee_location}) "
                f"but transaction breakdown is headed Partner ID {breakdown_partner_id} ({breakdown_location})"
            )

    return {
        "settlementDate": settlement_date.group(1),
        "processedToAccount": processed_account.group(1),
        "netSettledAmount": to_float(net_settled.group(1)),
        "totalValueOfTxns": to_float(total_value.group(1)),
        "txnFeesExclGst": to_float(txn_fees.group(1)),
        "gst": to_float(gst.group(1)),
        "locationMismatch": location_mismatch,
    }


if __name__ == "__main__":
    import sys
    import json
    print(json.dumps(parse_worldline_settlement(sys.argv[1]), indent=2))
