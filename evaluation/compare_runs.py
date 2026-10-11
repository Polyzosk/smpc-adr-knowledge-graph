"""
Compares the Gemini repeatability runs (counts only, no ADR terms printed).

Pairs are (product_id, adr_term normalised: lowercase, collapsed whitespace).
Also compares with the original pipeline output (data/output/adr_per_smpc.csv)
and, for SmPCs above the 8,000-char cut, the truncated run1 with gemini_run_full.

Run from the repository root:
    python evaluation/compare_runs.py
"""
from __future__ import annotations

import csv
import re
from collections import Counter
from pathlib import Path

OUT = Path("evaluation")
RUNS = ["gemini_run1", "gemini_run2", "gemini_run3"]
ORIGINAL = Path("data/output/adr_per_smpc.csv")


def norm(term: str) -> str:
    return re.sub(r"\s+", " ", term.strip().lower())


def load(path: Path, pids: set[str]) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as f:
        return [r for r in csv.DictReader(f) if r["product_id"] in pids]


def pairs(rows: list[dict]) -> set[tuple[str, str]]:
    return {(r["product_id"], norm(r["adr_term"])) for r in rows}


def triples(rows: list[dict]) -> set[tuple[str, str, str, str]]:
    return {(r["product_id"], norm(r["adr_term"]), r["frequency"].strip().lower(), r["soc"].strip().lower()) for r in rows}


def main() -> None:
    with (OUT / "evaluation_candidates.csv").open(encoding="utf-8", newline="") as f:
        pids = [r["product_id"] for r in csv.DictReader(f)]
    pid_set = set(pids)
    runs = {name: load(OUT / f"{name}.csv", pid_set) for name in RUNS}
    original = load(ORIGINAL, pid_set)

    report: list[list] = [["metric", "product_id", "value"]]

    for pid in pids:
        counts = [sum(r["product_id"] == pid for r in runs[n]) for n in RUNS]
        orig = sum(r["product_id"] == pid for r in original)
        print(f"{pid}: rows run1/run2/run3 = {counts} | original pipeline = {orig}")
        for n, c in zip(RUNS, counts):
            report.append([f"rows_{n}", pid, c])
        report.append(["rows_original_pipeline", pid, orig])

    pair_sets = [pairs(runs[n]) for n in RUNS]
    membership = Counter(p for s in pair_sets for p in s)
    in_all = sum(1 for v in membership.values() if v == 3)
    in_two = sum(1 for v in membership.values() if v == 2)
    in_one = sum(1 for v in membership.values() if v == 1)
    print(f"Pairs (pid, term) across 3 runs: union={len(membership)} | in all 3={in_all} | in 2={in_two} | in 1 only={in_one}")
    report += [["pairs_union_3runs", "all", len(membership)], ["pairs_in_all_3", "all", in_all],
               ["pairs_in_2", "all", in_two], ["pairs_in_1_only", "all", in_one]]

    triple_sets = [triples(runs[n]) for n in RUNS]
    t_member = Counter(t for s in triple_sets for t in s)
    t_all = sum(1 for v in t_member.values() if v == 3)
    print(f"Rows identical incl. frequency+SOC across 3 runs: {t_all} of union {len(t_member)}")
    report += [["triples_union_3runs", "all", len(t_member)], ["triples_in_all_3", "all", t_all]]

    orig_pairs = pairs(original)
    common = pair_sets[0] & orig_pairs
    print(f"run1 vs original pipeline: common={len(common)} | only run1={len(pair_sets[0] - orig_pairs)} | "
          f"only original={len(orig_pairs - pair_sets[0])}")
    report += [["run1_vs_original_common", "all", len(common)],
               ["run1_only_vs_original", "all", len(pair_sets[0] - orig_pairs)],
               ["original_only_vs_run1", "all", len(orig_pairs - pair_sets[0])]]

    full_path = OUT / "gemini_run_full.csv"
    if full_path.exists():
        full = load(full_path, pid_set)
        for pid in sorted({r["product_id"] for r in full}):
            f_set = {p for p in pairs(full) if p[0] == pid}
            t_set = {p for p in pair_sets[0] if p[0] == pid}
            print(f"{pid} full vs truncated run1: full={len(f_set)} | truncated={len(t_set)} | common={len(f_set & t_set)} | "
                  f"only full={len(f_set - t_set)} | only truncated={len(t_set - f_set)}")
            report += [["full_pairs", pid, len(f_set)], ["truncated_run1_pairs", pid, len(t_set)],
                       ["full_vs_truncated_common", pid, len(f_set & t_set)],
                       ["only_full", pid, len(f_set - t_set)], ["only_truncated", pid, len(t_set - f_set)]]

    with (OUT / "run_comparison.csv").open("w", encoding="utf-8", newline="") as f:
        csv.writer(f).writerows(report)
    print("-> evaluation/run_comparison.csv")


if __name__ == "__main__":
    main()
