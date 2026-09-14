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
   
def get_profit_and_loss_summary(xero_get_fn, token, tenant_id, from_date, to_date):
    """
    Pulls Total Trading Income (revenue), Gross Profit and Gross Profit
    Margin (%) from Xero's own Reports/ProfitAndLoss for the given date
    range -- the whole company, no tracking-category breakdown.

    Deliberately does NOT pass trackingCategoryID/trackingOptionID: Xero's
    Reports API returns one row per tracking-dimension value when asked
    for a tracking breakdown, and summing across dimensions double- (or
    N-times-) counts revenue if more than one tracking category is
    configured on the org -- this entity has two ("Installer" and
    "Location"), which is exactly what produced the doubled dataSights
    figures on 2026-09-13/14. Requesting the plain company-wide report
    avoids that: there is exactly one "Total Trading Income" row here.

    from_date / to_date: 'YYYY-MM-DD' strings, inclusive. For "month to
    date" pass the 1st of the month as from_date and the latest
    accountable date as to_date.

    Returns a dict: {revenue, grossProfit, grossProfitMarginPercent}.
    Any figure not found in the report comes back as None rather than
    raising -- a missing row is data-quality information for the caller
    to record in twos-state.json, not a hard failure that should kill
    the rest of the nightly run.
    """
    report = xero_get_fn(
        token, tenant_id, "/api.xro/2.0/Reports/ProfitAndLoss",
        params={"fromDate": from_date, "toDate": to_date},
    )
    rows = report["Reports"][0]["Rows"]

    def walk(rows):
        for row in rows:
            if row.get("RowType") == "Section":
                yield from walk(row.get("Rows", []))
            elif row.get("RowType") in ("Row", "SummaryRow"):
                cells = row.get("Cells", [])
                if cells:
                    yield cells

    # Xero's default P&L labels the revenue total differently depending
    # on the org's chart-of-accounts setup. This entity's own native P&L
    # export (2026-09-14) uses "Total Trading Income"; other orgs use
    # "Total Income" or "Total Revenue". Checked in order, first match wins.
    REVENUE_LABELS = ("Total Trading Income", "Total Income", "Total Revenue")
    GP_LABELS = ("Gross Profit",)
    GP_PERCENT_LABELS = ("Gross Profit Margin (%)", "Gross Profit Margin %")

    def find(labels):
        for cells in walk(rows):
            label = cells[0].get("Value", "")
            if label in labels and len(cells) >= 2:
                try:
                    return float(cells[1]["Value"])
                except (ValueError, KeyError, TypeError):
                    return None
        return None

    revenue = find(REVENUE_LABELS)
    gross_profit = find(GP_LABELS)
    gp_margin = find(GP_PERCENT_LABELS)

    if gp_margin is None and revenue and gross_profit is not None and revenue != 0:
        gp_margin = round(gross_profit / revenue * 100, 2)

    return {
        "revenue": revenue,
        "grossProfit": gross_profit,
        "grossProfitMarginPercent": gp_margin,
    }
