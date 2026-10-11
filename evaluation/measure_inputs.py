"""
Part A of the evaluation (read-only): text length vs the 8,000-char cut,
ICD-10 vs ICD-10-CM source, evaluation candidates, copies of their sources.

Run from the repository root:
    python evaluation/measure_inputs.py
"""
from __future__ import annotations

import csv
import json
import shutil
import statistics
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "02_extractor"))
from gemini_extract_adrs import html_to_clean_text  # noqa: E402

from clean_text import clean_text_untruncated  # noqa: E402

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

CUT = 8000
OUT = Path("evaluation")
BLOCKS = Path("data/output/extracted_4_8_blocks")
RAW = Path("data/raw")
PILOT = Path("data/output/pilot_selection.csv")
MANIFEST = Path("data/output/dataset_manifest.csv")
ADR = Path("data/output/adr_per_smpc.csv")
ADR_ENRICHED = Path("data/output/adr_per_smpc_enriched.csv")
DRUGS = Path("data/output/drug_per_smpc.csv")
UMLS_LOG = Path("data/output/umls_enrichment_log.csv")
ICD_LOG = Path("data/output/icd_enrichment_log.csv")
TTL = Path("data/output/pilot_knowledge_graph_custom.ttl")


def read_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def load_block(pid: str) -> dict:
    return json.loads((BLOCKS / f"{pid}_4_8_block.json").read_text(encoding="utf-8"))


def percentile(values: list[int], p: float) -> float:
    ordered = sorted(values)
    k = (len(ordered) - 1) * p
    lo, hi = int(k), min(int(k) + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (k - lo)


def write_lengths(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["product_id", "clean_chars", "truncated"])
        w.writeheader()
        w.writerows(rows)


def summarize(label: str, rows: list[dict]) -> None:
    lengths = [r["clean_chars"] for r in rows]
    over = sum(r["truncated"] for r in rows)
    print(f"[A1] {label}: n={len(rows)} | >{CUT}: {over} ({100 * over / len(rows):.2f}%) | "
          f"median={statistics.median(lengths):.0f} | p90={percentile(lengths, 0.9):.0f} | max={max(lengths)}")


def step_a1(pilot_ids: list[str]) -> dict[str, int]:
    pilot_rows = []
    for pid in pilot_ids:
        html = load_block(pid)["content_html"]
        full = clean_text_untruncated(html)
        assert full[:CUT] == html_to_clean_text(html), f"clean-text mismatch for {pid}"
        pilot_rows.append({"product_id": pid, "clean_chars": len(full), "truncated": len(full) > CUT})
    write_lengths(OUT / "text_length_pilot.csv", pilot_rows)
    summarize("pilot", pilot_rows)

    all_rows = []
    manifest_ids = [r["product_id"] for r in read_csv(MANIFEST)]
    for idx, pid in enumerate(manifest_ids, 1):
        n = len(clean_text_untruncated(load_block(pid)["content_html"]))
        all_rows.append({"product_id": pid, "clean_chars": n, "truncated": n > CUT})
        if idx % 1000 == 0:
            print(f"  {idx}/{len(manifest_ids)}", flush=True)
    write_lengths(OUT / "text_length_all.csv", all_rows)
    summarize("all", all_rows)
    return {r["product_id"]: r["clean_chars"] for r in pilot_rows}


def step_a2() -> None:
    from rdflib import Graph, Namespace

    ns = Namespace("http://example.org/pilot-smpc-kg#")
    g = Graph()
    g.parse(TTL, format="turtle")
    final_icd = {}
    for adr in g.subjects(predicate=ns.icdCode):
        code = str(g.value(adr, ns.icdCode))
        if code != "UNKNOWN":
            final_icd[str(g.value(adr, ns.adrLabel))] = code

    pass1 = {}
    for r in read_csv(UMLS_LOG):
        if r["target_field"] == "icd_code" and r["matched_code"] not in ("", "UNKNOWN"):
            pass1.setdefault(r["entity_value"].strip().lower(), set()).add((r["matched_code"], r["source_vocab"]))
    pass2 = {r["adr_term"]: r["icd_code_new"] for r in read_csv(ICD_LOG) if r["icd_code_new"] != "UNKNOWN"}

    origin = Counter()
    for term, code in final_icd.items():
        sources = {src for c, src in pass1.get(term, set()) if c == code}
        if len(sources) == 1:
            origin[f"pass1:{sources.pop()}"] += 1
        elif len(sources) > 1:
            origin["pass1:ambiguous"] += 1
        elif pass2.get(term) == code:
            origin["pass2:source_not_logged"] += 1
        else:
            origin["unattributed"] += 1
    print(f"[A2] ADRs with ICD code in TTL: {len(final_icd)} | {dict(origin)}")
    with (OUT / "icd_source_breakdown.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["origin", "count"])
        for k, v in origin.most_common():
            w.writerow([k, v])


def step_a3_a4(pilot_lengths: dict[str, int]) -> None:
    adr_counts = Counter(r["product_id"] for r in read_csv(ADR))
    brands = {r["product_id"]: r["brand_name"].split("|")[0].strip() for r in read_csv(DRUGS)}
    info = {
        pid: {
            "product_id": pid,
            "brand_name": brands.get(pid, ""),
            "clean_chars": n,
            "gemini_adr_count": adr_counts.get(pid, 0),
            "has_table": bool(load_block(pid).get("has_table")),
        }
        for pid, n in pilot_lengths.items()
    }

    chosen: list[dict] = []

    def pick(pred, reason: str) -> None:
        pool = [v for v in info.values() if pred(v) and v["product_id"] not in {c["product_id"] for c in chosen}]
        if not pool:
            print(f"[A3] no candidate for: {reason}")
            return
        best = min(pool, key=lambda v: (v["gemini_adr_count"], v["clean_chars"]))
        chosen.append({**best, "reason": reason})

    pick(lambda v: v["clean_chars"] > 8000, "long section, exceeds the 8,000-char cut")
    pick(lambda v: v["has_table"], "frequency table")
    pick(lambda v: not v["has_table"], "free text, no table")
    pick(lambda v: v["gemini_adr_count"] < 40, "small (<40 ADR)")
    pick(lambda v: v["gemini_adr_count"] < 40, "small (<40 ADR)")

    fields = ["product_id", "brand_name", "clean_chars", "gemini_adr_count", "has_table", "reason"]
    with (OUT / "evaluation_candidates.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(chosen)
    for c in chosen:
        print(f"[A3] {c['product_id']} | {c['brand_name']} | chars={c['clean_chars']} | "
              f"adr={c['gemini_adr_count']} | table={c['has_table']} | {c['reason']}")

    src = OUT / "evaluation_sources"
    src.mkdir(exist_ok=True)
    for c in chosen:
        pid = c["product_id"]
        shutil.copy2(RAW / f"smpc_{pid}.html", src / f"smpc_{pid}.html")
        shutil.copy2(BLOCKS / f"{pid}_4_8_block.json", src / f"{pid}_4_8_block.json")
    print(f"[A4] copied {2 * len(chosen)} files to {src}")


def main() -> None:
    OUT.mkdir(exist_ok=True)
    pilot_ids = [r["product_id"] for r in read_csv(PILOT)]
    pilot_lengths = step_a1(pilot_ids)
    step_a2()
    step_a3_a4(pilot_lengths)


if __name__ == "__main__":
    main()
