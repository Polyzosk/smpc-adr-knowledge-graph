"""
Βημα enrichment με UMLS API.

Σκοπος:
- Συμπληρωνει οπου λειπουν κωδικοι:
  - Drug -> atc_code (best effort απο UMLS source ATC)
  - ADR  -> meddra_code (source MDR), icd_code (source ICD10CM/ICD10)

Εισοδοι:
- drug_per_smpc.csv
- adr_per_smpc.csv

Εξοδοι:
- drug_per_smpc_enriched.csv
- adr_per_smpc_enriched.csv
- umls_enrichment_log.csv

Ρυθμιση:
- Environment variable: UMLS_API_KEY
"""

from __future__ import annotations

import csv
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

import requests


if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


UMLS_API_KEY = os.getenv("UMLS_API_KEY", "").strip()
UMLS_SEARCH_URL = "https://uts-ws.nlm.nih.gov/rest/search/current"
REQUEST_TIMEOUT = 30
REQUEST_DELAY_SECONDS = 0.0
MAX_RETRIES = 2
ADR_LOOKUP_WORKERS = 8

DRUG_INPUT = Path("data/output/drug_per_smpc.csv")
ADR_INPUT = Path("data/output/adr_per_smpc.csv")
DRUG_OUTPUT = Path("data/output/drug_per_smpc_enriched.csv")
ADR_OUTPUT = Path("data/output/adr_per_smpc_enriched.csv")
LOG_OUTPUT = Path("data/output/umls_enrichment_log.csv")


@dataclass
class MatchResult:
    code: str
    matched_name: str
    source: str
    status: str


