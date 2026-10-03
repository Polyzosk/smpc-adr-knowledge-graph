"""
ICD-10 enrichment για adr_per_smpc_enriched.csv μέσω UMLS API.

Στρατηγική (δύο πάσα):
  Pass 1 — Crosswalk: MedDRA code → UMLS CUI → ICD-10 code
           (για τα 422 ADRs που έχουν MedDRA αλλά όχι ICD)
  Pass 2 — Free-text search: adr_term → UMLS → ICD-10
           (για τα 307 ADRs που δεν έχουν τίποτα)

Είσοδος : adr_per_smpc_enriched.csv
Έξοδος  : adr_per_smpc_enriched.csv  (in-place update)
           icd_enrichment_log.csv
"""
from __future__ import annotations

import csv, os, sys, time
from pathlib import Path
from dotenv import load_dotenv
import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

load_dotenv()
UMLS_KEY   = os.getenv("UMLS_API_KEY", "").strip()
if not UMLS_KEY:
    raise RuntimeError("UMLS_API_KEY not found in .env")

ADR_FILE   = Path("data/output/adr_per_smpc_enriched.csv")
LOG_FILE   = Path("data/output/icd_enrichment_log.csv")
BASE       = "https://uts-ws.nlm.nih.gov/rest"
DELAY      = 0.15   # polite: stay well under UMLS rate limits

SESSION = requests.Session()

# ── UMLS helpers ──────────────────────────────────────────────

def umls_get(url: str, params: dict) -> dict:
    try:
        r = SESSION.get(url, params={**params, "apiKey": UMLS_KEY}, timeout=20)
        if r.status_code == 404:
            return {}
        r.raise_for_status()
        return r.json()
    except Exception:
        return {}


def meddra_to_cui(meddra_code: str) -> str:
    """MedDRA PT code → UMLS CUI via identifier search."""
    data = umls_get(f"{BASE}/search/current", {
        "string": meddra_code,
        "sabs": "MDR",
        "searchType": "exact",
        "inputType": "code",
        "returnIdType": "concept",
        "pageSize": 1,
    })
    results = data.get("result", {}).get("results", [])
    if results and results[0].get("ui") not in ("", "NONE"):
        return results[0]["ui"]
    return ""


def cui_to_icd(cui: str) -> str:
    """UMLS CUI → ICD-10CM code via crosswalk (atoms endpoint)."""
    data = umls_get(f"{BASE}/content/current/CUI/{cui}/atoms", {
        "sabs": "ICD10CM,ICD10",
        "pageSize": 5,
    })
    for atom in data.get("result", []):
        code = atom.get("code", "")
        # Strip URL prefix if present
        if "/" in code:
            code = code.rsplit("/", 1)[-1]
        if code and code not in ("NOCODE", ""):
            return code
    return ""


def term_to_icd(term: str) -> str:
    """Free-text ADR term → ICD-10 code via UMLS search."""
    for sab in ("ICD10CM", "ICD10"):
        data = umls_get(f"{BASE}/search/current", {
            "string": term,
            "sabs": sab,
            "searchType": "normalizedString",
            "returnIdType": "code",
            "pageSize": 1,
        })
        results = data.get("result", {}).get("results", [])
        if results and results[0].get("ui") not in ("", "NONE"):
            code = results[0]["ui"]
            if "/" in code:
                code = code.rsplit("/", 1)[-1]
            return code
    return ""

# ── Load and deduplicate ADR terms ───────────────────────────

def load_adr_rows(path: Path) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def get_fieldnames(path: Path) -> list[str]:
    with path.open(encoding="utf-8", newline="") as f:
        return csv.DictReader(f).fieldnames or []


# ── Main ──────────────────────────────────────────────────────

