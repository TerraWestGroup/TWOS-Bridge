"""
Parses a MechanicDesk daily Income Report (.xls) into the revenue fields
used by twos-state.json: net, grossProfit, grossMarginPercent, sourceFile.

Real column layout (confirmed against an actual exported report,
sheet name 'Invoices'):

Date | No.# | Description | Customer | Registration Number | Make | Model |
Year | VIN | Net Amount | GST | Total | COGS | Profit Margin |
Profit Margin Percentage | Paid | Remain | Salesperson | Tags

The final row is a totals row: every column except the label columns is
blank except a 'Total' marker in the Year/label area, with the numeric
totals in Net Amount / GST / Total / COGS / Profit Margin /
Profit Margin Percentage / Paid / Remain.

This module only parses; it does not fetch or write anything itself.
"""

import xlrd


def parse_income_report(file_path):
    """
    Returns a dict: {"net": float, "grossProfit": float, "grossMarginPercent": float}
    extracted from the totals row of the Income Report.
    """
    wb = xlrd.open_workbook(file_path)
    sheet = wb.sheet_by_index(0)

    header = [sheet.cell_value(0, c) for c in range(sheet.ncols)]
    col = {name: idx for idx, name in enumerate(header)}

    required = ["Net Amount", "Profit Margin", "Profit Margin Percentage"]
    for r in required:
        if r not in col:
            raise ValueError(f"Expected column '{r}' not found in {file_path}. Found columns: {header}")

    # The totals row is the last row where the 'Net Amount' cell is non-blank
    # and a 'Total' label appears somewhere in that row (MechanicDesk convention).
    totals_row = None
    for r in range(sheet.nrows - 1, 0, -1):
        row_values = [sheet.cell_value(r, c) for c in range(sheet.ncols)]
        if "Total" in row_values and row_values[col["Net Amount"]] != "":
            totals_row = r
            break

    if totals_row is None:
        raise ValueError(f"Could not locate a totals row in {file_path}")

    net = float(sheet.cell_value(totals_row, col["Net Amount"]))
    gross_profit = float(sheet.cell_value(totals_row, col["Profit Margin"]))
    gross_margin_percent = float(sheet.cell_value(totals_row, col["Profit Margin Percentage"]))

    return {
        "net": round(net, 2),
        "grossProfit": round(gross_profit, 2),
        "grossMarginPercent": round(gross_margin_percent, 2),
    }


if __name__ == "__main__":
    import sys
    import json
    result = parse_income_report(sys.argv[1])
    print(json.dumps(result, indent=2))
