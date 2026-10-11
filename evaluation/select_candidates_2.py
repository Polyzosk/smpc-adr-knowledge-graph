"""
Second evaluation set (read-only): random selection of 5 pilot SmPCs.

- Exclude the 5 already annotated SmPCs.
- Shuffle the remaining 45 with random.Random(42).shuffle and take the first 5,
  skipping any SmPC with more than 100 ADR rows in adr_per_smpc.csv.
- Write evaluation/evaluation_candidates_2.csv and copy sources to evaluation/evaluation_sources_2/.

Run from the repository root:
    python evaluation/select_candidates_2.py
"""
from __future__ import annotations

import csv
import json
import random
import shutil
import sys
from collections import Counter
from pathlib import Path

from clean_text import clean_text_untruncated

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SEED = 42
SET_SIZE = 5
MAX_ADR = 100
CUT = 8000
ALREADY_ANNOTATED = {"8137", "6554", "1945", "101979", "10060"}

OUT = Path("evaluation")
SOURCES = OUT / "evaluation_sources_2"
BLOCKS = Path("data/output/extracted_4_8_blocks")
RAW = Path("data/raw")


def read_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def main() -> None:
    pilot = [r["product_id"] for r in read_csv(Path("data/output/pilot_selection.csv"))]
    remaining = [pid for pid in pilot if pid not in ALREADY_ANNOTATED]
    print(f"Pilot: {len(pilot)} | after excluding annotated: {len(remaining)}")

    random.Random(SEED).shuffle(remaining)
    adr_counts = Counter(r["product_id"] for r in read_csv(Path("data/output/adr_per_smpc.csv")))
    brands = {r["product_id"]: r["brand_name"].split("|")[0].strip() for r in read_csv(Path("data/output/drug_per_smpc.csv"))}

    chosen: list[str] = []
    for pid in remaining:
        if len(chosen) == SET_SIZE:
            break
        if adr_counts.get(pid, 0) > MAX_ADR:
            print(f"Skipped {pid}: {adr_counts[pid]} ADR rows (> {MAX_ADR})")
            continue
        chosen.append(pid)
    print(f"Shuffled order (first 10): {remaining[:10]}")

    SOURCES.mkdir(parents=True, exist_ok=True)
    rows = []
    for pid in chosen:
        block = json.loads((BLOCKS / f"{pid}_4_8_block.json").read_text(encoding="utf-8"))
        chars = len(clean_text_untruncated(block["content_html"]))
        rows.append({
            "product_id": pid,
            "brand_name": brands.get(pid, ""),
            "clean_chars": chars,
            "exceeds_8000": chars > CUT,
            "has_table": bool(block.get("has_table")),
            "original_adr_count": adr_counts.get(pid, 0),
        })
        shutil.copy2(RAW / f"smpc_{pid}.html", SOURCES / f"smpc_{pid}.html")
        shutil.copy2(BLOCKS / f"{pid}_4_8_block.json", SOURCES / f"{pid}_4_8_block.json")

    with (OUT / "evaluation_candidates_2.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    for r in rows:
        print(f"{r['product_id']} | {r['brand_name']} | chars={r['clean_chars']} | >8000={r['exceeds_8000']} | "
              f"table={r['has_table']} | original ADR={r['original_adr_count']}")
    print(f"Copied {2 * len(rows)} files to {SOURCES}")


if __name__ == "__main__":
    main()