def main() -> None:
    rows = load_adr_rows(ADR_FILE)
    fields = get_fieldnames(ADR_FILE)

    # Build unique term index → pick best row per term
    term_map: dict[str, dict] = {}
    for r in rows:
        t = r["adr_term"].lower().strip()
        if t not in term_map:
            term_map[t] = r
        else:
            # prefer row with more data
            if term_map[t].get("meddra_code", "0") == "0" and r.get("meddra_code", "0") != "0":
                term_map[t] = r

    # Separate into targets
    need_crosswalk  = {t: d for t, d in term_map.items()
                       if d.get("meddra_code", "0") != "0"
                       and d.get("icd_code", "UNKNOWN") in ("UNKNOWN", "")}
    need_freetext   = {t: d for t, d in term_map.items()
                       if d.get("meddra_code", "0") == "0"
                       and d.get("icd_code", "UNKNOWN") in ("UNKNOWN", "")}

    print(f"ADR terms total          : {len(term_map)}")
    print(f"  Pass 1 (crosswalk)     : {len(need_crosswalk)}")
    print(f"  Pass 2 (free-text)     : {len(need_freetext)}")
    print()

    icd_cache: dict[str, str] = {}  # term → icd_code

    # ── Pass 1: MedDRA code → CUI → ICD ─────────────────────
    print("=== Pass 1: MedDRA crosswalk ===")
    cw_ok = 0
    for idx, (term, row) in enumerate(need_crosswalk.items(), 1):
        mcode = row.get("meddra_code", "").strip()
        cui   = meddra_to_cui(mcode)
        time.sleep(DELAY)
        icd = ""
        if cui:
            icd = cui_to_icd(cui)
            time.sleep(DELAY)
        if not icd:
            # Fallback: free-text search with the term
            icd = term_to_icd(term)
            time.sleep(DELAY)
        icd_cache[term] = icd
        if icd:
            cw_ok += 1
        if idx % 50 == 0 or idx == len(need_crosswalk):
            print(f"  [{idx}/{len(need_crosswalk)}] filled so far: {cw_ok}", flush=True)

    # ── Pass 2: free-text search ─────────────────────────────
    print(f"\n=== Pass 2: Free-text search ===")
    ft_ok = 0
    for idx, (term, _) in enumerate(need_freetext.items(), 1):
        icd = term_to_icd(term)
        time.sleep(DELAY)
        icd_cache[term] = icd
        if icd:
            ft_ok += 1
        if idx % 50 == 0 or idx == len(need_freetext):
            print(f"  [{idx}/{len(need_freetext)}] filled so far: {ft_ok}", flush=True)

    # ── Apply cache back to ALL rows ─────────────────────────
    updated = 0
    for r in rows:
        term = r["adr_term"].lower().strip()
        if r.get("icd_code", "UNKNOWN") not in ("UNKNOWN", ""):
            continue   # already has ICD
        new_icd = icd_cache.get(term, "")
        if new_icd:
            r["icd_code"] = new_icd
            updated += 1

    # ── Save enriched CSV ────────────────────────────────────
    with ADR_FILE.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)

    # ── Log ──────────────────────────────────────────────────
    log_fields = ["adr_term", "meddra_code", "icd_code_new", "source"]
    with LOG_FILE.open("w", encoding="utf-8", newline="") as f:
        w2 = csv.DictWriter(f, fieldnames=log_fields)
        w2.writeheader()
        for term, icd in icd_cache.items():
            src = "crosswalk" if term in need_crosswalk else "freetext"
            w2.writerow({
                "adr_term": term,
                "meddra_code": term_map.get(term, {}).get("meddra_code", ""),
                "icd_code_new": icd or "UNKNOWN",
                "source": src,
            })

    # ── Summary ──────────────────────────────────────────────
    all_icd   = sum(1 for r in rows if r.get("icd_code","UNKNOWN") not in ("UNKNOWN",""))
    total_adr = len(set(r["adr_term"].lower().strip() for r in rows))

    print(f"\n=== Summary ===")
    print(f"Pass 1 (crosswalk) ICD found : {cw_ok}/{len(need_crosswalk)}")
    print(f"Pass 2 (free-text) ICD found : {ft_ok}/{len(need_freetext)}")
    print(f"Rows updated in CSV          : {updated}")
    print(f"Total ICD coverage (rows)    : {all_icd}/{len(rows)}")
    print(f"Output: {ADR_FILE}")
    print(f"Log   : {LOG_FILE}")


if __name__ == "__main__":
    main()