def load_csv(path: Path) -> list[dict]:
    if not path.exists():
        raise FileNotFoundError(f"Δεν βρέθηκε το αρχείο: {path}")
    with path.open("r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def normalize_drug_query(raw_label: str) -> str:
    """Κανει πιο καθαρο query term για ATC αναζητηση."""
    label = (raw_label or "").strip()
    if not label:
        return ""

    label = label.split("|")[0].strip()
    label = re.sub(r"\s+", " ", label)

    # Προσπαθουμε να κρατησουμε το πρωτο ονομα πριν απο dosage/forms.
    tokens = label.split(" ")
    cut_idx = len(tokens)
    for i, tok in enumerate(tokens):
        clean = tok.lower().strip(".,;:()[]")
        if re.search(r"\d", clean):
            cut_idx = i
            break
        if clean in {"mg", "mcg", "ml", "tablets", "tablet", "capsules", "capsule", "solution"}:
            cut_idx = i
            break
    reduced = " ".join(tokens[:cut_idx]).strip()
    return reduced or label


def umls_search(query: str, sab: str) -> MatchResult:
    """Best-effort lookup σε συγκεκριμενο source vocabulary."""
    if not query:
        return MatchResult(code="", matched_name="", source=sab, status="empty_query")

    params_base = {
        "apiKey": UMLS_API_KEY,
        "sabs": sab,
        "returnIdType": "code",
        "pageSize": 1,
    }

    search_modes = ["exact", "words"]
    last_error = ""
    for mode in search_modes:
        params = dict(params_base)
        params["string"] = query
        params["searchType"] = mode

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                response = requests.get(UMLS_SEARCH_URL, params=params, timeout=REQUEST_TIMEOUT)
                if response.status_code != 200:
                    raise RuntimeError(f"HTTP {response.status_code}: {response.text[:300]}")

                payload = response.json()
                results = payload.get("result", {}).get("results", [])
                valid = [r for r in results if r.get("ui") and r.get("ui") != "NONE"]
                if valid:
                    top = valid[0]
                    return MatchResult(
                        code=str(top.get("ui", "")).strip(),
                        matched_name=str(top.get("name", "")).strip(),
                        source=str(top.get("rootSource", sab)).strip() or sab,
                        status=f"matched_{mode}",
                    )
                break
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                if attempt < MAX_RETRIES:
                    time.sleep(min(1.0 * attempt, 3.0))
                else:
                    return MatchResult(code="", matched_name=last_error, source=sab, status="error")
        if REQUEST_DELAY_SECONDS:
            time.sleep(REQUEST_DELAY_SECONDS)

    return MatchResult(code="", matched_name=last_error, source=sab, status="not_found")


def enrich_drugs(drug_rows: list[dict], logs: list[dict]) -> list[dict]:
    cache: dict[str, MatchResult] = {}
    enriched: list[dict] = []

    for row in drug_rows:
        if row.get("status") != "Success":
            continue

        product_id = (row.get("product_id") or "").strip()
        brand_name = (row.get("brand_name") or "").strip()
        query = normalize_drug_query(brand_name)

        if query in cache:
            match = cache[query]
        else:
            match = umls_search(query, "ATC")
            cache[query] = match
            if REQUEST_DELAY_SECONDS:
                time.sleep(REQUEST_DELAY_SECONDS)

        atc_code = match.code or "UNKNOWN"
        enriched.append(
            {
                "product_id": product_id,
                "brand_name": brand_name,
                "status": row.get("status", ""),
                "atc_code": atc_code,
            }
        )
        logs.append(
            {
                "entity_type": "drug",
                "entity_value": brand_name,
                "query_used": query,
                "target_field": "atc_code",
                "source_vocab": match.source,
                "matched_code": atc_code,
                "matched_name": match.matched_name,
                "status": match.status,
            }
        )
    return enriched


def lookup_adr_term(term: str) -> tuple[str, MatchResult, MatchResult]:
    """Κανει τα required lookups για εναν μοναδικο ADR term."""
    meddra_match = umls_search(term, "MDR")
    icd_match = umls_search(term, "ICD10CM")
    if not icd_match.code:
        icd_match = umls_search(term, "ICD10")
    return term, meddra_match, icd_match


def enrich_adrs(adr_rows: list[dict], logs: list[dict]) -> list[dict]:
    unique_terms = sorted({(row.get("adr_term") or "").strip() for row in adr_rows if (row.get("adr_term") or "").strip()})
    meddra_cache: dict[str, MatchResult] = {}
    icd_cache: dict[str, MatchResult] = {}

    print(f"Unique ADR terms to lookup: {len(unique_terms)}")
    completed = 0
    with ThreadPoolExecutor(max_workers=ADR_LOOKUP_WORKERS) as executor:
        future_map = {executor.submit(lookup_adr_term, term): term for term in unique_terms}
        for future in as_completed(future_map):
            term = future_map[future]
            completed += 1
            try:
                resolved_term, meddra_match, icd_match = future.result()
            except Exception as exc:
                meddra_match = MatchResult(code="", matched_name=f"{type(exc).__name__}: {exc}", source="MDR", status="error")
                icd_match = MatchResult(code="", matched_name=f"{type(exc).__name__}: {exc}", source="ICD10CM", status="error")
                resolved_term = term
            meddra_cache[resolved_term] = meddra_match
            icd_cache[resolved_term] = icd_match
            if completed % 100 == 0 or completed == len(unique_terms):
                print(f"ADR lookup progress: {completed}/{len(unique_terms)}")

    enriched: list[dict] = []

    for row in adr_rows:
        term = (row.get("adr_term") or "").strip()
        frequency = (row.get("frequency") or "").strip()
        soc = (row.get("soc") or "").strip()
        product_id = (row.get("product_id") or "").strip()
        meddra_match = meddra_cache.get(term, MatchResult(code="", matched_name="", source="MDR", status="not_found"))
        icd_match = icd_cache.get(term, MatchResult(code="", matched_name="", source="ICD10CM", status="not_found"))

        meddra_code = meddra_match.code or "0"
        icd_code = icd_match.code or "UNKNOWN"

        enriched.append(
            {
                "product_id": product_id,
                "adr_term": term,
                "frequency": frequency,
                "soc": soc,
                "meddra_code": meddra_code,
                "icd_code": icd_code,
                "meddra_preferred_term": term,
            }
        )

        logs.append(
            {
                "entity_type": "adr",
                "entity_value": term,
                "query_used": term,
                "target_field": "meddra_code",
                "source_vocab": meddra_match.source,
                "matched_code": meddra_code,
                "matched_name": meddra_match.matched_name,
                "status": meddra_match.status,
            }
        )
        logs.append(
            {
                "entity_type": "adr",
                "entity_value": term,
                "query_used": term,
                "target_field": "icd_code",
                "source_vocab": icd_match.source,
                "matched_code": icd_code,
                "matched_name": icd_match.matched_name,
                "status": icd_match.status,
            }
        )
    return enriched


def write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def summarize(rows: list[dict], field: str, unknown_value: str) -> tuple[int, int]:
    total = len(rows)
    filled = sum(1 for r in rows if str(r.get(field, "")).strip() != unknown_value)
    return filled, total


def main() -> None:
    if not UMLS_API_KEY:
        raise RuntimeError("Δεν βρέθηκε UMLS_API_KEY στο environment.")

    print("=== UMLS enrichment started ===")
    drug_rows = load_csv(DRUG_INPUT)
    adr_rows = load_csv(ADR_INPUT)
    logs: list[dict] = []

    print(f"Drugs input rows: {len(drug_rows)}")
    print(f"ADR input rows: {len(adr_rows)}")

    enriched_drugs = enrich_drugs(drug_rows, logs)
    enriched_adrs = enrich_adrs(adr_rows, logs)

    write_csv(
        DRUG_OUTPUT,
        enriched_drugs,
        ["product_id", "brand_name", "status", "atc_code"],
    )
    write_csv(
        ADR_OUTPUT,
        enriched_adrs,
        [
            "product_id",
            "adr_term",
            "frequency",
            "soc",
            "meddra_code",
            "icd_code",
            "meddra_preferred_term",
        ],
    )
    write_csv(
        LOG_OUTPUT,
        logs,
        [
            "entity_type",
            "entity_value",
            "query_used",
            "target_field",
            "source_vocab",
            "matched_code",
            "matched_name",
            "status",
        ],
    )

    atc_filled, atc_total = summarize(enriched_drugs, "atc_code", "UNKNOWN")
    meddra_filled, meddra_total = summarize(enriched_adrs, "meddra_code", "0")
    icd_filled, icd_total = summarize(enriched_adrs, "icd_code", "UNKNOWN")

    print("\n=== UMLS enrichment done ===")
    print(f"ATC filled: {atc_filled}/{atc_total}")
    print(f"MedDRA filled: {meddra_filled}/{meddra_total}")
    print(f"ICD filled: {icd_filled}/{icd_total}")
    print(f"Drug output: {DRUG_OUTPUT.resolve()}")
    print(f"ADR output: {ADR_OUTPUT.resolve()}")
    print(f"Log output: {LOG_OUTPUT.resolve()}")


if __name__ == "__main__":
    main()
