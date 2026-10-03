"""
Υπολογισμός στατιστικών από το scraping_log.csv.

Παράγει:
- Συνοπτικό report στην οθόνη
- scraping_summary.json για εύκολη ενσωμάτωση στην πτυχιακή
"""

from __future__ import annotations

import csv
import json
from argparse import ArgumentParser
from collections import Counter
from pathlib import Path


DEFAULT_LOG_FILE = Path("data/output/scraping_log.csv")
DEFAULT_SUMMARY_FILE = Path("data/output/scraping_summary.json")


def parse_args() -> tuple[Path, Path]:
    parser = ArgumentParser(description="Compute descriptive stats from scraping_log.csv")
    parser.add_argument("--log", type=Path, default=DEFAULT_LOG_FILE, help="Path προς scraping log CSV")
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_SUMMARY_FILE,
        help="Path για JSON output με συνοπτικά στατιστικά",
    )
    args = parser.parse_args()
    return args.log, args.out


def load_rows(path: Path) -> list[dict]:
    if not path.exists():
        raise FileNotFoundError(f"Δεν βρέθηκε log file: {path}")
    with path.open("r", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))

    if not rows:
        return []

    # Legacy schema compatibility: Product_ID, Status, Timestamp
    first_row = rows[0]
    if "status" in first_row:
        return rows

    converted = []
    for row in rows:
        legacy_status = (row.get("Status") or "").strip()
        status_map = {"Success": "SUCCESS", "404": "HTTP_404", "Error": "HTTP_ERROR"}
        converted.append(
            {
                "timestamp": row.get("Timestamp", ""),
                "product_id": row.get("Product_ID", ""),
                "url": "",
                "status": status_map.get(legacy_status, legacy_status.upper()),
                "http_status": "404" if legacy_status == "404" else "",
                "error_type": "",
                "attempt_count": "0",
                "duration_ms": "0",
                "output_file": "",
            }
        )
    return converted


def safe_int(value: str, fallback: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def build_summary(rows: list[dict]) -> dict:
    total = len(rows)
    status_counter = Counter(row.get("status", "") for row in rows)
    http_counter = Counter(row.get("http_status", "") for row in rows if row.get("http_status"))
    error_counter = Counter(row.get("error_type", "") for row in rows if row.get("error_type"))
    attempts_counter = Counter(safe_int(row.get("attempt_count", "0")) for row in rows)

    durations = [safe_int(row.get("duration_ms", "0")) for row in rows if row.get("duration_ms")]
    avg_duration = round(sum(durations) / len(durations), 2) if durations else 0
    max_duration = max(durations) if durations else 0

    success = status_counter.get("SUCCESS", 0)
    skipped = status_counter.get("SKIPPED_EXISTS", 0)
    errors = (
        status_counter.get("HTTP_ERROR", 0)
        + status_counter.get("REQUEST_ERROR", 0)
        + status_counter.get("HTTP_404", 0)
    )
    attempted = total - skipped
    success_rate_attempted = round((success / attempted) * 100, 2) if attempted else 0
    error_rate_attempted = round((errors / attempted) * 100, 2) if attempted else 0

    return {
        "total_rows": total,
        "attempted_requests": attempted,
        "status_counts": dict(status_counter),
        "http_status_counts": dict(http_counter),
        "error_type_counts": dict(error_counter),
        "attempt_count_distribution": dict(sorted(attempts_counter.items())),
        "success_rate_attempted_percent": success_rate_attempted,
        "error_rate_attempted_percent": error_rate_attempted,
        "avg_duration_ms": avg_duration,
        "max_duration_ms": max_duration,
    }


def print_report(summary: dict) -> None:
    print("=== Scraping Log Stats ===")
    print(f"Total log rows: {summary['total_rows']}")
    print(f"Attempted requests: {summary['attempted_requests']}")
    print(
        f"Success rate (attempted): {summary['success_rate_attempted_percent']}% | "
        f"Error rate: {summary['error_rate_attempted_percent']}%"
    )
    print(f"Avg duration: {summary['avg_duration_ms']} ms | Max duration: {summary['max_duration_ms']} ms")

    print("\nStatus counts:")
    for key, value in sorted(summary["status_counts"].items()):
        print(f"  - {key}: {value}")

    if summary["http_status_counts"]:
        print("\nHTTP status counts:")
        for key, value in sorted(summary["http_status_counts"].items()):
            print(f"  - {key}: {value}")

    if summary["error_type_counts"]:
        print("\nError type counts:")
        for key, value in sorted(summary["error_type_counts"].items()):
            print(f"  - {key}: {value}")


def main() -> None:
    log_path, output_path = parse_args()
    rows = load_rows(log_path)
    summary = build_summary(rows)
    print_report(summary)
    output_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nSaved summary JSON -> {output_path.resolve()}")


if __name__ == "__main__":
    main()
