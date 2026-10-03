"""
Phase 2 - Step 2: Στοχευμένο scraping SmPC HTML αρχείων.

Το script:
Φορτώνει έγκυρα IDs από valid_ids.json
Κατεβάζει το πλήρες HTML από το /smpc/print endpoint
Υποστηρίζει resume (skip αν το αρχείο υπάρχει ήδη)
Εφαρμόζει αυστηρό delay 6s μόνο όταν γίνεται πραγματικό HTTP request
Κάνει pre-flight checks (δίσκος, δίκτυο, robots)
Γράφει αναλυτικό log για thesis-ready στατιστικά
Παράγει failed IDs και dataset manifest στο τέλος
"""

from __future__ import annotations

import csv
import json
import shutil
import time
from argparse import ArgumentParser
from datetime import datetime
from pathlib import Path
from typing import Optional

import requests

# Πόσα IDs θα επεξεργαστούμε για δοκιμή (βάλε None για full crawl)
LIMIT: Optional[int] = 500

# Υποχρεωτικό delay σε δευτερόλεπτα
REQUEST_DELAY = 6
MAX_RETRIES = 3
BACKOFF_BASE_SECONDS = 2
MIN_FREE_SPACE_MB = 500

BASE_URL = "https://www.medicines.org.uk/emc/product/{product_id}/smpc/print"
ROBOTS_URL = "https://www.medicines.org.uk/robots.txt"
VALID_IDS_FILE = Path("data/output/valid_ids.json")
OUTPUT_DIR = Path("data/raw")
LOG_FILE = Path("data/output/scraping_log.csv")
FAILED_RETRY_IDS_FILE = Path("data/output/failed_ids_retry.txt")
FAILED_404_IDS_FILE = Path("data/output/failed_ids_404.txt")
MANIFEST_FILE = Path("data/output/dataset_manifest.csv")
RUN_METADATA_FILE = Path("data/output/run_metadata.json")
LOG_HEADERS = [
    "timestamp",
    "product_id",
    "url",
    "status",
    "http_status",
    "error_type",
    "attempt_count",
    "duration_ms",
    "output_file",
]
LEGACY_LOG_HEADERS = ["Product_ID", "Status", "Timestamp"]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    )
}


def load_ids(json_path: Path) -> list[int]:
    """
    Φορτώνει IDs από valid_ids.json.
    Υποστηρίζει είτε μορφή {"ids": [...]} είτε απλή λίστα [...].
    """
    if not json_path.exists():
        raise FileNotFoundError(f"Δεν βρέθηκε το αρχείο: {json_path}")

    with json_path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    if isinstance(data, dict):
        ids = data.get("ids", [])
    elif isinstance(data, list):
        ids = data
    else:
        raise ValueError("Μη έγκυρη δομή JSON στο valid_ids.json")

    clean_ids: list[int] = []
    for value in ids:
        try:
            clean_ids.append(int(value))
        except (TypeError, ValueError):
            # Αγνοώ τυχόν μη έγκυρες τιμές
            continue

    return clean_ids


def init_log_file(log_path: Path) -> None:
    """Δημιουργεί/μετατρέπει CSV log στο νέο σχήμα."""
    if not log_path.exists():
        with log_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(LOG_HEADERS)
        return

    with log_path.open("r", newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        headers = next(reader, [])

    if headers == LOG_HEADERS:
        return

    if headers != LEGACY_LOG_HEADERS:
        raise RuntimeError(
            f"Μη αναμενόμενα headers στο {log_path.name}: {headers}. "
            "Κάνε backup και ξεκίνα νέο log."
        )

    # Μετατροπή legacy log ώστε να συνεχίσω με ενιαίο schema.
    with log_path.open("r", newline="", encoding="utf-8") as f:
        legacy_rows = list(csv.DictReader(f))

    converted_rows = []
    for row in legacy_rows:
        legacy_status = (row.get("Status") or "").strip()
        status_map = {"Success": "SUCCESS", "404": "HTTP_404", "Error": "HTTP_ERROR"}
        converted_rows.append(
            {
                "timestamp": row.get("Timestamp", ""),
                "product_id": row.get("Product_ID", ""),
                "url": "",
                "status": status_map.get(legacy_status, legacy_status.upper()),
                "http_status": "404" if legacy_status == "404" else "",
                "error_type": "",
                "attempt_count": "",
                "duration_ms": "",
                "output_file": "",
            }
        )

    with log_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=LOG_HEADERS)
        writer.writeheader()
        writer.writerows(converted_rows)


def append_log(
    *,
    product_id: int,
    url: str,
    status: str,
    http_status: str,
    error_type: str,
    attempt_count: int,
    duration_ms: int,
    output_file: str,
    log_path: Path,
) -> None:
    """Προσθέτει μία εγγραφή στο scraping log CSV."""
    timestamp = datetime.now().isoformat(timespec="seconds")
    with log_path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                timestamp,
                product_id,
                url,
                status,
                http_status,
                error_type,
                attempt_count,
                duration_ms,
                output_file,
            ]
        )


