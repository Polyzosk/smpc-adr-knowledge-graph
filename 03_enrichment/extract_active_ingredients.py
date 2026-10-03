"""
Εξαγωγή δραστικής ουσίας (INN) + ATC κωδικού από brand name φαρμάκου.

Μέθοδος:
  1. Hardcoded product_id → INN map για τα 50 φάρμακα του pilot.
     Επαληθεύτηκαν με βάση EMA SmPC / WHO INN / BNF.
  2. RxNorm API (NIH/NLM, δωρεάν, χωρίς API key) για ATC κωδικό.

Είσοδος : drug_per_smpc.csv  (product_id, brand_name, status)
Έξοδος  : drug_with_ingredients_pilot.csv
           rxnorm_lookup_log.csv
"""

from __future__ import annotations

import csv
import sys
import time
from pathlib import Path

import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DRUG_INPUT = Path("data/output/drug_per_smpc.csv")
OUTPUT_CSV = Path("data/output/drug_with_ingredients_pilot.csv")
LOG_CSV    = Path("data/output/rxnorm_lookup_log.csv")

RXNORM_BASE = "https://rxnav.nlm.nih.gov/REST"
DELAY       = 0.4   # polite: ≤20 req/s recommended by RxNorm

SESSION = requests.Session()
SESSION.headers.update({"Accept": "application/json"})

# ──────────────────────────────────────────────────────────────────
# Complete product_id → INN mapping (all 50 pilot drugs)
# Source: EMA product pages / BNF / WHO INN list
# ──────────────────────────────────────────────────────────────────
PRODUCT_INN_MAP: dict[str, str] = {
    "120":    "eplerenone",
    "503":    "oxycodone, naloxone",
    "1945":   "sodium cromoglicate",
    "2602":   "ciclosporin",
    "2648":   "labetalol",
    "3815":   "oxcarbazepine",
    "3887":   "methylphenidate",
    "3904":   "gadoxetate disodium",
    "4732":   "perindopril",
    "6554":   "gentamicin, hydrocortisone",
    "7484":   "quetiapine",
    "7700":   "morphine",
    "7708":   "oxycodone, naloxone",
    "7867":   "rabeprazole",
    "8137":   "ibuprofen",
    "9280":   "oxycodone",
    "9321":   "ibuprofen",
    "9715":   "budesonide",
    "9765":   "dorzolamide, timolol",
    "10060":  "riluzole",
    "10309":  "pregabalin",
    "10334":  "atomoxetine",
    "10419":  "melatonin",
    "10555":  "isotretinoin",
    "10560":  "coagulation factor x",
    "10755":  "pregabalin",
    "11053":  "olanzapine",
    "11509":  "adenosine",
    "11906":  "ozanimod",
    "13261":  "sotalol",
    "13596":  "sodium valproate",
    "13659":  "efavirenz, emtricitabine, tenofovir disoproxil",
    "13689":  "amoxicillin",
    "13742":  "fesoterodine",
    "13936":  "testosterone",
    "14210":  "amlodipine, valsartan",
    "14563":  "montelukast",
    "14749":  "lercanidipine",
    "14799":  "solifenacin, tamsulosin",
    "14804":  "iodixanol",
    "15886":  "sulfasalazine",
    "15960":  "albumin, human",
    "16018":  "ibuprofen",
    "100179": "ganciclovir",
    "100626": "bosutinib",
    "100689": "famotidine",
    "100725": "mirtazapine",
    "100999": "loperamide",
    "101803": "venlafaxine",
    "101979": "cladribine",
}

# Manual ATC overrides for cases where RxNorm fails or returns a
# non-primary code (e.g., ibuprofen C01EB16 = neonatal cardiac use;
# M01AE01 = general anti-inflammatory, which matches these SmPCs).
# Sources: WHO ATC/DDD Index 2024.
MANUAL_ATC: dict[str, str] = {
    "1945":  "R01AC01",   # sodium cromoglicate – ophthalmic/nasal mast cell stabiliser
    "3904":  "V08CA10",   # gadoxetate disodium – MRI contrast agent
    "13596": "N03AG01",   # sodium valproate – antiepileptic
    "15960": "B05AA01",   # albumin, human – plasma substitute
    # ibuprofen entries: override C01EB16 → M01AE01 (NSAID analgesic)
    "8137":  "M01AE01",
    "9321":  "M01AE01",
    "16018": "M01AE01",
}

# ──────────────────────────────────────────────────────────────────
# RxNorm ATC lookup
# ──────────────────────────────────────────────────────────────────

def _rxnorm_get(url: str, params: dict) -> dict:
    try:
        r = SESSION.get(url, params=params, timeout=15)
        r.raise_for_status()
        return r.json()
    except Exception:
        return {}


def _search_rxcuis(term: str) -> list[str]:
    """Fuzzy search → unique RXCUI list."""
    data = _rxnorm_get(f"{RXNORM_BASE}/approximateTerm.json",
                       {"term": term, "maxEntries": 10})
    seen: dict[str, bool] = {}
    for c in data.get("approximateGroup", {}).get("candidate", []):
        cui = c.get("rxcui", "")
        if cui and cui not in seen:
            seen[cui] = True
    return list(seen.keys())


