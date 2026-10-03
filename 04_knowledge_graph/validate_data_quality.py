"""
Comprehensive data-quality check for pilot_knowledge_graph_custom.ttl.
Ελέγχει: ATC, MedDRA, ICD, SmPC links, Drug-ADR occurrences, literals.
"""
from __future__ import annotations
import re, sys
from pathlib import Path
from collections import defaultdict

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

TTL = Path("data/output/pilot_knowledge_graph_custom.ttl")
text = TTL.read_text(encoding="utf-8")

SEP = "-" * 60

def section(title: str) -> None:
    print(f"\n{'='*60}")
    print(f"  {title}")
    print('='*60)

def find_all(pattern: str) -> list[str]:
    return re.findall(pattern, text)

# ── 1. Triples count ──────────────────────────────────────────
section("1. OVERVIEW")
drug_ids     = find_all(r':Drug_(\w+)\s+rdf:type owl:NamedIndividual')
adr_ids      = find_all(r':(adr_\w+)\s+rdf:type owl:NamedIndividual')
occ_ids      = find_all(r':(occ_\w+)\s+rdf:type owl:NamedIndividual')
smpc_ids     = find_all(r':(smpc_\d+)\s+rdf:type owl:NamedIndividual')

# Also catch slugified drug names
drug_blocks  = re.findall(r'(:[\w]+)\s+rdf:type owl:NamedIndividual\s*,\s*\n\s+:Drug\s*;', text)
adr_blocks   = re.findall(r'(:[\w]+)\s+rdf:type owl:NamedIndividual\s*,\s*\n\s+:AdverseDrugReaction\s*;', text)
occ_blocks   = re.findall(r'(:[\w]+)\s+rdf:type owl:NamedIndividual\s*,\s*\n\s+:DrugAdrOccurrence\s*;', text)
smpc_blocks  = re.findall(r'(:smpc_\d+)\s+rdf:type owl:NamedIndividual\s*,\s*\n\s+:SmPCDocument\s*;', text)

print(f"Drug individuals        : {len(drug_blocks)}")
print(f"ADR individuals         : {len(adr_blocks)}")
print(f"DrugAdrOccurrence       : {len(occ_blocks)}")
print(f"SmPCDocument individuals: {len(smpc_blocks)}")

# ── 2. ATC codes ──────────────────────────────────────────────
section("2. ATC CODES (:atcCode)")
atc_vals = find_all(r':atcCode "([^"]+)"')
atc_unknown  = [v for v in atc_vals if v == "UNKNOWN"]
atc_filled   = [v for v in atc_vals if v != "UNKNOWN"]
print(f"Total     : {len(atc_vals)}")
print(f"Filled    : {len(atc_filled)}  ({100*len(atc_filled)//max(len(atc_vals),1)}%)")
print(f"UNKNOWN   : {len(atc_unknown)}")
# Check format: ATC codes are 1-7 alphanumeric chars
bad_atc = [v for v in atc_filled if not re.match(r'^[A-Z]\d{2}[A-Z]{2}\d{2}$|^[A-Z]\d{2}[A-Z]{2}$|^[A-Z]\d{2}[A-Z]$|^[A-Z]\d{2}$|^[A-Z]$', v)]
print(f"Format OK : {len(atc_filled)-len(bad_atc)}/{len(atc_filled)}")
if bad_atc:
    print(f"  [WARN] Unexpected format: {bad_atc[:5]}")
else:
    print("  [OK] All ATC codes match expected format (A##XX## or shorter)")

# ── 3. MedDRA codes ───────────────────────────────────────────
section("3. MedDRA CODES (:meddraCode)")
meddra_vals    = find_all(r':meddraCode "([^"]+)"')
meddra_zero    = [v for v in meddra_vals if v == "0"]
meddra_filled  = [v for v in meddra_vals if v != "0"]
print(f"Total ADR individuals : {len(meddra_vals)}")
print(f"Filled (non-zero)     : {len(meddra_filled)}  ({100*len(meddra_filled)//max(len(meddra_vals),1)}%)")
print(f"Zero (missing)        : {len(meddra_zero)}")
# MedDRA codes are 8-digit numbers
bad_meddra = [v for v in meddra_filled if not re.match(r'^\d{7,8}$', v)]
print(f"Format OK             : {len(meddra_filled)-len(bad_meddra)}/{len(meddra_filled)}")
if bad_meddra:
    print(f"  [WARN] Unexpected format: {bad_meddra[:5]}")

# ── 4. ICD-10 codes ───────────────────────────────────────────
section("4. ICD-10 CODES (:icdCode)")
icd_vals    = find_all(r':icdCode "([^"]+)"')
icd_unknown = [v for v in icd_vals if v == "UNKNOWN"]
icd_filled  = [v for v in icd_vals if v != "UNKNOWN"]
print(f"Total ADR individuals : {len(icd_vals)}")
print(f"Filled (not UNKNOWN)  : {len(icd_filled)}  ({100*len(icd_filled)//max(len(icd_vals),1)}%)")
print(f"UNKNOWN               : {len(icd_unknown)}")
# ICD-10 format: letter + digits (e.g. T50.9, Z88.0, K29.7)
bad_icd = [v for v in icd_filled if not re.match(r'^[A-Z]\d{1,2}(\.\d+)?$', v)]
print(f"Format OK             : {len(icd_filled)-len(bad_icd)}/{len(icd_filled)}")
if bad_icd:
    print(f"  [WARN] Unexpected format (sample): {bad_icd[:5]}")

