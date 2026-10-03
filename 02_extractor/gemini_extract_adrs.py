"""
Βημα 3 για το πιλοτικο σετ.

Για καθε product_id στο pilot_selection.csv
διαβαζει το json απο τον φακελο extracted_4_8_blocks.
Στελνει το content_html στο μοντελο.
Παιρνει λιστα με ADRs και την γραφει σε csv.

Αρχεια εξοδου
adr_per_smpc.csv
gemini_extraction_log.csv
"""

from __future__ import annotations

import csv
import json
import os
import re
import sys
import time
from pathlib import Path

# Ρυθμιση για σωστα ελληνικα στο terminal
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from bs4 import BeautifulSoup
from dotenv import load_dotenv
import requests


load_dotenv()

API_KEY = os.getenv("GEMINI_API_KEY", "")
MODEL_NAME = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")

# Καθυστερηση αναμεσα στα requests
REQUEST_DELAY = 4
REQUEST_TIMEOUT_SECONDS = 90
MAX_RETRIES = 3

PILOT_FILE = Path("data/output/pilot_selection.csv")
BLOCKS_DIR = Path("data/output/extracted_4_8_blocks")
ADR_OUTPUT = Path("data/output/adr_per_smpc.csv")
LOG_OUTPUT = Path("data/output/gemini_extraction_log.csv")


PROMPT_TEMPLATE = """
You are a pharmacovigilance expert. Below is the HTML content of Section 4.8 
("Undesirable effects" or "Adverse reactions") from a Summary of Product 
Characteristics (SmPC).

Extract ALL adverse drug reactions (ADRs) mentioned. For each ADR return:
- "adr_term": the exact ADR name as written
- "frequency": one of [very common, common, uncommon, rare, very rare, not known]
- "soc": the MedDRA System Organ Class (SOC) it belongs to (if stated)

Return ONLY a valid JSON object in this exact format, no extra text:
{{
  "adrs": [
    {{"adr_term": "...", "frequency": "...", "soc": "..."}},
    ...
  ]
}}

If no ADRs are found, return: {{"adrs": []}}

HTML content:
{html_content}
"""


def load_pilot_ids(path: Path) -> list[str]:
    if not path.exists():
        raise FileNotFoundError(f"Δεν βρέθηκε: {path}")
    with path.open("r", encoding="utf-8", newline="") as f:
        return [row["product_id"].strip() for row in csv.DictReader(f)]


def html_to_clean_text(html: str) -> str:
    """Μετατρεπει html σε πιο καθαρο κειμενο."""
    soup = BeautifulSoup(html, "html.parser")
    # Κραταμε τη σειρα του πινακα
    for br in soup.find_all("br"):
        br.replace_with("\n")
    for td in soup.find_all(["td", "th"]):
        td.append(" | ")
    for tr in soup.find_all("tr"):
        tr.append("\n")
    text = soup.get_text(" ", strip=False)
    # Καθαρισμος πολλων κενων και αλλαγων γραμμης
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()[:8000]  # Κραταμε λογικο μηκος κειμενου


def parse_gemini_response(response_text: str) -> list[dict]:
    """Βγαζει json ακομα και αν υπαρχουν code fences."""
    # Αφαιρεση markdown fences
    clean = re.sub(r"```(?:json)?", "", response_text).replace("```", "").strip()
    data = json.loads(clean)
    return data.get("adrs", [])


def init_csv_outputs() -> None:
    with ADR_OUTPUT.open("w", encoding="utf-8", newline="") as f:
        csv.writer(f).writerow(["product_id", "adr_term", "frequency", "soc"])
    with LOG_OUTPUT.open("w", encoding="utf-8", newline="") as f:
        csv.writer(f).writerow(["product_id", "status", "adr_count", "error"])


def append_adrs(product_id: str, adrs: list[dict]) -> None:
    with ADR_OUTPUT.open("a", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        for adr in adrs:
            writer.writerow([
                product_id,
                (adr.get("adr_term") or "").strip(),
                (adr.get("frequency") or "").strip().lower(),
                (adr.get("soc") or "").strip(),
            ])


def append_log(product_id: str, status: str, adr_count: int, error: str) -> None:
    with LOG_OUTPUT.open("a", encoding="utf-8", newline="") as f:
        csv.writer(f).writerow([product_id, status, adr_count, error])


def request_gemini_with_retry(prompt: str) -> list[dict]:
    endpoint = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{MODEL_NAME}:generateContent?key={API_KEY}"
    )
    headers = {"Content-Type": "application/json"}
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.0},
    }

    last_error = "Unknown error"
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = requests.post(
                endpoint,
                headers=headers,
                json=payload,
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
            if response.status_code != 200:
                raise RuntimeError(f"HTTP {response.status_code}: {response.text}")

            raw = response.json()
            text = raw["candidates"][0]["content"]["parts"][0]["text"]
            return parse_gemini_response(text)
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc} (attempt {attempt}/{MAX_RETRIES})"

        if attempt < MAX_RETRIES:
            sleep_s = min(10 * attempt, 30)
            print(f"[Retry] {last_error}. Sleeping {sleep_s}s before retry...", flush=True)
            time.sleep(sleep_s)

    raise RuntimeError(last_error)


def main() -> None:
    if not API_KEY:
        raise RuntimeError("GEMINI_API_KEY δεν βρέθηκε στο .env")

    ids = load_pilot_ids(PILOT_FILE)
    init_csv_outputs()

    print(f"=== Gemini ADR Extraction ===")
    print(f"Model: {MODEL_NAME} | IDs: {len(ids)} | Delay: {REQUEST_DELAY}s\n")

    total_adrs = 0

    for idx, product_id in enumerate(ids, start=1):
        block_path = BLOCKS_DIR / f"{product_id}_4_8_block.json"
        status = "Failed"
        adr_count = 0
        error_msg = ""

        try:
            if not block_path.exists():
                raise FileNotFoundError(f"Block JSON δεν βρέθηκε: {block_path}")

            block_data = json.loads(block_path.read_text(encoding="utf-8"))
            html_content = block_data.get("content_html", "")
            if not html_content:
                raise ValueError("Κενό content_html")

            clean_text = html_to_clean_text(html_content)
            prompt = PROMPT_TEMPLATE.format(html_content=clean_text)

            print(f"[{idx}/{len(ids)}] Product {product_id}: requesting Gemini...", flush=True)
            adrs = request_gemini_with_retry(prompt)

            append_adrs(product_id, adrs)
            adr_count = len(adrs)
            total_adrs += adr_count
            status = "Success"
            print(f"[{idx}/{len(ids)}] Product {product_id}: {adr_count} ADRs extracted.", flush=True)

        except json.JSONDecodeError as exc:
            error_msg = f"JSON parse error: {exc}"
            print(f"[{idx}/{len(ids)}] Product {product_id}: FAILED ({error_msg})", flush=True)
        except Exception as exc:
            error_msg = f"{type(exc).__name__}: {exc}"
            print(f"[{idx}/{len(ids)}] Product {product_id}: FAILED ({error_msg})", flush=True)

        append_log(product_id, status, adr_count, error_msg)

        # Μικρη αναμονη αναμεσα στα requests
        if idx < len(ids):
            time.sleep(REQUEST_DELAY)

    print(f"\n=== Done ===")
    print(f"Total ADRs extracted: {total_adrs}")
    print(f"ADR output: {ADR_OUTPUT.resolve()}")
    print(f"Log: {LOG_OUTPUT.resolve()}")


if __name__ == "__main__":
    main()
