"""
Parses a MechanicDesk Quote Report (.xls) -- sent manually by sales
staff (Luke for Bunbury, Mitch for Busselton) rather than on
MechanicDesk's own automated schedule -- into a single day's quote
activity: count, total value, and a breakdown by whatever outcome tag
each quote actually carries (no invented Won/Lost/Pending categories;
the raw tag text is used as-is, since rows can carry more than one tag).

Real column layout (confirmed against actual exported reports, both
Bunbury and Busselton use the same schema):

Date | No.# | Description | Customer | Registration Number | Make |
Model | Year | VIN | Net Amount | GST | Total | COGS | Profit Margin |
Profit Margin Percentage | Invoice Amount | Salesperson | Tags

This is a full running log (weeks of history in every export), not a
single day's snapshot -- so this parser filters to whichever date is
the MOST RECENT one actually present in the file, treating that as
"today's" quote activity. The last row is a totals row (across the
entire history), which is not used here since the whole point is a
single-day slice.
"""

import datetime
import xlrd


def parse_quote_report(file_path):
    """
    Returns {"reportDate": "YYYY-MM-DD", "quoteCount": int,
             "totalValue": float, "tagBreakdown": {tag: count}}
    for the most recent date present in the file.
    """
    wb = xlrd.open_workbook(file_path)
    sheet = wb.sheet_by_index(0)

    header = [sheet.cell_value(0, c) for c in range(sheet.ncols)]
    col = {name: idx for idx, name in enumerate(header)}

    required = ["Date", "No.#", "Total", "Tags"]
    for r in required:
        if r not in col:
            raise ValueError(f"Expected column '{r}' not found in {file_path}. Found columns: {header}")

    rows = []
    for r in range(1, sheet.nrows):
        date_str = sheet.cell_value(r, col["Date"])
        quote_no = sheet.cell_value(r, col["No.#"])
        if not date_str or not quote_no:
            continue  # skip the totals row (blank Date/No.#) and any blank rows
        day, month, year = date_str.split("/")
        parsed_date = datetime.date(int(year), int(month), int(day))
        total = float(sheet.cell_value(r, col["Total"]) or 0)
        tags_raw = sheet.cell_value(r, col["Tags"]) or ""
        tags = [t.strip() for t in tags_raw.split(",") if t.strip()]
        rows.append({"date": parsed_date, "total": total, "tags": tags})

    if not rows:
        raise ValueError(f"No quote rows found in {file_path}")

    target_date = max(r["date"] for r in rows)
    todays_rows = [r for r in rows if r["date"] == target_date]

    tag_breakdown = {}
    for r in todays_rows:
        for tag in r["tags"]:
            tag_breakdown[tag] = tag_breakdown.get(tag, 0) + 1

    return {
        "reportDate": target_date.isoformat(),
        "quoteCount": len(todays_rows),
        "totalValue": round(sum(r["total"] for r in todays_rows), 2),
        "tagBreakdown": tag_breakdown,
    }


if __name__ == "__main__":
    import sys
    import json
    print(json.dumps(parse_quote_report(sys.argv[1]), indent=2))
