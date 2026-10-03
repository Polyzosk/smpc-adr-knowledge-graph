"""
Βήμα enrichment για ATC: εξαγωγή δραστικής ουσίας (INN).

Διαβάζει το drug_per_smpc.csv και στέλνει κάθε brand_name στο Gemini
για να εξάγει την καθαρή δραστική ουσία / INN.

Αρχεία εξόδου:
- drug_per_smpc_substances.csv
- gemini_substance_extraction_log.csv
"""

from __future__ import annotations

import csv
import json
import os
import re
import sys
import time
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from dotenv import load_dotenv
import requests


load_dotenv()

API_KEY = os.getenv("GEMINI_API_KEY", "")
MODEL_NAME = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")
FALLBACK_MODELS = [
    "gemini-2.0-flash",
    "gemini-1.5-flash",
    "gemini-1.5-flash-8b",
]

REQUEST_DELAY = 2
REQUEST_TIMEOUT_SECONDS = 60
MAX_RETRIES = 3
MAX_CONSECUTIVE_QUOTA_ERRORS = 3

DRUG_INPUT = Path("data/output/drug_per_smpc.csv")
DRUG_OUTPUT = Path("data/output/drug_per_smpc_substances.csv")
LOG_OUTPUT = Path("data/output/gemini_substance_extraction_log.csv")


PROMPT_TEMPLATE = """
You are a pharmacology expert. Below is the brand/product name of a medicine
from a UK SmPC (Summary of Product Characteristics).

Extract the active substance(s) using International Nonproprietary Names (INN)
where possible. Ignore dosage, pharmaceutical form, and marketing names.

Rules:
- Return ONLY the active ingredient name(s), lowercase INN style.
- For combination products, separate ingredients with " + " (e.g. "oxycodone + naloxone").
- If the brand name already contains the INN (e.g. "Labetalol 200 mg tablets"), return "labetalol".
- If you cannot determine the active substance, return an empty string.

Return ONLY valid JSON in this exact format, no extra text:
{{
  "active_substance": "..."
}}

Brand/product name:
{brand_name}
"""


def load_drug_rows(path: Path) -> list[dict]:
    if not path.exists():
        raise FileNotFoundError(f"Δεν βρέθηκε: {path}")
    with path.open("r", encoding="utf-8", newline="") as f:
        return [row for row in csv.DictReader(f) if row.get("status") == "Success"]


def load_existing_output(path: Path) -> dict[str, str]:
    """Επιστρέφει map product_id -> active_substance για resume."""
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8", newline="") as f:
        return {
            row["product_id"]: row.get("active_substance", "").strip()
            for row in csv.DictReader(f)
            if row.get("product_id") and row.get("active_substance", "").strip()
        }


def parse_gemini_response(response_text: str) -> str:
    clean = re.sub(r"```(?:json)?", "", response_text).replace("```", "").strip()
    data = json.loads(clean)
    value = (data.get("active_substance") or "").strip().lower()
    value = re.sub(r"\s+", " ", value)
    return value


def model_candidates() -> list[str]:
    """Επιστρέφει λίστα μοντέλων με fallback αν το .env έχει unsupported model."""
    models: list[str] = []
    for model in [MODEL_NAME, *FALLBACK_MODELS]:
        if model and model not in models:
            models.append(model)
    return models


def request_gemini_active_substance(brand_name: str) -> tuple[str, str, bool]:
    """Επιστρέφει (active_substance, error_msg, quota_exceeded)."""
    prompt = PROMPT_TEMPLATE.format(brand_name=brand_name)
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.0},
    }

    last_error = "Unknown error"
    for model_name in model_candidates():
        endpoint = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model_name}:generateContent?key={API_KEY}"
        )

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                response = requests.post(
                    endpoint,
                    headers={"Content-Type": "application/json"},
                    json=payload,
                    timeout=REQUEST_TIMEOUT_SECONDS,
                )
                if response.status_code == 404:
                    last_error = f"Model not found: {model_name}"
                    break
                if response.status_code == 429:
                    last_error = f"Quota exceeded ({model_name})"
                    return "", last_error, True
                if response.status_code != 200:
                    raise RuntimeError(f"HTTP {response.status_code}: {response.text[:300]}")

                raw = response.json()
                text = raw["candidates"][0]["content"]["parts"][0]["text"]
                substance = parse_gemini_response(text)
                if not substance:
                    return "", "empty_result", False
                return substance, "", False
            except json.JSONDecodeError as exc:
                last_error = f"JSON parse error ({model_name}): {exc}"
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc} ({model_name}, attempt {attempt}/{MAX_RETRIES})"

            if attempt < MAX_RETRIES:
                sleep_s = min(8 * attempt, 20)
                print(f"[Retry] {last_error}. Sleeping {sleep_s}s...", flush=True)
                time.sleep(sleep_s)

    return "", last_error, False


