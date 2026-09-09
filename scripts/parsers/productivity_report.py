"""
Parses a MechanicDesk daily Productivity Report (.xls) into the workshop
productivity fields used by twos-state.json.

Real column layout (confirmed against actual exported reports):
Employee | Total Hours | Charged Hours | Non-Charged Hours | Percentage

There is no totals row in this report type -- each row is one employee's
day. TWOS aggregates to a single workshop-level productivity percentage
as: sum(Charged Hours) / sum(Total Hours) * 100 (hours-weighted, not a
simple average of the per-employee percentages, so a short/part-day
employee doesn't skew the location figure).
"""

import xlrd


def parse_productivity_report(file_path):
    """
    Returns {"productivityPercent": float, "productivityCoverage": "Name, Name"}
    """
    wb = xlrd.open_workbook(file_path)
    sheet = wb.sheet_by_index(0)

    header = [sheet.cell_value(0, c) for c in range(sheet.ncols)]
    col = {name: idx for idx, name in enumerate(header)}

    required = ["Employee", "Total Hours", "Charged Hours"]
    for r in required:
        if r not in col:
            raise ValueError(f"Expected column '{r}' not found in {file_path}. Found columns: {header}")

    employees = []
    total_hours_sum = 0.0
    charged_hours_sum = 0.0

    for r in range(1, sheet.nrows):
        name = sheet.cell_value(r, col["Employee"])
        if name == "":
            continue
        total_hours = float(sheet.cell_value(r, col["Total Hours"]) or 0)
        charged_hours = float(sheet.cell_value(r, col["Charged Hours"]) or 0)
        employees.append(name)
        total_hours_sum += total_hours
        charged_hours_sum += charged_hours

    if total_hours_sum == 0:
        raise ValueError(f"No recorded total hours found in {file_path}; cannot compute productivity.")

    productivity_percent = (charged_hours_sum / total_hours_sum) * 100

    return {
        "productivityPercent": round(productivity_percent, 2),
        "productivityCoverage": ", ".join(employees),
    }


if __name__ == "__main__":
    import sys
    import json
    result = parse_productivity_report(sys.argv[1])
    print(json.dumps(result, indent=2))
