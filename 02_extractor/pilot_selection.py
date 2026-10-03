"""
Βημα 1 για το πιλοτικο σετ.

Το script διαβαζει το extraction_4_8_log.csv.
Κραταει μονο οσα εχουν Success.
Μετα διαλεγει τυχαια 50 με σταθερο seed 42.

Το αποτελεσμα μπαινει στο pilot_selection.csv.
"""

from __future__ import annotations

import csv
import random
from pathlib import Path


SEED = 42
PILOT_SIZE = 50

LOG_FILE = Path("data/output/extraction_4_8_log.csv")
OUTPUT_FILE = Path("data/output/pilot_selection.csv")


def load_successful_ids(log_path: Path) -> list[str]:
    if not log_path.exists():
        raise FileNotFoundError(f"Δεν βρέθηκε: {log_path}")

    successful = []
    with log_path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("status", "").strip() == "Success":
                successful.append(row["product_id"].strip())
    return successful


def main() -> None:
    all_ids = load_successful_ids(LOG_FILE)
    print(f"Συνολικα επιτυχημενα IDs: {len(all_ids)}")

    rng = random.Random(SEED)
    selected = rng.sample(all_ids, min(PILOT_SIZE, len(all_ids)))
    selected_sorted = sorted(selected, key=lambda x: int(x))

    with OUTPUT_FILE.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["product_id"])
        for pid in selected_sorted:
            writer.writerow([pid])

    print(f"Επιλεχθηκαν {len(selected_sorted)} IDs με seed {SEED} -> {OUTPUT_FILE}")
    print("Τα IDs ειναι:", selected_sorted)


if __name__ == "__main__":
    main()