# ── 5. SmPC links (refersToSmPC) ──────────────────────────────
section("5. SmPC DOCUMENT LINKS (:refersToSmPC)")
refers = find_all(r':refersToSmPC (:smpc_\d+)')
smpc_targets = set(refers)
smpc_defined = set(smpc_blocks)
print(f"Occurrences with :refersToSmPC : {len(refers)}")
print(f"Unique SmPC targets linked     : {len(smpc_targets)}")
print(f"SmPC individuals defined       : {len(smpc_defined)}")
dangling = smpc_targets - smpc_defined
if dangling:
    print(f"  [ERROR] Dangling SmPC refs: {dangling}")
else:
    print("  [OK] All :refersToSmPC targets have a defined individual")

# ── 6. Occurrence completeness ────────────────────────────────
section("6. OCCURRENCE COMPLETENESS")
occ_freq  = find_all(r':frequency "([^"]+)"')
occ_soc   = find_all(r':soc "([^"]+)"')
occ_adr   = find_all(r':occurrenceOfAdr (:[\w]+)')
occ_drug  = find_all(r':hasOccurrence (:occ_[\w]+)')
print(f"Occurrences total              : {len(occ_blocks)}")
print(f"  with :frequency              : {len(occ_freq)}")
print(f"  with :soc                    : {len(occ_soc)}")
print(f"  with :occurrenceOfAdr        : {len(occ_adr)}")
print(f"  linked via :hasOccurrence    : {len(occ_drug)}")

freq_unknown = [v for v in occ_freq if v == "not known"]
freq_filled  = [v for v in occ_freq if v != "not known"]
print(f"  Frequency: filled={len(freq_filled)}, 'not known'={len(freq_unknown)}")

soc_unknown = [v for v in occ_soc if v == "UNKNOWN"]
soc_filled  = [v for v in occ_soc if v != "UNKNOWN"]
print(f"  SOC: filled={len(soc_filled)}, UNKNOWN={len(soc_unknown)}")

# ── 7. Drug label check ───────────────────────────────────────
section("7. DRUG LABELS (:drugLabel)")
drug_labels = find_all(r':drugLabel "([^"]+)"')
print(f"Drug labels present: {len(drug_labels)} / {len(drug_blocks)} expected")
if len(drug_labels) < len(drug_blocks):
    print("  [WARN] Some drugs are missing :drugLabel")
else:
    print("  [OK]")

# ── 8. ADR label check ────────────────────────────────────────
section("8. ADR LABELS (:adrLabel)")
adr_labels = find_all(r':adrLabel "([^"]+)"')
print(f"ADR labels present: {len(adr_labels)} / {len(adr_blocks)} expected")
if len(adr_labels) < len(adr_blocks):
    print("  [WARN] Some ADRs are missing :adrLabel")
else:
    print("  [OK]")

# ── 9. Source URLs ────────────────────────────────────────────
section("9. SmPC SOURCE URLs (:sourceUrl)")
source_urls = find_all(r':sourceUrl "([^"]+)"')
print(f"Source URLs defined: {len(source_urls)} / {len(smpc_blocks)} expected")
bad_urls = [u for u in source_urls if not u.startswith("https://")]
print(f"  HTTPS format OK: {len(source_urls)-len(bad_urls)}/{len(source_urls)}")
if bad_urls:
    print(f"  [WARN] Non-HTTPS: {bad_urls[:3]}")
else:
    print("  [OK]")

# ── 10. Final verdict ─────────────────────────────────────────
section("10. FINAL VERDICT")
issues = []
if atc_unknown:     issues.append(f"{len(atc_unknown)} UNKNOWN ATC codes")
if meddra_zero:     issues.append(f"{len(meddra_zero)} zero MedDRA codes ({100*len(meddra_zero)//len(meddra_vals):.0f}%)")
if icd_unknown:     issues.append(f"{len(icd_unknown)} UNKNOWN ICD codes ({100*len(icd_unknown)//len(icd_vals):.0f}%)")
if dangling:        issues.append(f"{len(dangling)} dangling SmPC refs")
if bad_atc:         issues.append(f"{len(bad_atc)} malformed ATC codes")

if not issues:
    print("[PASS] No critical issues found.")
else:
    print(f"[INFO] {len(issues)} area(s) with missing data:")
    for i in issues:
        print(f"  - {i}")
print()
print(f"Knowledge graph file: {TTL.name}")
print(f"File size: {TTL.stat().st_size/1024:.1f} KB")
