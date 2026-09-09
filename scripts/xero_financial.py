"""
Pulls the DN03 financial-control figures from Xero directly (via the
Custom Connection), for merging into twos-state.json's financialControl
section.

Two data sources:
1. Balance Sheet report, pinned to an explicit date (Xero defaults to
   the current fiscal period-end -- e.g. a forward month-end -- if no
   date is given, which would silently drift the reported figures away
   from "today"). Used to find the ANZ Cheque Account balance, which
   carries the overdraft facility.
2. Accounts Payable invoices (Type==ACCPAY, Status==AUTHORISED), summed
   for net payables and, split by due date, for overdue payables.

Deliberately NOT touched here (left as whatever is already in
twos-state.json, since they aren't reliably derivable from this data):
- overdraftLimit (a facility limit set by the bank, not visible in Xero)
- caplinkDue / caplinkDueDate (Caplink's own receivable schedule)
"""

import re
import datetime

BANK_ACCOUNT_OF_INTEREST = "ANZ Cheque Account (3693)"


def _extract_epoch_ms(xero_date_str):
    """Xero JSON dates look like '/Date(1735689600000+0000)/'."""
    match = re.search(r"/Date\((-?\d+)", xero_date_str)
    if not match:
        return None
    return int(match.group(1))


def _to_date(xero_date_str):
    epoch_ms = _extract_epoch_ms(xero_date_str)
    if epoch_ms is None:
        return None
    return datetime.datetime.utcfromtimestamp(epoch_ms / 1000).date()


def get_bank_account_balance(xero_get_fn, token, tenant_id, as_of_date, account_name):
    """
    as_of_date: 'YYYY-MM-DD' string. Returns the float balance for the
    named bank account as of that date, or None if not found.
    """
    report = xero_get_fn(
        token, tenant_id, "/api.xro/2.0/Reports/BalanceSheet",
        params={"date": as_of_date},
    )
    rows = report["Reports"][0]["Rows"]

    def walk(rows):
        for row in rows:
            if row.get("RowType") == "Section":
                yield from walk(row.get("Rows", []))
            elif row.get("RowType") == "Row":
                cells = row.get("Cells", [])
                if cells and cells[0].get("Value") == account_name:
                    yield cells

    for cells in walk(rows):
        if len(cells) >= 2:
            try:
                return float(cells[1]["Value"])
            except (ValueError, KeyError):
                return None
    return None


def get_payables_summary(xero_get_fn, token, tenant_id, as_of_date_str):
    """
    Sums AmountDue across all AUTHORISED ACCPAY invoices, split into
    net (all) and overdue (DueDate before as_of_date). Paginates
    through all pages Xero returns (100 invoices/page).
    """
    as_of = datetime.date.fromisoformat(as_of_date_str)
    net_payables = 0.0
    overdue_payables = 0.0
    page = 1

    while True:
        result = xero_get_fn(
            token, tenant_id, "/api.xro/2.0/Invoices",
            params={"where": 'Type=="ACCPAY"&&Status=="AUTHORISED"', "page": str(page)},
        )
        invoices = result.get("Invoices", [])
        if not invoices:
            break

        for inv in invoices:
            amount_due = float(inv.get("AmountDue", 0) or 0)
            net_payables += amount_due
            due_date = _to_date(inv.get("DueDate", "")) if inv.get("DueDate") else None
            if due_date and due_date < as_of:
                overdue_payables += amount_due

        if len(invoices) < 100:
            break
        page += 1

    return round(net_payables, 2), round(overdue_payables, 2)
