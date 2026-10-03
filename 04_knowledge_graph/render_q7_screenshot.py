import csv
from PIL import Image, ImageDraw, ImageFont

CSV_PATH = "sparql_results/q7_patient_scenario.csv"
PNG_PATH = "sparql_results/q7_patient_scenario_screenshot.png"


def font(size, bold=False):
    try:
        return ImageFont.truetype("arialbd.ttf" if bold else "arial.ttf", size)
    except OSError:
        return ImageFont.load_default()


with open(CSV_PATH, encoding="utf-8") as fh:
    rows = list(csv.reader(fh))

title = "Q7 — Σενάριο ασθενή: Lyrica + INSPRA + Ibuprofen gel, ADR = dizziness (MedDRA 10013573)"
note = f"{len(rows) - 1} rows  |  κενά (—) = το φάρμακο δεν αναφέρει την ADR (OPTIONAL χωρίς match)"
ft, fh_, fb = font(20, True), font(16, True), font(16)

tmp = ImageDraw.Draw(Image.new("RGB", (10, 10)))
widths = [
    max(tmp.textlength(r[i] or "—", font=fh_ if j == 0 else fb) for j, r in enumerate(rows)) + 30
    for i in range(len(rows[0]))
]
row_h = 34
W = int(max(sum(widths) + 40, tmp.textlength(title, font=ft) + 40))
H = 70 + row_h * len(rows) + 40

img = Image.new("RGB", (W, H), "white")
d = ImageDraw.Draw(img)
d.text((20, 20), title, font=ft, fill="black")

y = 60
for j, r in enumerate(rows):
    bg = "#2f5597" if j == 0 else ("#f2f2f2" if j % 2 else "white")
    d.rectangle([20, y, 20 + sum(widths), y + row_h], fill=bg, outline="#bfbfbf")
    x = 20
    for i, cell in enumerate(r):
        color = "white" if j == 0 else ("black" if cell else "#999999")
        d.text((x + 10, y + 8), cell or "—", font=fh_ if j == 0 else fb, fill=color)
        x += widths[i]
    y += row_h

d.text((20, y + 10), note, font=font(14), fill="#555555")
img.save(PNG_PATH)
print("Saved", PNG_PATH)