def init_outputs() -> None:
    with DRUG_OUTPUT.open("w", encoding="utf-8", newline="") as f:
        csv.writer(f).writerow(
            ["product_id", "brand_name", "status", "active_substance"]
        )
    with LOG_OUTPUT.open("w", encoding="utf-8", newline="") as f:
        csv.writer(f).writerow(
            ["product_id", "brand_name", "active_substance", "status", "error"]
        )


def append_result(row: dict, log_row: dict) -> None:
    with DRUG_OUTPUT.open("a", encoding="utf-8", newline="") as f:
        csv.DictWriter(
            f,
            fieldnames=["product_id", "brand_name", "status", "active_substance"],
        ).writerow(row)
    with LOG_OUTPUT.open("a", encoding="utf-8", newline="") as f:
        csv.DictWriter(
            f,
            fieldnames=["product_id", "brand_name", "active_substance", "status", "error"],
        ).writerow(log_row)


def main() -> None:
    if not API_KEY:
        raise RuntimeError("GEMINI_API_KEY δεν βρέθηκε στο .env")

    rows = load_drug_rows(DRUG_INPUT)
    existing = load_existing_output(DRUG_OUTPUT)

    if existing:
        print(f"Resume mode: {len(existing)} rows already extracted, skipping those.")
        init_outputs()
        for row in rows:
            pid = row["product_id"].strip()
            if pid in existing:
                append_result(
                    {
                        "product_id": pid,
                        "brand_name": row["brand_name"],
                        "status": row["status"],
                        "active_substance": existing[pid],
                    },
                    {
                        "product_id": pid,
                        "brand_name": row["brand_name"],
                        "active_substance": existing[pid],
                        "status": "Skipped",
                        "error": "",
                    },
                )
    else:
        init_outputs()

    # Cache ανα brand_name για να μην καλούμε Gemini δύο φορές το ίδιο όνομα.
    brand_cache: dict[str, tuple[str, str, bool]] = {}

    print("=== Gemini Active Substance Extraction ===")
    print(f"Model: {MODEL_NAME} | Drugs: {len(rows)} | Delay: {REQUEST_DELAY}s\n")

    success_count = 0
    quota_errors = 0
    for idx, row in enumerate(rows, start=1):
        product_id = row["product_id"].strip()
        brand_name = row["brand_name"].strip()

        if product_id in existing:
            success_count += 1
            continue

        if brand_name in brand_cache:
            substance, error_msg, quota_hit = brand_cache[brand_name]
            status = "Success" if substance else "Failed"
            print(
                f"[{idx}/{len(rows)}] Product {product_id}: cached -> {substance or 'FAILED'}",
                flush=True,
            )
        else:
            print(
                f"[{idx}/{len(rows)}] Product {product_id}: requesting Gemini...",
                flush=True,
            )
            substance, error_msg, quota_hit = request_gemini_active_substance(brand_name)
            brand_cache[brand_name] = (substance, error_msg, quota_hit)
            status = "Success" if substance else "Failed"
            print(
                f"[{idx}/{len(rows)}] Product {product_id}: {substance or 'FAILED'}",
                flush=True,
            )
            if quota_hit:
                quota_errors += 1
                if quota_errors >= MAX_CONSECUTIVE_QUOTA_ERRORS:
                    print(
                        "\n[STOP] Gemini quota exceeded. "
                        "Check billing/rate limits and rerun later. "
                        "Partial results were saved.",
                        flush=True,
                    )
                    break
            elif idx < len(rows):
                time.sleep(REQUEST_DELAY)

        if substance:
            success_count += 1

        append_result(
            {
                "product_id": product_id,
                "brand_name": brand_name,
                "status": row["status"],
                "active_substance": substance,
            },
            {
                "product_id": product_id,
                "brand_name": brand_name,
                "active_substance": substance,
                "status": status,
                "error": error_msg,
            },
        )

    print("\n=== Done ===")
    print(f"Active substances extracted: {success_count}/{len(rows)}")
    print(f"Output: {DRUG_OUTPUT.resolve()}")
    print(f"Log: {LOG_OUTPUT.resolve()}")


if __name__ == "__main__":
    main()
