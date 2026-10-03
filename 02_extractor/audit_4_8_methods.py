"""
Read-only audit της εξαγωγής 4.8: ποια μέθοδος χρησιμοποιήθηκε ανά SmPC
(details/summary ή generic fallback) και αν βρέθηκε το τέλος (4.9).
Δεν γράφει blocks, μόνο audit_4_8_methods.csv.
"""
from __future__ import annotations

import csv
import sys
from collections import Counter
from pathlib import Path

from bs4 import BeautifulSoup

from extract_4_8_blocks import (
    HTML_DIR,
    MANIFEST_FILE,
    extract_between_summaries,
    extract_generic,
    find_markers,
    get_root_tag,
)

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

OUTPUT = Path("data/output/audit_4_8_methods.csv")


def audit(html_text: str) -> tuple[str, bool, int]:
    soup = BeautifulSoup(html_text, "html.parser")
    root = get_root_tag(soup)
    if not root:
        return "no_root", False, 0
    start_tag, end_tag = find_markers(root)
    if not start_tag:
        return "not_found", False, 0
    block = extract_between_summaries(start_tag, end_tag)
    method = "details_summary"
    if not block:
        block = extract_generic(root, start_tag, end_tag)
        method = "generic_fallback"
    return method, end_tag is not None, len(block)


def main() -> None:
    with MANIFEST_FILE.open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))

    results = []
    for idx, row in enumerate(rows, start=1):
        path = HTML_DIR / row["file_name"]
        method, end_found, length = audit(path.read_text(encoding="utf-8", errors="replace"))
        results.append({
            "product_id": row["product_id"],
            "method": method,
            "end_4_9_found": end_found,
            "block_chars": length,
        })
        if idx % 1000 == 0:
            print(f"{idx}/{len(rows)}", flush=True)

    with OUTPUT.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["product_id", "method", "end_4_9_found", "block_chars"])
        w.writeheader()
        w.writerows(results)

    n = len(results)
    print(f"\nTotal: {n}")
    for k, v in Counter(r["method"] for r in results).most_common():
        print(f"method {k}: {v} ({100 * v / n:.2f}%)")
    for k, v in Counter(r["end_4_9_found"] for r in results).most_common():
        print(f"end_4_9_found {k}: {v} ({100 * v / n:.2f}%)")
    small = [r for r in results if r["block_chars"] < 500]
    print(f"blocks < 500 chars: {len(small)}")


if __name__ == "__main__":
    main()