def _rxcui_atc(rxcui: str) -> str:
    """RXCUI → ATC code via allProperties endpoint."""
    data = _rxnorm_get(f"{RXNORM_BASE}/rxcui/{rxcui}/allProperties.json",
                       {"prop": "all"})
    for concept in data.get("propConceptGroup", {}).get("propConcept", []):
        if concept.get("propName") == "ATC":
            val = concept.get("propValue", "")
            if val:
                return val
    return ""


def _rxcui_to_ingredient_cuis(rxcui: str) -> list[str]:
    """Clinical Drug RXCUI → ingredient (IN/PIN) RXCUIs."""
    data = _rxnorm_get(f"{RXNORM_BASE}/rxcui/{rxcui}/related.json",
                       {"tty": "IN+PIN+MIN"})
    cuis: list[str] = []
    for group in data.get("relatedGroup", {}).get("conceptGroup", []):
        for prop in group.get("conceptProperties", []):
            cui = prop.get("rxcui", "")
            if cui:
                cuis.append(cui)
    return cuis


def lookup_atc_rxnorm(inn: str) -> tuple[str, str]:
    """
    INN string → (atc_code, method_note).
    For combo INNs (e.g. 'oxycodone, naloxone'), looks up the first ingredient.
    """
    primary = inn.split(",")[0].strip()

    rxcuis = _search_rxcuis(primary)
    time.sleep(DELAY)
    if not rxcuis:
        return "UNKNOWN", "not found in RxNorm"

    for cui in rxcuis[:5]:
        # Try direct: if CUI is already an ingredient-type concept
        code = _rxcui_atc(cui)
        time.sleep(DELAY)
        if code:
            return code, "rxnorm_direct"

        # Try via ingredient: CUI might be a clinical drug
        ing_cuis = _rxcui_to_ingredient_cuis(cui)
        time.sleep(DELAY)
        for ing_cui in ing_cuis[:3]:
            code = _rxcui_atc(ing_cui)
            time.sleep(DELAY)
            if code:
                return code, "rxnorm_via_ingredient"

    return "UNKNOWN", "not found in RxNorm"


# ──────────────────────────────────────────────────────────────────
# CSV I/O
# ──────────────────────────────────────────────────────────────────

def load_drugs(path: Path) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as f:
        return [r for r in csv.DictReader(f) if r.get("status") == "Success"]


OUTPUT_FIELDS = ["product_id", "brand_name", "status",
                 "active_ingredient", "atc_code", "atc_source"]


def write_output(rows: list[dict]) -> None:
    with OUTPUT_CSV.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=OUTPUT_FIELDS)
        w.writeheader()
        w.writerows(rows)


def write_log(log: list[dict]) -> None:
    with LOG_CSV.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=OUTPUT_FIELDS)
        w.writeheader()
        w.writerows(log)


# ──────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────

def main() -> None:
    drugs = load_drugs(DRUG_INPUT)
    total = len(drugs)
    results: list[dict] = []

    print("=== Active Ingredient + ATC Extraction ===")
    print(f"Drugs: {total}  |  INN: hardcoded map  |  ATC: RxNorm API\n")

    for idx, row in enumerate(drugs, start=1):
        pid        = row["product_id"].strip()
        brand_name = row["brand_name"].strip()

        inn = PRODUCT_INN_MAP.get(pid, "")

        if inn:
            atc_code, atc_src = lookup_atc_rxnorm(inn)
        else:
            atc_code, atc_src = "UNKNOWN", "inn not in map"
            print(f"  [!] product_id {pid} not in INN map", flush=True)

        # Apply manual override (takes priority over RxNorm result)
        if pid in MANUAL_ATC:
            atc_code = MANUAL_ATC[pid]
            atc_src  = "manual_who_atc"

        icon = "+" if atc_code != "UNKNOWN" else "-"
        print(f"[{idx:2}/{total}] [{icon}] {brand_name[:50]:52} "
              f"INN: {inn:35} ATC: {atc_code}", flush=True)

        results.append({
            "product_id":      pid,
            "brand_name":      brand_name,
            "status":          row["status"],
            "active_ingredient": inn,
            "atc_code":        atc_code,
            "atc_source":      atc_src,
        })
        write_output(results)

    write_log(results)

    filled_inn = sum(1 for r in results if r["active_ingredient"])
    filled_atc = sum(1 for r in results if r["atc_code"] != "UNKNOWN")

    print(f"\n=== Summary ===")
    print(f"INN filled  : {filled_inn}/{total} ({100*filled_inn//total}%)")
    print(f"ATC filled  : {filled_atc}/{total} ({100*filled_atc//total}%)")
    print(f"Output      : {OUTPUT_CSV}")
    print(f"Log         : {LOG_CSV}")


if __name__ == "__main__":
    main()
