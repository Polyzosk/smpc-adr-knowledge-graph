"""
Βημα 6 για το πιλοτικο σετ.

Διαβαζει τα αρχεια drug_per_smpc.csv και adr_per_smpc.csv.
Διαβαζει το template_ontology_custom.txt.
Παραγει custom ontology χωρις OpenPVSignal ως pilot_knowledge_graph_custom.ttl.
Τελος κανει validation και γραφει το validation_log_custom.txt.
"""

from __future__ import annotations

import csv
import hashlib
import re
import sys
from pathlib import Path

# Ρυθμιση για σωστα ελληνικα στο terminal
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


DRUG_FILE = Path("data/output/drug_per_smpc.csv")
ADR_FILE = Path("data/output/adr_per_smpc.csv")
DRUG_FILE_ENRICHED = Path("data/output/drug_per_smpc_enriched.csv")
ADR_FILE_ENRICHED = Path("data/output/adr_per_smpc_enriched.csv")
INN_FILE = Path("data/output/rxnorm_lookup_log.csv")   # product_id → active_ingredient
TEMPLATE_FILE = Path("04_knowledge_graph/template_ontology_custom.txt")
OUTPUT_TTL = Path("data/output/pilot_knowledge_graph_custom.ttl")
VALIDATION_LOG = Path("data/output/validation_log_custom.txt")
BASE_NAMESPACE = "http://example.org/pilot-smpc-kg#"
SMPC_URL_TEMPLATE = "https://www.medicines.org.uk/emc/product/{product_id}/smpc/print"


def slugify(text: str) -> str:
    """Κανει το κειμενο πιο καθαρο για URI fragment."""
    text = text.strip().lower()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s-]+", "_", text)
    text = text.strip("_")
    return (text or "unknown")[:80]


def escape_ttl_literal(value: str) -> str:
    """Κανει escape σε quotes/backslashes για ασφαλη Turtle literals."""
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ").strip()


def load_drugs(path: Path) -> list[dict]:
    if not path.exists():
        raise FileNotFoundError(f"Δεν βρέθηκε: {path}")
    with path.open("r", encoding="utf-8", newline="") as f:
        return [row for row in csv.DictReader(f) if row.get("status") == "Success"]


