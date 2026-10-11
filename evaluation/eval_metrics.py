"""
Υπολογισμός μετρικών αξιολόγησης της εξαγωγής ADR (gold standard vs Gemini).

Μονάδα σύγκρισης: ζεύγος (SmPC, ADR). Για κάθε ζεύγος που βρέθηκε και στα δύο
σύνολα (TP) ελέγχονται επιπλέον η συχνότητα και το SOC.

Είσοδοι (CSV, UTF-8, στήλες product_id, adr_term, frequency, soc):
  --gold    χειροκίνητη επισήμανση (εξαγωγή από το φύλλο Annotations)
  --pred    έξοδος Gemini (adr_per_smpc.csv ή αντίγραφο από επανάληψη)
Προαιρετικά:
  --manual  CSV με στήλες product_id, gold_term, pred_term: χειροκίνητοι
            ταιριασμοί για όρους που διαφέρουν μόνο στη διατύπωση
  --soc-map CSV με στήλες raw, canonical: αντιστοίχιση παραλλαγών SOC
  --out     φάκελος εξόδου (default: eval_output)

Έξοδοι: metrics.json, metrics.md, matches.csv (TP/FP/FN ανά γραμμή, με κενή
στήλη error_category για χειροκίνητη συμπλήρωση).
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

FREQ_VALUES = {"very common", "common", "uncommon", "rare", "very rare", "not known"}


def norm_term(s: str) -> str:
    s = (s or "").lower().strip()
    s = s.replace("&", " and ")
    s = re.sub(r"[^a-z0-9Ͱ-Ͽ ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def norm_freq(s: str) -> str:
    s = re.sub(r"\s+", " ", (s or "").lower().strip()).replace("-", " ")
    return s


def load_soc_map(path: Path | None) -> dict[str, str]:
    if not path:
        return {}
    with path.open(encoding="utf-8", newline="") as f:
        return {norm_term(r["raw"]): norm_term(r["canonical"]) for r in csv.DictReader(f)}


def norm_soc(s: str, soc_map: dict[str, str]) -> str:
    n = norm_term(s)
    return soc_map.get(n, n)


def read_rows(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        rows = []
        for r in csv.DictReader(f):
            pid = str(r.get("product_id", "")).strip()
            term = (r.get("adr_term") or "").strip()
            if not pid or not term or pid.upper() == "EXAMPLE":
                continue
            rows.append({
                "product_id": pid,
                "adr_term": term,
                "frequency": (r.get("frequency") or "").strip(),
                "soc": (r.get("soc") or "").strip(),
                "is_head": str(r.get("is_head") or "0").strip(),
            })
    return rows


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (float("nan"), float("nan"))
    p = k / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, centre - half), min(1.0, centre + half))


def safe_div(a: float, b: float) -> float:
    return a / b if b else float("nan")


def evaluate(gold, pred, manual, soc_map, lenient=False):
    manual_map = defaultdict(dict)  # pid -> {norm gold term: norm pred term}
    for m in manual:
        manual_map[m["product_id"]][norm_term(m["gold_term"])] = norm_term(m["pred_term"])

    pids = sorted({r["product_id"] for r in gold} | {r["product_id"] for r in pred})
    matches = []
    per_product = {}

    for pid in pids:
        g_by = defaultdict(list)
        p_by = defaultdict(list)
        for r in gold:
            if r["product_id"] == pid:
                g_by[norm_term(r["adr_term"])].append(r)
        for r in pred:
            if r["product_id"] == pid:
                p_by[norm_term(r["adr_term"])].append(r)

        # χειροκίνητοι ταιριασμοί: το κλειδί του pred αντιστοιχίζεται στο κλειδί του gold.
        # Με lenient=True ένας σύνθετος όρος του pred (π.χ. «X including Y») ταιριάζει
        # με κάθε όρο του gold που περιέχει. Με lenient=False ταιριάζει μόνο με τον πρώτο.
        orig_p = {k: [dict(r) for r in v] for k, v in p_by.items()}
        consumed = set()
        for gk, pk in manual_map.get(pid, {}).items():
            if gk in g_by and pk in orig_p and gk != pk:
                if pk in consumed and not lenient:
                    continue
                p_by[gk].extend(dict(r) for r in orig_p[pk])
                consumed.add(pk)
        for pk in consumed:
            p_by.pop(pk, None)

        tp = fp = fn = 0
        for key in sorted(set(g_by) | set(p_by)):
            gl, pl = list(g_by.get(key, [])), list(p_by.get(key, []))
            # προτεραιότητα σε ζεύγη με ίδια συχνότητα
            used_p = set()
            paired = []
            for gi, g in enumerate(gl):
                for pi, p in enumerate(pl):
                    if pi not in used_p and norm_freq(g["frequency"]) == norm_freq(p["frequency"]):
                        paired.append((gi, pi)); used_p.add(pi); break
            used_g = {gi for gi, _ in paired}
            for gi, g in enumerate(gl):
                if gi in used_g:
                    continue
                for pi, p in enumerate(pl):
                    if pi not in used_p:
                        paired.append((gi, pi)); used_p.add(pi); used_g.add(gi); break
            used_g = {gi for gi, _ in paired}
            for gi, pi in paired:
                g, p = gl[gi], pl[pi]
                matches.append({
                    "product_id": pid, "status": "TP",
                    "gold_term": g["adr_term"], "pred_term": p["adr_term"],
                    "gold_frequency": g["frequency"], "pred_frequency": p["frequency"],
                    "gold_soc": g["soc"], "pred_soc": p["soc"],
                    "freq_ok": int(norm_freq(g["frequency"]) == norm_freq(p["frequency"])),
                    "soc_ok": int(norm_soc(g["soc"], soc_map) == norm_soc(p["soc"], soc_map)),
                    "error_category": "",
                })
                tp += 1
            for gi, g in enumerate(gl):
                if gi not in used_g:
                    matches.append({"product_id": pid, "status": "FN", "gold_term": g["adr_term"],
                                    "pred_term": "", "gold_frequency": g["frequency"], "pred_frequency": "",
                                    "gold_soc": g["soc"], "pred_soc": "", "freq_ok": "", "soc_ok": "",
                                    "error_category": ""})
                    fn += 1
            for pi, p in enumerate(pl):
                if pi not in used_p:
                    matches.append({"product_id": pid, "status": "FP", "gold_term": "",
                                    "pred_term": p["adr_term"], "gold_frequency": "", "pred_frequency": p["frequency"],
                                    "gold_soc": "", "pred_soc": p["soc"], "freq_ok": "", "soc_ok": "",
                                    "error_category": ""})
                    fp += 1
        per_product[pid] = {"TP": tp, "FP": fp, "FN": fn,
                            "precision": safe_div(tp, tp + fp), "recall": safe_div(tp, tp + fn)}

    TP = sum(v["TP"] for v in per_product.values())
    FP = sum(v["FP"] for v in per_product.values())
    FN = sum(v["FN"] for v in per_product.values())
    P, R = safe_div(TP, TP + FP), safe_div(TP, TP + FN)
    F1 = safe_div(2 * P * R, P + R) if TP else float("nan")
    tps = [m for m in matches if m["status"] == "TP"]
    fa = sum(m["freq_ok"] for m in tps)
    sa = sum(m["soc_ok"] for m in tps)
    both = sum(1 for m in tps if m["freq_ok"] and m["soc_ok"])
    n = len(tps)
    summary = {
        "TP": TP, "FP": FP, "FN": FN,
        "precision": P, "precision_ci95": wilson(TP, TP + FP),
        "recall": R, "recall_ci95": wilson(TP, TP + FN),
        "f1": F1,
        "frequency_accuracy": safe_div(fa, n), "frequency_ci95": wilson(fa, n),
        "soc_accuracy": safe_div(sa, n), "soc_ci95": wilson(sa, n),
        "all_fields_accuracy": safe_div(both, n), "all_fields_ci95": wilson(both, n),
        "n_gold": TP + FN, "n_pred": TP + FP, "n_tp": n,
    }
    return summary, per_product, matches


def pct(x):
    return "n/a" if x != x else f"{100 * x:.1f}%"


def ci(t):
    return "n/a" if t[0] != t[0] else f"[{100 * t[0]:.1f}%, {100 * t[1]:.1f}%]"


def write_report(out: Path, summary, per_product, matches, errors_tally=None):
    out.mkdir(parents=True, exist_ok=True)
    with (out / "matches.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(matches[0].keys()) if matches else ["status"])
        w.writeheader()
        w.writerows(matches)
    (out / "metrics.json").write_text(
        json.dumps({"summary": summary, "per_product": per_product}, indent=2, ensure_ascii=False),
        encoding="utf-8")
    s = summary
    lines = [
        "# Αποτελέσματα αξιολόγησης εξαγωγής ADR",
        "",
        f"Gold: {s['n_gold']} ζεύγη (SmPC, ADR) · Gemini: {s['n_pred']} · TP {s['TP']} · FP {s['FP']} · FN {s['FN']}",
        "",
        "| Μετρική | Τιμή | 95% CI (Wilson) |",
        "|---|---|---|",
        f"| Precision | {pct(s['precision'])} | {ci(s['precision_ci95'])} |",
        f"| Recall | {pct(s['recall'])} | {ci(s['recall_ci95'])} |",
        f"| F1 | {pct(s['f1'])} | |",
        f"| Ορθότητα συχνότητας (στα TP) | {pct(s['frequency_accuracy'])} | {ci(s['frequency_ci95'])} |",
        f"| Ορθότητα SOC (στα TP) | {pct(s['soc_accuracy'])} | {ci(s['soc_ci95'])} |",
        f"| Ορθότητα και των δύο (στα TP) | {pct(s['all_fields_accuracy'])} | {ci(s['all_fields_ci95'])} |",
        "",
        "## Ανά SmPC",
        "",
        "| product_id | TP | FP | FN | Precision | Recall |",
        "|---|---|---|---|---|---|",
    ]
    for pid, v in per_product.items():
        lines.append(f"| {pid} | {v['TP']} | {v['FP']} | {v['FN']} | {pct(v['precision'])} | {pct(v['recall'])} |")
    if errors_tally:
        lines += ["", "## Κατηγορίες σφαλμάτων", "", "| Κατηγορία | Πλήθος |", "|---|---|"]
        lines += [f"| {k} | {v} |" for k, v in errors_tally.most_common()]
    (out / "metrics.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def tally_errors(path: Path) -> Counter:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return Counter(r["error_category"].strip() for r in csv.DictReader(f)
                       if r.get("status") in ("FP", "FN") and r.get("error_category", "").strip())


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--gold", required=True, type=Path)
    ap.add_argument("--pred", required=True, type=Path)
    ap.add_argument("--manual", type=Path)
    ap.add_argument("--soc-map", type=Path)
    ap.add_argument("--errors", type=Path, help="συμπληρωμένο matches.csv με error_category")
    ap.add_argument("--lenient", action="store_true", help="σύνθετος όρος του pred ταιριάζει με όλους τους όρους του gold που περιέχει")
    ap.add_argument("--exclude-heads", action="store_true", help="αφαίρεση γραμμών gold με is_head=1")
    ap.add_argument("--out", type=Path, default=Path("eval_output"))
    a = ap.parse_args()

    gold, pred = read_rows(a.gold), read_rows(a.pred)
    if a.exclude_heads:
        gold = [r for r in gold if r.get("is_head") != "1"]
    pids = {r["product_id"] for r in gold}
    pred = [r for r in pred if r["product_id"] in pids]  # μόνο τα SmPC που επισημάνθηκαν
    manual = []
    if a.manual:
        with a.manual.open(encoding="utf-8-sig", newline="") as f:
            manual = list(csv.DictReader(f))
    summary, per_product, matches = evaluate(gold, pred, manual, load_soc_map(a.soc_map), a.lenient)
    tally = tally_errors(a.errors) if a.errors else None
    write_report(a.out, summary, per_product, matches, tally)
    print((a.out / "metrics.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
