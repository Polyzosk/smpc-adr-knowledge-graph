"""
SPARQL queries on pilot_knowledge_graph_custom.ttl via rdflib.
Αποθηκεύει τα αποτελέσματα σε CSV και εκτυπώνει στο terminal.
"""
from __future__ import annotations
import csv, sys
from pathlib import Path
from rdflib import Graph

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

TTL   = Path("data/output/pilot_knowledge_graph_custom.ttl")
OUT   = Path("sparql_results")
OUT.mkdir(exist_ok=True)

print("Loading graph…", flush=True)
g = Graph()
g.parse(TTL, format="turtle")
print(f"Loaded {len(g)} triples.\n")

BASE = "http://example.org/pilot-smpc-kg#"

PREFIX = """
PREFIX : <http://example.org/pilot-smpc-kg#>
PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>
"""

SEP = "-" * 60

def run(title: str, sparql: str, csv_name: str, limit_print: int = 20) -> None:
    print(f"\n{'='*60}")
    print(f"  {title}")
    print('='*60)
    results = list(g.query(PREFIX + sparql))
    if not results:
        print("  (no results)")
        return

    vars_ = [str(v) for v in results[0].labels]
    # Print top rows
    header = " | ".join(f"{v:40}" if i == 0 else f"{v}" for i, v in enumerate(vars_))
    print(header)
    print("-" * len(header))
    for i, row in enumerate(results):
        if i >= limit_print:
            print(f"  … ({len(results) - limit_print} more rows in CSV)")
            break
        vals = []
        for v in vars_:
            cell = str(row[v]) if row[v] is not None else ""
            vals.append(cell)
        print(" | ".join(f"{vals[0]:40}" if j == 0 else f"{vals[j]}" for j, _ in enumerate(vars_)))

    # Save to CSV
    out_path = OUT / f"{csv_name}.csv"
    with out_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(vars_)
        for row in results:
            w.writerow([str(row[v]) if row[v] is not None else "" for v in vars_])
    print(f"\n  → Saved {len(results)} rows to {out_path}")


# ── Q1: ADR count per drug ────────────────────────────────────
run(
    "Q1 — Αριθμός ADR ανά φάρμακο",
    """
    SELECT ?drugLabel (COUNT(DISTINCT ?occ) AS ?numADR)
    WHERE {
      ?drug a :Drug ;
            :drugLabel ?drugLabel ;
            :hasOccurrence ?occ .
    }
    GROUP BY ?drugLabel
    ORDER BY DESC(?numADR)
    """,
    "q1_adr_per_drug",
)

# ── Q2: Top ADRs across all drugs ─────────────────────────────
run(
    "Q2 — Top 20 ADRs (σε πόσα φάρμακα εμφανίζεται)",
    """
    SELECT ?adrLabel (COUNT(DISTINCT ?drug) AS ?numDrugs)
    WHERE {
      ?occ a :DrugAdrOccurrence ;
           :occurrenceOfAdr ?adr .
      ?adr :adrLabel ?adrLabel .
      ?drug :hasOccurrence ?occ .
    }
    GROUP BY ?adrLabel
    ORDER BY DESC(?numDrugs)
    LIMIT 20
    """,
    "q2_top_adrs",
)

# ── Q3: ATC category distribution ────────────────────────────
run(
    "Q3 — Κατανομή φαρμάκων ανά ATC κατηγορία (1ο γράμμα)",
    """
    SELECT ?atcCategory (COUNT(?drug) AS ?count)
    WHERE {
      ?drug a :Drug ;
            :atcCode ?atc .
      BIND(SUBSTR(STR(?atc), 1, 1) AS ?atcCategory)
    }
    GROUP BY ?atcCategory
    ORDER BY ?atcCategory
    """,
    "q3_atc_categories",
    limit_print=30,
)

# ── Q4: Common-frequency ADRs ─────────────────────────────────
run(
    "Q4 — ADRs με συχνότητα 'common' (δείγμα)",
    """
    SELECT ?drugLabel ?adrLabel ?soc
    WHERE {
      ?drug a :Drug ;
            :drugLabel ?drugLabel ;
            :hasOccurrence ?occ .
      ?occ :occurrenceOfAdr ?adr ;
           :frequency ?freq ;
           :soc ?soc .
      ?adr :adrLabel ?adrLabel .
      FILTER(LCASE(STR(?freq)) = "common")
    }
    ORDER BY ?drugLabel ?adrLabel
    """,
    "q4_common_adrs",
)

# ── Q5: SOC distribution ──────────────────────────────────────
run(
    "Q5 — Κατανομή ADR occurrences ανά System Organ Class",
    """
    SELECT ?soc (COUNT(?occ) AS ?count)
    WHERE {
      ?occ a :DrugAdrOccurrence ;
           :soc ?soc .
      FILTER(?soc != "UNKNOWN")
    }
    GROUP BY ?soc
    ORDER BY DESC(?count)
    """,
    "q5_soc_distribution",
    limit_print=25,
)

# ── Q6: Drugs + ATC + ADR count summary ──────────────────────
run(
    "Q6 — Πλήρης λίστα φαρμάκων με ATC + πλήθος ADR",
    """
    SELECT ?drugLabel ?atcCode (COUNT(DISTINCT ?occ) AS ?adrCount)
    WHERE {
      ?drug a :Drug ;
            :drugLabel ?drugLabel ;
            :atcCode ?atcCode ;
            :hasOccurrence ?occ .
    }
    GROUP BY ?drugLabel ?atcCode
    ORDER BY ?atcCode
    """,
    "q6_drug_atc_summary",
    limit_print=50,
)

# ── Q7: Patient scenario — which of the patient's drugs list ADR X ──
# Drugs: Lyrica (10309), INSPRA (120), Ibuprofen 5% gel (16018)
# ADR X: dizziness, matched via MedDRA code 10013573
run(
    "Q7 — Σενάριο ασθενή: ποια από τα φάρμακα αναφέρουν dizziness (MedDRA 10013573)",
    """
    SELECT ?pid ?drugLabel ?atcCode ?adrLabel ?frequency ?soc
    WHERE {
      VALUES ?pid { "10309" "120" "16018" }
      ?drug a :Drug ;
            :productId ?pid ;
            :drugLabel ?drugLabel ;
            :atcCode ?atcCode .
      OPTIONAL {
        ?drug :hasOccurrence ?occ .
        ?occ :occurrenceOfAdr ?adr ;
             :frequency ?frequency ;
             :soc ?soc .
        ?adr :meddraCode "10013573" ;
             :adrLabel ?adrLabel .
      }
    }
    ORDER BY ?drugLabel
    """,
    "q7_patient_scenario",
)

print(f"\n{'='*60}")
print(f"  Αποτελέσματα αποθηκευμένα στο: {OUT.resolve()}")
print('='*60)
