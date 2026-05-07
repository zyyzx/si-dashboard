#!/usr/bin/env python3
"""Create CapIQ historical float template with CIQ formulas."""

import json
import re
import datetime
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter

TRACKER_DIR = r"C:\Users\VamseeRavella\SI Tracker"

print("Loading tickers from dashboard...")
with open(f"{TRACKER_DIR}\\si_dashboard.html", "r", encoding="utf-8") as f:
    text = f.read()
m = re.search(r"const RAW=(\{.*?\});", text)
raw = json.loads(m.group(1))
tickers = sorted(raw["tickers"].keys())
all_dates = raw["dates"]

with open(f"{TRACKER_DIR}\\quarterly_dates.json") as f:
    selected_dates = json.load(f)

print(f"Tickers: {len(tickers)}, Dates: {len(selected_dates)}")

wb = openpyxl.Workbook()

# Instructions sheet
ws_inst = wb.active
ws_inst.title = "Instructions"
lines = [
    "CapIQ Historical Float Data Template",
    "",
    "Formulas: =@CIQ($A{row}, \"IQ_FLOAT\", {col}$1)",
    "Each cell pulls the public float for a ticker as of the settlement date in row 1.",
    "",
    "Steps:",
    "1. Open this file in Excel with the Capital IQ Pro Plug-in",
    "2. Go to the Float sheet - formulas are pre-filled",
    "3. Let CIQ calculate (~360K formulas, may take 30-60 min)",
    "4. Save As a new copy (values only recommended)",
    "5. Run: python integrate_float_data.py --capiq capiq_float_historical.xlsx",
    "",
    f"Layout: {len(tickers):,} tickers x {len(selected_dates)} quarterly dates",
    f"Dates: {selected_dates[0]} through {selected_dates[-1]}",
    f"Total formulas: {len(tickers) * len(selected_dates):,}",
]
for i, line in enumerate(lines, 1):
    ws_inst.cell(row=i, column=1, value=line)
ws_inst["A1"].font = Font(bold=True, size=14, color="0000FF")
ws_inst.column_dimensions["A"].width = 80

# Float sheet
print("Building Float sheet with CIQ formulas...")
ws = wb.create_sheet("Float")

header_fill = PatternFill("solid", fgColor="16213E")
header_font = Font(bold=True, color="FFFFFF", size=9)

ws.cell(row=1, column=1, value="Ticker").font = header_font
ws["A1"].fill = header_fill
ws.column_dimensions["A"].width = 10

for j, dstr in enumerate(selected_dates):
    col = j + 2
    dt = datetime.datetime.strptime(dstr, "%Y%m%d")
    cell = ws.cell(row=1, column=col, value=dt)
    cell.number_format = "MM/DD/YYYY"
    cell.font = header_font
    cell.fill = header_fill
    cell.alignment = Alignment(horizontal="center")
    ws.column_dimensions[get_column_letter(col)].width = 14

for i, ticker in enumerate(tickers):
    row = i + 2
    ws.cell(row=row, column=1, value=ticker)
    for j in range(len(selected_dates)):
        col = j + 2
        col_letter = get_column_letter(col)
        formula = f'=@CIQ($A{row},"IQ_FLOAT",{col_letter}$1)'
        ws.cell(row=row, column=col, value=formula)
    if (i + 1) % 2000 == 0:
        print(f"  {i+1:,}/{len(tickers):,} tickers...")

ws.freeze_panes = "B2"

# Reference sheet with all 151 settlement dates
ws_ref = wb.create_sheet("All Settlement Dates")
ws_ref.cell(row=1, column=1, value="Date Index").font = Font(bold=True)
ws_ref.cell(row=1, column=2, value="Settlement Date").font = Font(bold=True)
ws_ref.cell(row=1, column=3, value="In Template").font = Font(bold=True)
for i, d in enumerate(all_dates):
    dt = datetime.datetime.strptime(d, "%Y%m%d")
    ws_ref.cell(row=i + 2, column=1, value=i)
    ws_ref.cell(row=i + 2, column=2, value=dt).number_format = "MM/DD/YYYY"
    ws_ref.cell(row=i + 2, column=3, value="Yes" if d in selected_dates else "")
ws_ref.column_dimensions["A"].width = 12
ws_ref.column_dimensions["B"].width = 16
ws_ref.column_dimensions["C"].width = 14

out_path = f"{TRACKER_DIR}\\capiq_float_historical.xlsx"
print(f"Saving to {out_path}...")
wb.save(out_path)
print(f"Done! {len(tickers):,} tickers x {len(selected_dates)} dates = {len(tickers)*len(selected_dates):,} formulas")