def load_adrs(path: Path) -> list[dict]:
    if not path.exists():
        raise FileNotFoundError(f"Δεν βρέθηκε: {path}")
    with path.open("r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def load_template(path: Path) -> list[str]:
    if not path.exists():
        raise FileNotFoundError(f"Δεν βρέθηκε template: {path}")
    return path.read_text(encoding="utf-8").splitlines()


def _load_inn_map() -> dict[str, str]:
    """Φορτωνει product_id -> active_ingredient απο rxnorm_lookup_log.csv."""
    if not INN_FILE.exists():
        return {}
    with INN_FILE.open("r", encoding="utf-8", newline="") as f:
        return {
            row["product_id"].strip(): (row.get("active_ingredient") or "").strip().lower()
            for row in csv.DictReader(f)
            if row.get("product_id")
        }


def build_drug_map(drugs: list[dict]) -> dict[str, dict]:
    """Map product_id -> metadata για σταθερη δημιουργια Drug individuals."""
    inn_map = _load_inn_map()
    mapped: dict[str, dict] = {}
    used_slugs: set[str] = set()

    for row in drugs:
        product_id = (row.get("product_id") or "").strip()
        if not product_id:
            continue

        raw_name = (row.get("brand_name") or "").split("|")[0].strip()
        if not raw_name:
            raw_name = f"product_{product_id}"

        base_slug = slugify(raw_name)
        drug_slug = base_slug
        suffix = 2
        while drug_slug in used_slugs:
            drug_slug = f"{base_slug}_{suffix}"
            suffix += 1

        used_slugs.add(drug_slug)
        mapped[product_id] = {
            "slug": drug_slug,
            "label": raw_name,
            "atc_code": (row.get("atc_code") or "UNKNOWN").strip() or "UNKNOWN",
            "active_ingredient": inn_map.get(product_id, ""),
        }

    return mapped


# Canonical MedDRA SOC names (handles spelling variants from SmPC extraction)
_SOC_CANONICAL: dict[str, str] = {
    # Gastrointestinal
    "gastro-intestinal disorders":                     "Gastrointestinal Disorders",
    "gastrointestinal disorders":                      "Gastrointestinal Disorders",
    "gastrointestinal":                                "Gastrointestinal Disorders",
    # Skin
    "skin and subcutaneous tissues disorders":         "Skin and Subcutaneous Tissue Disorders",
    "skin and subcutaneous tissue disorders":          "Skin and Subcutaneous Tissue Disorders",
    "skin and subcutaneous system disorders":          "Skin and Subcutaneous Tissue Disorders",
    "skin and appendages":                             "Skin and Subcutaneous Tissue Disorders",
    # Nervous system
    "nervous system disorders":                        "Nervous System Disorders",
    "nervous system":                                  "Nervous System Disorders",
    "nervous system disorder":                         "Nervous System Disorders",
    # Psychiatric
    "psychiatric disorders":                           "Psychiatric Disorders",
    # Cardiac
    "cardiac disorders":                               "Cardiac Disorders",
    "cardiovascular":                                  "Cardiac Disorders",
    # General disorders
    "general disorders and administration site conditions": "General Disorders and Administration Site Conditions",
    # Eye
    "eye disorders":                                   "Eye Disorders",
    # Vascular
    "vascular disorders":                              "Vascular Disorders",
    # Blood
    "blood and lymphatic system disorders":            "Blood and Lymphatic System Disorders",
    "blood and the lymphatic system disorders":        "Blood and Lymphatic System Disorders",
    "blood and lymphatic disorders":                   "Blood and Lymphatic System Disorders",
    # Hepatobiliary
    "hepatobiliary disorders":                         "Hepatobiliary Disorders",
    "hepato-biliary disorders":                        "Hepatobiliary Disorders",
    "hepatic":                                         "Hepatobiliary Disorders",
    # Renal
    "renal and urinary disorders":                     "Renal and Urinary Disorders",
    "renal and urinary":                               "Renal and Urinary Disorders",
    "renal and urinary tract disorders":               "Renal and Urinary Disorders",
    # Investigations
    "investigations":                                  "Investigations",
    "laboratory and other examinations":               "Investigations",
    # Musculoskeletal
    "musculoskeletal and connective tissue disorders": "Musculoskeletal and Connective Tissue Disorders",
    "musculo-skeletal and connective tissue disorders": "Musculoskeletal and Connective Tissue Disorders",
    "musculoskeletal, connective tissue and bone disorders": "Musculoskeletal and Connective Tissue Disorders",
    "musculoskeletal connective tissue and bone disorders": "Musculoskeletal and Connective Tissue Disorders",
    # Immune system
    "immune system disorders":                         "Immune System Disorders",
    # Metabolism
    "metabolism and nutrition disorders":              "Metabolism and Nutrition Disorders",
    "metabolism and nutritional disorders":            "Metabolism and Nutrition Disorders",
    "metabolic and nutrition disorders":               "Metabolism and Nutrition Disorders",
    "metabolism and nutrition system disorders":       "Metabolism and Nutrition Disorders",
    "electrolyte and acid-base disorders":             "Metabolism and Nutrition Disorders",
    # Reproductive
    "reproductive system and breast disorders":        "Reproductive System and Breast Disorders",
    # Respiratory
    "respiratory, thoracic and mediastinal disorders": "Respiratory, Thoracic and Mediastinal Disorders",
    "respiratory, thoracic, and mediastinal disorders": "Respiratory, Thoracic and Mediastinal Disorders",
    "respiratory thoracic and mediastinal disorders":  "Respiratory, Thoracic and Mediastinal Disorders",
    "respiratory, thoracic and mediastinal disorder":  "Respiratory, Thoracic and Mediastinal Disorders",
    "respiratory system disorders":                    "Respiratory, Thoracic and Mediastinal Disorders",
    "respiratory":                                     "Respiratory, Thoracic and Mediastinal Disorders",
    # Infections
    "infections and infestations":                     "Infections and Infestations",
    "infections":                                      "Infections and Infestations",
    # Ear
    "ear and labyrinth disorders":                     "Ear and Labyrinth Disorders",
    # Endocrine
    "endocrine disorders":                             "Endocrine Disorders",
    # Injury
    "injury, poisoning and procedural complications":  "Injury, Poisoning and Procedural Complications",
    # Neoplasms
    "neoplasms benign, malignant and unspecified (incl cysts and polyps)": "Neoplasms Benign, Malignant and Unspecified",
    "neoplasms benign, malignant and unspecified":     "Neoplasms Benign, Malignant and Unspecified",
    # Pregnancy
    "pregnancy, puerperium and perinatal conditions":  "Pregnancy, Puerperium and Perinatal Conditions",
    # Not known / not specified / not applicable
    "not known":                                       "Not Known",
    "not specified":                                   "Not Known",
    "not applicable":                                  "Not Known",
    # Other
    "social circumstances":                            "Social Circumstances",
    "surgical and medical procedures":                 "Surgical and Medical Procedures",
    "congenital, familial and genetic disorders":      "Congenital, Familial and Genetic Disorders",
    "product issues":                                  "Product Issues",
}


def normalize_soc(soc: str) -> str:
    """Κανονικοποιεί το SOC string: title-case + διόρθωση γνωστών παραλλαγών."""
    cleaned = soc.strip()
    if not cleaned or cleaned == "UNKNOWN":
        return "UNKNOWN"
    return _SOC_CANONICAL.get(cleaned.lower(), cleaned.title())


def build_adr_map(adrs: list[dict]) -> dict[str, dict]:
    """Map adr_term (lowercase canonical) -> ADR metadata."""
    mapped: dict[str, dict] = {}
    used_slugs: set[str] = set()

    for row in adrs:
        raw_term = (row.get("adr_term") or "").strip()
        if not raw_term:
            continue

        # Canonical key: lowercase for deduplication across case variants
        term = raw_term.lower()
        if term in mapped:
            # Merge: prefer richer metadata from later rows
            existing = mapped[term]
            if existing["meddra_code"] == "0":
                code = (row.get("meddra_code") or "0").strip() or "0"
                if code != "0":
                    existing["meddra_code"] = code
            if existing["icd_code"] == "UNKNOWN":
                icd = (row.get("icd_code") or "UNKNOWN").strip() or "UNKNOWN"
                if icd != "UNKNOWN":
                    existing["icd_code"] = icd
            continue

        base_slug = slugify(term)
        adr_slug = base_slug
        suffix = 2
        while adr_slug in used_slugs:
            adr_slug = f"{base_slug}_{suffix}"
            suffix += 1

        used_slugs.add(adr_slug)
        mapped[term] = {
            "slug": adr_slug,
            "label": term,                                              # stored lowercase
            "meddra_preferred_term": (row.get("meddra_preferred_term") or term).strip().lower() or term,
            "meddra_code": (row.get("meddra_code") or "0").strip() or "0",
            "icd_code": (row.get("icd_code") or "UNKNOWN").strip() or "UNKNOWN",
        }

    return mapped


def build_smpc_map(product_ids: set[str]) -> dict[str, dict]:
    """Map product_id -> SmPCDocument metadata."""
    mapped: dict[str, dict] = {}
    for product_id in sorted(product_ids, key=lambda x: int(x) if x.isdigit() else x):
        mapped[product_id] = {
            "slug": f"smpc_{product_id}",
            "source_url": SMPC_URL_TEMPLATE.format(product_id=product_id),
        }
    return mapped


def build_occurrence_rows(adrs: list[dict]) -> list[dict]:
    """Κραταει μοναδικο ζευγος (product_id, adr_term_lowercase) με frequency/soc."""
    merged: dict[tuple[str, str], dict] = {}

    for row in adrs:
        product_id = (row.get("product_id") or "").strip()
        adr_term = (row.get("adr_term") or "").strip().lower()   # normalize to lowercase
        if not product_id or not adr_term:
            continue

        frequency = (row.get("frequency") or "").strip().lower() or "not known"
        soc = normalize_soc((row.get("soc") or "").strip())      # canonical SOC
        key = (product_id, adr_term)

        if key not in merged:
            merged[key] = {
                "product_id": product_id,
                "adr_term": adr_term,
                "frequency": frequency,
                "soc": soc,
            }
            continue

        # Αν βρεθει duplicate row, προτιμαμε non-default τιμες.
        current = merged[key]
        if current["frequency"] == "not known" and frequency != "not known":
            current["frequency"] = frequency
        if current["soc"] == "UNKNOWN" and soc != "UNKNOWN":
            current["soc"] = soc

    return list(merged.values())


def build_drug_triples(drug_map: dict[str, dict]) -> list[str]:
    lines = [
        "",
        "#################################################################",
        "#    Drug Individuals",
        "#################################################################",
    ]

    for product_id in sorted(drug_map, key=lambda x: int(x) if x.isdigit() else x):
        drug = drug_map[product_id]
        ing = drug.get("active_ingredient", "")
        atc_line = f'    :atcCode "{escape_ttl_literal(drug["atc_code"])}"'
        if ing:
            atc_line += " ;"
            lines.extend(
                [
                    "",
                    f":{drug['slug']} rdf:type owl:NamedIndividual ,",
                    "    :Drug ;",
                    f'    :productId "{escape_ttl_literal(product_id)}" ;',
                    f'    :drugLabel "{escape_ttl_literal(drug["label"])}" ;',
                    atc_line,
                    f'    :activeIngredient "{escape_ttl_literal(ing)}" .',
                ]
            )
        else:
            lines.extend(
                [
                    "",
                    f":{drug['slug']} rdf:type owl:NamedIndividual ,",
                    "    :Drug ;",
                    f'    :productId "{escape_ttl_literal(product_id)}" ;',
                    f'    :drugLabel "{escape_ttl_literal(drug["label"])}" ;',
                    f'    :atcCode "{escape_ttl_literal(drug["atc_code"])}" .',
                ]
            )

    return lines


def build_adr_triples(adr_map: dict[str, dict]) -> list[str]:
    lines = [
        "",
        "#################################################################",
        "#    ADR Individuals",
        "#################################################################",
    ]

    for term in sorted(adr_map):
        adr = adr_map[term]
        lines.extend(
            [
                "",
                f":{adr['slug']} rdf:type owl:NamedIndividual ,",
                "    :AdverseDrugReaction ;",
                f'    :adrLabel "{escape_ttl_literal(adr["label"])}" ;',
                f'    :meddraPreferredTerm "{escape_ttl_literal(adr["meddra_preferred_term"])}" ;',
                f'    :meddraCode "{escape_ttl_literal(adr["meddra_code"])}" ;',
                f'    :icdCode "{escape_ttl_literal(adr["icd_code"])}" .',
            ]
        )

    return lines


def build_smpc_triples(smpc_map: dict[str, dict]) -> list[str]:
    lines = [
        "",
        "#################################################################",
        "#    SmPC Document Individuals",
        "#################################################################",
    ]

    for product_id in sorted(smpc_map, key=lambda x: int(x) if x.isdigit() else x):
        smpc = smpc_map[product_id]
        lines.extend(
            [
                "",
                f":{smpc['slug']} rdf:type owl:NamedIndividual ,",
                "    :SmPCDocument ;",
                f'    :productId "{escape_ttl_literal(product_id)}" ;',
                f'    :sourceUrl "{escape_ttl_literal(smpc["source_url"])}" .',
            ]
        )

    return lines


def occurrence_slug(product_id: str, adr_term: str) -> str:
    """Παραγει σταθερο, κοντο id για occurrence individual."""
    digest = hashlib.sha1(f"{product_id}|{adr_term}".encode("utf-8")).hexdigest()[:12]
    return f"occ_{product_id}_{digest}"


def build_occurrence_triples(
    occurrence_rows: list[dict],
    drug_map: dict[str, dict],
    adr_map: dict[str, dict],
    smpc_map: dict[str, dict],
) -> list[str]:
    lines = [
        "",
        "#################################################################",
        "#    Drug ADR Occurrence Individuals",
        "#################################################################",
    ]

    # Σταθερη σειρα για reproducible output
    sorted_rows = sorted(
        occurrence_rows,
        key=lambda r: (
            int(r["product_id"]) if r["product_id"].isdigit() else r["product_id"],
            r["adr_term"].lower(),
        ),
    )

    for row in sorted_rows:
        product_id = row["product_id"]
        adr_term = row["adr_term"]
        drug = drug_map.get(product_id)
        adr = adr_map.get(adr_term)
        smpc = smpc_map.get(product_id)
        if not drug or not adr or not smpc:
            continue

        occ_slug = occurrence_slug(product_id, adr_term)
        lines.extend(
            [
                "",
                f":{occ_slug} rdf:type owl:NamedIndividual ,",
                "    :DrugAdrOccurrence ;",
                f"    :occurrenceOfAdr :{adr['slug']} ;",
                f"    :refersToSmPC :{smpc['slug']} ;",
                f'    :productId "{escape_ttl_literal(product_id)}" ;',
                f'    :frequency "{escape_ttl_literal(row["frequency"])}" ;',
                f'    :soc "{escape_ttl_literal(row["soc"])}" .',
                f":{drug['slug']} :hasOccurrence :{occ_slug} .",
            ]
        )

    return lines


def validate_ttl(path: Path) -> str:
    """Ελεγχος οτι το TTL ειναι εγκυρο και ακολουθει το custom schema."""
    try:
        import rdflib
        from rdflib.namespace import RDF

        text = path.read_text(encoding="utf-8")
        if "OpenPVSignal" in text:
            return "INVALID — Found forbidden OpenPVSignal references in output."

        g = rdflib.Graph()
        g.parse(str(path), format="turtle")
        ns = rdflib.Namespace(BASE_NAMESPACE)

        occ_class = ns.DrugAdrOccurrence
        occ_rel = ns.occurrenceOfAdr
        smpc_rel = ns.refersToSmPC
        freq = ns.frequency
        soc = ns.soc

        errors: list[str] = []
        occurrences = list(g.subjects(RDF.type, occ_class))
        for occ in occurrences:
            if len(list(g.objects(occ, occ_rel))) != 1:
                errors.append(f"{occ} must have exactly 1 occurrenceOfAdr.")
            if len(list(g.objects(occ, smpc_rel))) != 1:
                errors.append(f"{occ} must have exactly 1 refersToSmPC.")
            if len(list(g.objects(occ, freq))) != 1:
                errors.append(f"{occ} must have exactly 1 frequency.")
            if len(list(g.objects(occ, soc))) != 1:
                errors.append(f"{occ} must have exactly 1 soc.")

        if errors:
            preview = " | ".join(errors[:5])
            return f"INVALID — Integrity check failed. {preview}"

        return (
            f"VALID — {len(g)} triples parsed successfully. "
            f"Occurrences checked: {len(occurrences)}."
        )
    except ImportError:
        return "rdflib not installed — skipping validation (pip install rdflib)."
    except Exception as exc:
        return f"INVALID — Parse error: {exc}"


def main() -> None:
    print("=== Παραγωγη custom ontology Turtle (.ttl) ===")

    template_lines = load_template(TEMPLATE_FILE)
    drug_source = DRUG_FILE_ENRICHED if DRUG_FILE_ENRICHED.exists() else DRUG_FILE
    adr_source = ADR_FILE_ENRICHED if ADR_FILE_ENRICHED.exists() else ADR_FILE
    drugs = load_drugs(drug_source)
    adrs = load_adrs(adr_source)
    drug_map = build_drug_map(drugs)
    adr_map = build_adr_map(adrs)
    smpc_map = build_smpc_map(set(drug_map.keys()))
    occurrence_rows = build_occurrence_rows(adrs)

    print(f"Drugs loaded: {len(drug_map)}")
    print(f"Unique ADRs loaded: {len(adr_map)}")
    print(f"SmPC documents loaded: {len(smpc_map)}")
    print(f"Drug-ADR occurrences loaded: {len(occurrence_rows)}")
    print(f"Drug source file: {drug_source}")
    print(f"ADR source file: {adr_source}")

    content = list(template_lines)
    content.extend(build_drug_triples(drug_map))
    content.extend(build_adr_triples(adr_map))
    content.extend(build_smpc_triples(smpc_map))
    content.extend(build_occurrence_triples(occurrence_rows, drug_map, adr_map, smpc_map))

    OUTPUT_TTL.write_text("\n".join(content), encoding="utf-8")
    print(f"\nTurtle file -> {OUTPUT_TTL.resolve()}")

    validation_result = validate_ttl(OUTPUT_TTL)
    VALIDATION_LOG.write_text(
        f"File: {OUTPUT_TTL.resolve()}\nResult: {validation_result}\n",
        encoding="utf-8",
    )
    print(f"Validation: {validation_result}")
    print(f"Validation log -> {VALIDATION_LOG.resolve()}")


if __name__ == "__main__":
    main()