def parse_args() -> tuple[Optional[int], int]:
    """Επιτρέπει full crawl ή custom limit από CLI."""
    parser = ArgumentParser(description="SmPC HTML scraper with resume and retry support.")
    parser.add_argument(
        "--limit",
        type=int,
        default=LIMIT,
        help="Πλήθος IDs προς επεξεργασία (None για full crawl).",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Αγνοεί το limit και επεξεργάζεται όλα τα IDs.",
    )
    parser.add_argument(
        "--delay",
        type=int,
        default=REQUEST_DELAY,
        help="Delay σε δευτερόλεπτα μεταξύ πραγματικών requests.",
    )
    args = parser.parse_args()

    if args.delay < 0:
        raise ValueError("Το --delay πρέπει να είναι >= 0")
    if args.limit is not None and args.limit < 1:
        raise ValueError("Το --limit πρέπει να είναι >= 1 ή να παραλειφθεί")

    resolved_limit: Optional[int] = None if args.all else args.limit
    return resolved_limit, args.delay


def preflight_checks(session: requests.Session, ids_count: int, first_id: int) -> None:
    """Βασικοί έλεγχοι πριν το long run."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    if ids_count == 0:
        raise RuntimeError("Δεν βρέθηκαν IDs προς επεξεργασία.")

    usage = shutil.disk_usage(OUTPUT_DIR.resolve())
    free_mb = usage.free // (1024 * 1024)
    print(f"[Pre-flight] Free disk space: {free_mb} MB")
    if free_mb < MIN_FREE_SPACE_MB:
        raise RuntimeError(
            f"Χαμηλός ελεύθερος χώρος ({free_mb} MB). "
            f"Απαιτούνται τουλάχιστον {MIN_FREE_SPACE_MB} MB."
        )

    try:
        robots_resp = session.get(ROBOTS_URL, headers=HEADERS, timeout=20)
        if robots_resp.status_code == 200:
            print("[Pre-flight] robots.txt fetched successfully.")
            if "/emc/" in robots_resp.text:
                print("[Pre-flight] robots.txt contains /emc/ entries. Review before full crawl.")
        else:
            print(f"[Pre-flight] robots.txt returned HTTP {robots_resp.status_code}.")
    except requests.RequestException as exc:
        print(f"[Pre-flight] robots.txt check failed (non-blocking): {exc}")

    test_url = BASE_URL.format(product_id=first_id)
    try:
        session.get(test_url, headers=HEADERS, timeout=20)
        print("[Pre-flight] Network check passed.")
    except requests.RequestException as exc:
        raise RuntimeError(f"Αποτυχία δικτύου στο pre-flight check: {exc}") from exc


def request_with_retries(
    session: requests.Session, url: str
) -> tuple[Optional[requests.Response], int, int, str]:
    """
    Εκτελεί request με retries/backoff για 429 και 5xx.
    Επιστρέφει response, attempts, duration_ms, error_type.
    """
    attempt = 0
    started = time.perf_counter()
    last_error = ""
    retry_codes = {429, 500, 502, 503, 504}

    while attempt < MAX_RETRIES:
        attempt += 1
        try:
            response = session.get(url, headers=HEADERS, timeout=30)
            if response.status_code in retry_codes and attempt < MAX_RETRIES:
                sleep_s = BACKOFF_BASE_SECONDS ** attempt
                print(
                    f"[Retry] HTTP {response.status_code} (attempt {attempt}/{MAX_RETRIES}), "
                    f"sleeping {sleep_s}s."
                )
                time.sleep(sleep_s)
                continue

            duration_ms = int((time.perf_counter() - started) * 1000)
            return response, attempt, duration_ms, ""
        except requests.RequestException as exc:
            last_error = type(exc).__name__
            if attempt < MAX_RETRIES:
                sleep_s = BACKOFF_BASE_SECONDS ** attempt
                print(
                    f"[Retry] {last_error} (attempt {attempt}/{MAX_RETRIES}), "
                    f"sleeping {sleep_s}s."
                )
                time.sleep(sleep_s)
                continue

    duration_ms = int((time.perf_counter() - started) * 1000)
    return None, attempt, duration_ms, last_error or "RequestException"


def write_failed_ids(path: Path, ids: list[int]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for item in sorted(set(ids)):
            f.write(f"{item}\n")


def build_manifest(output_dir: Path, manifest_path: Path) -> int:
    files = sorted(output_dir.glob("smpc_*.html"))
    with manifest_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["product_id", "file_name", "file_size_bytes", "modified_at", "source_url"])
        for file_path in files:
            product_id = file_path.stem.replace("smpc_", "", 1)
            stat = file_path.stat()
            writer.writerow(
                [
                    product_id,
                    file_path.name,
                    stat.st_size,
                    datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
                    BASE_URL.format(product_id=product_id),
                ]
            )
    return len(files)


def write_run_metadata(payload: dict) -> None:
    RUN_METADATA_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def scrape_htmls(limit: Optional[int], request_delay: int) -> None:
    """Κύρια ρουτίνα scraping με resume capability και strict delay."""
    all_ids = load_ids(VALID_IDS_FILE)
    ids = all_ids if limit is None else all_ids[:limit]
    init_log_file(LOG_FILE)

    existing_files = len(list(OUTPUT_DIR.glob("smpc_*.html")))

    print("=== HTML SmPC Scraper (Phase 2 - Step 2) ===")
    print(f"IDs loaded: {len(ids)} (limit={limit})")
    print(f"Already downloaded files: {existing_files}")
    print(f"Output dir: {OUTPUT_DIR.resolve()}\n")

    session = requests.Session()
    preflight_checks(session, len(ids), ids[0])

    success_count = 0
    not_found_count = 0
    skipped_count = 0
    transient_fail_count = 0
    http_error_count = 0
    failed_retry_ids: list[int] = []
    failed_404_ids: list[int] = []
    run_started = datetime.now().isoformat(timespec="seconds")

    for idx, product_id in enumerate(ids, start=1):
        output_file = OUTPUT_DIR / f"smpc_{product_id}.html"
        url = BASE_URL.format(product_id=product_id)

        # Resume capability: αν υπάρχει ήδη, skip χωρίς delay
        if output_file.exists():
            print(f"[{idx}/{len(ids)}] ID {product_id}: Already exists, skipping...")
            append_log(
                product_id=product_id,
                url=url,
                status="SKIPPED_EXISTS",
                http_status="",
                error_type="",
                attempt_count=0,
                duration_ms=0,
                output_file=str(output_file),
                log_path=LOG_FILE,
            )
            skipped_count += 1
            continue

        did_request = False

        print(f"[{idx}/{len(ids)}] ID {product_id}: Requesting {url}")
        did_request = True
        response, attempts, duration_ms, error_type = request_with_retries(session, url)

        if response is not None:
            if response.status_code == 200:
                output_file.write_text(response.text, encoding="utf-8")
                append_log(
                    product_id=product_id,
                    url=url,
                    status="SUCCESS",
                    http_status="200",
                    error_type="",
                    attempt_count=attempts,
                    duration_ms=duration_ms,
                    output_file=str(output_file),
                    log_path=LOG_FILE,
                )
                success_count += 1
                print(f"[✓] Saved HTML -> {output_file}")
            elif response.status_code == 404:
                append_log(
                    product_id=product_id,
                    url=url,
                    status="HTTP_404",
                    http_status="404",
                    error_type="",
                    attempt_count=attempts,
                    duration_ms=duration_ms,
                    output_file="",
                    log_path=LOG_FILE,
                )
                not_found_count += 1
                failed_404_ids.append(product_id)
                print(f"[!] ID {product_id}: HTTP 404")
            else:
                append_log(
                    product_id=product_id,
                    url=url,
                    status="HTTP_ERROR",
                    http_status=str(response.status_code),
                    error_type="",
                    attempt_count=attempts,
                    duration_ms=duration_ms,
                    output_file="",
                    log_path=LOG_FILE,
                )
                http_error_count += 1
                failed_retry_ids.append(product_id)
                print(f"[!] ID {product_id}: HTTP {response.status_code}")
        else:
            append_log(
                product_id=product_id,
                url=url,
                status="REQUEST_ERROR",
                http_status="",
                error_type=error_type,
                attempt_count=attempts,
                duration_ms=duration_ms,
                output_file="",
                log_path=LOG_FILE,
            )
            transient_fail_count += 1
            failed_retry_ids.append(product_id)
            print(f"[!] ID {product_id}: Request failed after {attempts} attempts ({error_type})")

        # Αυστηρό delay μόνο όταν έγινε πραγματικό request (όχι στα skips)
        if did_request:
            print(f"[~] Sleeping {request_delay}s...\n")
            time.sleep(request_delay)

    write_failed_ids(FAILED_RETRY_IDS_FILE, failed_retry_ids)
    write_failed_ids(FAILED_404_IDS_FILE, failed_404_ids)
    manifest_count = build_manifest(OUTPUT_DIR, MANIFEST_FILE)

    run_ended = datetime.now().isoformat(timespec="seconds")
    metadata = {
        "started_at": run_started,
        "ended_at": run_ended,
        "limit": limit,
        "request_delay_seconds": request_delay,
        "max_retries": MAX_RETRIES,
        "backoff_base_seconds": BACKOFF_BASE_SECONDS,
        "total_ids_loaded": len(ids),
        "results": {
            "success": success_count,
            "http_404": not_found_count,
            "http_error": http_error_count,
            "request_error": transient_fail_count,
            "skipped_exists": skipped_count,
        },
        "output_dir": str(OUTPUT_DIR.resolve()),
        "log_file": str(LOG_FILE.resolve()),
        "failed_retry_ids_file": str(FAILED_RETRY_IDS_FILE.resolve()),
        "failed_404_ids_file": str(FAILED_404_IDS_FILE.resolve()),
        "manifest_file": str(MANIFEST_FILE.resolve()),
        "manifest_rows": manifest_count,
    }
    write_run_metadata(metadata)

    print("=== Done ===")
    print(json.dumps(metadata, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    selected_limit, selected_delay = parse_args()
    scrape_htmls(selected_limit, selected_delay)
