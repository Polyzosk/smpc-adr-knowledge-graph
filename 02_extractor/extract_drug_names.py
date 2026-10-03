"""
Βημα 2 για το πιλοτικο σετ.

Για καθε product_id στο pilot_selection.csv
διαβαζει το HTML απο τον φακελο smpc_htmls.
Βρισκει την ενοτητα 1 Name of the medicinal product.
Κραταει το ονομα του φαρμακου.

Το αποτελεσμα μπαινει στο drug_per_smpc.csv
με πεδια product_id brand_name status.
"""

from __future__ import annotations

import csv
import re
import sys
from pathlib import Path

# Ρυθμιση για σωστα ελληνικα στο terminal
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from bs4 import BeautifulSoup


PILOT_FILE = Path("data/output/pilot_selection.csv")
HTML_DIR = Path("data/raw")
OUTPUT_FILE = Path("data/output/drug_per_smpc.csv")

SECTION1_RE = re.compile(r"\b1\b.*\bname of the medicinal product\b", re.IGNORECASE)
SECTION2_RE = re.compile(r"\b2\b.*\bqualitative.*quantitative\b", re.IGNORECASE)


def load_pilot_ids(path: Path) -> list[str]:
    if not path.exists():
        raise FileNotFoundError(f"Δεν βρέθηκε: {path}")
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        return [row["product_id"].strip() for row in reader]


def extract_section1_text(soup: BeautifulSoup) -> str:
    """
    Βρισκει την ενοτητα 1 Name of the medicinal product
    και επιστρεφει το κειμενο της.
    """
    # Συνηθισμενη μορφη eMC με details και summary
    for summary in soup.find_all("summary"):
        text = summary.get_text(" ", strip=True)
        if SECTION1_RE.search(text):
            section_div = summary.find_next_sibling("div")
            if section_div:
                return section_div.get_text(" ", strip=True)
            parent = summary.parent
            if parent:
                return parent.get_text(" ", strip=True)

    # Δευτερη λυση για html με h2 και h3
    for tag in soup.find_all(["h1", "h2", "h3", "h4"]):
        text = tag.get_text(" ", strip=True)
        if SECTION1_RE.search(text):
            parts = []
            for sib in tag.next_siblings:
                if hasattr(sib, "get_text"):
                    sib_text = sib.get_text(" ", strip=True)
                    if SECTION2_RE.search(sib_text):
                        break
                    if sib_text:
                        parts.append(sib_text)
            return " ".join(parts)

    return ""


def clean_drug_name(raw: str) -> str:
    """Καθαριζει το κειμενο και κραταει μικρο κομματι."""
    # Αν το κειμενο ειναι πολυ μεγαλο κραταω μονο την αρχη
    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    # Παιρνει μεχρι τρεις γραμμες
    short = " | ".join(lines[:3])
    return short[:300]


def main() -> None:
    ids = load_pilot_ids(PILOT_FILE)
    print(f"Φόρτωση {len(ids)} pilot IDs.")

    results = []
    for idx, product_id in enumerate(ids, start=1):
        html_path = HTML_DIR / f"smpc_{product_id}.html"
        brand_name = ""
        status = "Failed"

        try:
            if not html_path.exists():
                raise FileNotFoundError(f"HTML δεν βρέθηκε: {html_path}")

            html = html_path.read_text(encoding="utf-8", errors="replace")
            soup = BeautifulSoup(html, "html.parser")
            raw = extract_section1_text(soup)
            brand_name = clean_drug_name(raw)
            status = "Success" if brand_name else "EmptyExtraction"

        except Exception as exc:
            print(f"[{idx}/{len(ids)}] Product {product_id}: FAILED ({exc})")
            status = "Failed"

        results.append(
            {
                "product_id": product_id,
                "brand_name": brand_name,
                "status": status,
            }
        )
        print(f"[{idx}/{len(ids)}] Product {product_id}: {status} | {brand_name[:80]}")

    with OUTPUT_FILE.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["product_id", "brand_name", "status"])
        writer.writeheader()
        writer.writerows(results)

    success = sum(1 for r in results if r["status"] == "Success")
    print(f"\n=== Done === {success}/{len(results)} successful -> {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
