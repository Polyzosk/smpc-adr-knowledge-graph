"""
Part B of the evaluation: repeatability of the Gemini extraction.

Re-runs the extraction of 02_extractor/gemini_extract_adrs.py (same prompt,
same cleaning, same request/retry function, model from .env, temperature 0.0)
on the SmPCs of evaluation/evaluation_candidates.csv:
  - 3 runs with the 8,000-char cut  -> evaluation/gemini_run{1,2,3}.csv
  - 1 run without the cut, only for SmPCs above the limit -> evaluation/gemini_run_full.csv

Run from the repository root:
    python evaluation/rerun_gemini_subset.py
    python evaluation/rerun_gemini_subset.py --candidates evaluation/evaluation_candidates_2.csv --suffix _set2 --runs 1
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "02_extractor"))
import gemini_extract_adrs as gx  # noqa: E402

from clean_text import clean_text_untruncated  # noqa: E402

CUT = 8000
OUT = Path("evaluation")
CANDIDATES = OUT / "evaluation_candidates.csv"
FIELDS = ["product_id", "adr_term", "frequency", "soc"]


class QuotaExceeded(RuntimeError):
    pass


def load_candidates(path: Path) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def block_html(pid: str) -> str:
    path = gx.BLOCKS_DIR / f"{pid}_4_8_block.json"
    return json.loads(path.read_text(encoding="utf-8"))["content_html"]


def extract(text: str) -> list[dict]:
    prompt = gx.PROMPT_TEMPLATE.format(html_content=text)
    try:
        return gx.request_gemini_with_retry(prompt)
    except RuntimeError as exc:
        if "HTTP 429" in str(exc):
            raise QuotaExceeded(str(exc)) from exc
        raise


def to_rows(pid: str, adrs: list[dict]) -> list[dict]:
    return [
        {
            "product_id": pid,
            "adr_term": (a.get("adr_term") or "").strip(),
            "frequency": (a.get("frequency") or "").strip().lower(),
            "soc": (a.get("soc") or "").strip(),
        }
        for a in adrs
    ]


def run(name: str, pids: list[str], truncate: bool, first_call: list[bool]) -> None:
    rows: list[dict] = []
    for pid in pids:
        html = block_html(pid)
        text = gx.html_to_clean_text(html) if truncate else clean_text_untruncated(html)
        if not first_call[0]:
            time.sleep(gx.REQUEST_DELAY)
        first_call[0] = False
        adrs = extract(text)
        rows.extend(to_rows(pid, adrs))
        print(f"  {name} | {pid} | {len(adrs)} ADR", flush=True)
    with (OUT / f"{name}.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    print(f"[done] {name}.csv ({len(rows)} rows)", flush=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Re-run Gemini ADR extraction on an evaluation subset.")
    parser.add_argument("--candidates", type=Path, default=CANDIDATES)
    parser.add_argument("--suffix", default="", help="appended to output file names, e.g. _set2")
    parser.add_argument("--runs", type=int, default=3, help="number of truncated runs")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not gx.API_KEY:
        raise RuntimeError("GEMINI_API_KEY not found in .env")
    candidates = load_candidates(args.candidates)
    pids = [c["product_id"] for c in candidates]
    long_pids = [c["product_id"] for c in candidates if int(c["clean_chars"]) > CUT]
    print(f"Model: {gx.MODEL_NAME} | SmPCs: {pids} | above {CUT}: {long_pids}", flush=True)

    first_call = [True]
    plan = [(f"gemini_run{i}{args.suffix}", pids, True) for i in range(1, args.runs + 1)]
    if long_pids:
        plan.append((f"gemini_run_full{args.suffix}", long_pids, False))

    completed = []
    try:
        for name, run_pids, truncate in plan:
            run(name, run_pids, truncate, first_call)
            completed.append(name)
    except QuotaExceeded as exc:
        print(f"[STOP] quota exceeded: {exc}", flush=True)
    finally:
        print(f"Completed runs: {completed}", flush=True)


if __name__ == "__main__":
    main()
