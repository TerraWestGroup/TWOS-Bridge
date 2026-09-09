"""
Parses a MechanicDesk daily Efficiency Report (.xls) into the workshop
efficiency fields used by twos-state.json.

Real column layout (confirmed against actual exported reports):
Employee | Total Hours | Charged Amount | Effective Hours | Percentage

Like the Productivity Report, there is no totals row -- each row is one
employee's day. TWOS aggregates to a single workshop-level efficiency
percentage as: sum(Effective Hours) / sum(Total Hours) * 100
(hours-weighted, consistent with how the Productivity Report is
aggregated, so the two figures are comparable to each other).
"""

import xlrd


def parse_efficiency_report(file_path):
    """
    Returns {"efficiencyPercent": float, "efficiencyCoverage": "Name, Name"}
    """
    wb = xlrd.open_workbook(file_path)
    sheet = wb.sheet_by_index(0)

    header = [sheet.cell_value(0, c) for c in range(sheet.ncols)]
    col = {name: idx for idx, name in enumerate(header)}

    required = ["Employee", "Total Hours", "Effective Hours"]
    for r in required:
        if r not in col:
            raise ValueError(f"Expected column '{r}' not found in {file_path}. Found columns: {header}")

    employees = []
    total_hours_sum = 0.0
    effective_hours_sum = 0.0

    for r in range(1, sheet.nrows):
        name = sheet.cell_value(r, col["Employee"])
        if name == "":
            continue
        total_hours = float(sheet.cell_value(r, col["Total Hours"]) or 0)
        effective_hours = float(sheet.cell_value(r, col["Effective Hours"]) or 0)
        employees.append(name)
        total_hours_sum += total_hours
        effective_hours_sum += effective_hours

    if total_hours_sum == 0:
        raise ValueError(f"No recorded total hours found in {file_path}; cannot compute efficiency.")

    efficiency_percent = (effective_hours_sum / total_hours_sum) * 100

    return {
        "efficiencyPercent": round(efficiency_percent, 2),
        "efficiencyCoverage": ", ".join(employees),
    }


if __name__ == "__main__":
    import sys
    import json
    result = parse_efficiency_report(sys.argv[1])
    print(json.dumps(result, indent=2))
