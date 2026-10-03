"""
MedDRA enrichment για τα 313 ADR terms χωρίς κωδικό.

Στρατηγική:
  1. Clean term: αφαίρεση παρένθεσης, ληψη πρώτου token σε compound terms
  2. UMLS search με cleaned term → MDR vocabulary
  3. Αν αποτύχει: δοκιμή με approximate search

Είσοδος : adr_per_smpc_enriched.csv
Έξοδος  : adr_per_smpc_enriched.csv (in-place)
           meddra_enrichment_log.csv
"""
from __future__ import annotations

import csv, os, re, sys, time
from pathlib import Path
from dotenv import load_dotenv
import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

load_dotenv()
UMLS_KEY = os.getenv("UMLS_API_KEY", "").strip()
if not UMLS_KEY:
    raise RuntimeError("UMLS_API_KEY not found in .env")

ADR_FILE = Path("data/output/adr_per_smpc_enriched.csv")
LOG_FILE = Path("data/output/meddra_enrichment_log.csv")
BASE     = "https://uts-ws.nlm.nih.gov/rest"
DELAY    = 0.15

SESSION = requests.Session()

# ── Term cleaning ─────────────────────────────────────────────

# Separators used in compound SmPC ADR strings
_COMPOUND_SEP = re.compile(
    r'\s*/\s*|\s+or\s+|\s+and\s+|\s+including\s+|\s+with\s+|\s+such\s+as\s+',
    re.IGNORECASE,
)
# Parenthetical content (abbreviations, qualifiers)
_PARENS = re.compile(r'\s*\([^)]*\)')
# Trailing punctuation / codes (e.g. "aggression b")
_TRAILING_SINGLE = re.compile(r'\s+[a-z]$')


def clean_term(raw: str) -> list[str]:
    """
    Returns 1-2 candidate search strings from a raw SmPC ADR term.
    Priority: cleaned original → first component of compound.
    """
    t = raw.strip().lower()
    t = _PARENS.sub('', t).strip()       # remove parenthetical
    t = _TRAILING_SINGLE.sub('', t).strip()  # remove trailing single letter

    candidates = [t]

    # Split on compound separators and add first component as second candidate
    parts = [p.strip() for p in _COMPOUND_SEP.split(t) if p.strip()]
    if len(parts) > 1:
        candidates.append(parts[0])      # first component often most specific

    return candidates


# ── UMLS helpers ──────────────────────────────────────────────

def umls_search_meddra(term: str, search_type: str = "normalizedString") -> tuple[str, str]:
    """Returns (meddra_code, preferred_term) or ('', '')."""
    try:
        r = SESSION.get(f"{BASE}/search/current", params={
            "apiKey": UMLS_KEY,
            "string": term,
            "sabs": "MDR",
            "searchType": search_type,
            "returnIdType": "code",
            "pageSize": 1,
        }, timeout=20)
        if r.status_code == 404:
            return "", ""
        r.raise_for_status()
        results = r.json().get("result", {}).get("results", [])
        if results and results[0].get("ui") not in ("", "NONE"):
            return results[0]["ui"], results[0].get("name", "")
    except Exception:
        pass
    return "", ""


# ── Main ──────────────────────────────────────────────────────

def main() -> None:
    rows = list(csv.DictReader(ADR_FILE.open(encoding="utf-8")))
    fields = list(csv.DictReader(ADR_FILE.open(encoding="utf-8")).fieldnames or [])

    # Build unique term index
    term_map: dict[str, dict] = {}
    for r in rows:
        t = r["adr_term"].lower().strip()
        if t not in term_map:
            term_map[t] = r
        elif term_map[t].get("meddra_code", "0") == "0" and r.get("meddra_code", "0") != "0":
            term_map[t] = r

    missing = {t: d for t, d in term_map.items()
               if d.get("meddra_code", "0") in ("0", "")}

    print(f"Terms missing MedDRA code : {len(missing)}/1345\n")

    cache: dict[str, tuple[str, str]] = {}   # term → (code, pref_term)
    found = 0

    for idx, (term, _) in enumerate(missing.items(), 1):
        candidates = clean_term(term)
        code, pref = "", ""

        for candidate in candidates:
            if not candidate or len(candidate) < 3:
                continue
            # Try normalizedString first, then approximate
            for stype in ("normalizedString", "approximate"):
                code, pref = umls_search_meddra(candidate, stype)
                time.sleep(DELAY)
                if code:
                    break
            if code:
                break

        cache[term] = (code, pref)
        if code:
            found += 1

        if idx % 50 == 0 or idx == len(missing):
            print(f"  [{idx}/{len(missing)}] found so far: {found}", flush=True)

    # Apply back to all rows
    updated = 0
    for r in rows:
        t = r["adr_term"].lower().strip()
        if r.get("meddra_code", "0") not in ("0", ""):
            continue
        new_code, new_pref = cache.get(t, ("", ""))
        if new_code:
            r["meddra_code"] = new_code
            if r.get("meddra_preferred_term", "") in ("", t):
                r["meddra_preferred_term"] = new_pref.lower() if new_pref else t
            updated += 1

    # Save
    with ADR_FILE.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)

    # Log
    with LOG_FILE.open("w", encoding="utf-8", newline="") as f:
        w2 = csv.DictWriter(f, fieldnames=["original_term", "cleaned_candidate",
                                            "meddra_code", "meddra_preferred_term"])
        w2.writeheader()
        for term, (code, pref) in cache.items():
            cands = clean_term(term)
            w2.writerow({"original_term": term,
                         "cleaned_candidate": cands[0] if cands else "",
                         "meddra_code": code or "0",
                         "meddra_preferred_term": pref})

    # Summary
    all_filled = sum(1 for t, r in term_map.items()
                     if cache.get(t, ("0",))[0] or r.get("meddra_code", "0") != "0")
    print(f"\n=== Summary ===")
    print(f"Previously missing : {len(missing)}")
    print(f"Newly found        : {found}  ({100*found//len(missing)}%)")
    print(f"Rows updated       : {updated}")
    print(f"Total MedDRA coverage (unique terms) : ~{all_filled}/1345")
    print(f"Output : {ADR_FILE}")
    print(f"Log    : {LOG_FILE}")


if __name__ == "__main__":
    main()
