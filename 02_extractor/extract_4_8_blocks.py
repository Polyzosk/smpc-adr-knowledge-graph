"""
Phase 3 - Block Extraction:
Εξαγωγή της ενότητας "4.8 Undesirable effects / Adverse reactions"
από τοπικά SmPC HTML αρχεία.

Input:
dataset_manifest.csv

Output:
extracted_4_8_blocks/{product_id}_4_8_block.json
extraction_4_8_log.csv
"""

from __future__ import annotations

import csv
import json
import re
from argparse import ArgumentParser
from pathlib import Path
from typing import Optional

from bs4 import BeautifulSoup, Tag


LIMIT = 500  # Initial trial batch

MANIFEST_FILE = Path("data/output/dataset_manifest.csv")
HTML_DIR = Path("data/raw")
OUTPUT_DIR = Path("data/output/extracted_4_8_blocks")
LOG_FILE = Path("data/output/extraction_4_8_log.csv")

START_RE = re.compile(
    r"\b4\.?\s*8\b.*\b(?:undesirable\s+effects|adverse\s+reactions)\b",
    re.IGNORECASE,
)
END_RE = re.compile(
    r"\b4\.?\s*9\b(?:\s+overdose)?\b",
    re.IGNORECASE,
)


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def iter_candidate_tags(root: Tag) -> list[tuple[Tag, str]]:
    """
    Επιστρέφει tags-υποψήφια για section markers.
    Κρατάμε κυρίως heading-like tags και σύντομα text blocks.
    """
    candidates: list[tuple[Tag, str]] = []
    wanted_tags = {"summary", "h1", "h2", "h3", "h4", "h5", "h6", "p", "strong", "b", "div", "span"}

    for tag in root.find_all(True):
        if tag.name not in wanted_tags:
            continue
        text = normalize_text(tag.get_text(" ", strip=True))
        if not text:
            continue
        if tag.name in {"div", "span", "p"} and len(text) > 220:
            # Αποφεύγω πολύ μεγάλα containers για marker detection.
            continue
        candidates.append((tag, text))
    return candidates


def get_root_tag(soup: BeautifulSoup) -> Optional[Tag]:
    if soup.find("div", class_="spcWrapper"):
        return soup.find("div", class_="spcWrapper")
    if soup.body:
        return soup.body
    return soup.find(True)


def find_markers(root: Tag) -> tuple[Optional[Tag], Optional[Tag]]:
    candidates = iter_candidate_tags(root)
    start_idx: Optional[int] = None
    start_tag: Optional[Tag] = None

    for idx, (tag, text) in enumerate(candidates):
        if START_RE.search(text):
            start_idx = idx
            start_tag = tag
            break

    if start_idx is None:
        return None, None

    end_tag: Optional[Tag] = None
    for idx in range(start_idx + 1, len(candidates)):
        tag, text = candidates[idx]
        if END_RE.search(text):
            end_tag = tag
            break

    return start_tag, end_tag


def extract_between_summaries(start_tag: Tag, end_tag: Optional[Tag]) -> str:
    """
    Κύριο μοτίβο eMC: <details><summary>4.8...</summary>...</details>
    Παίρνει tags από το details(4.8) μέχρι πριν το details(4.9).
    """
    if start_tag.name != "summary" or not start_tag.parent or start_tag.parent.name != "details":
        return ""

    start_details = start_tag.parent
    parent = start_details.parent
    if not parent:
        return ""

    sibling_tags = [child for child in parent.children if isinstance(child, Tag)]
    if not sibling_tags:
        return ""

    try:
        start_idx = sibling_tags.index(start_details)
    except ValueError:
        return ""

    end_idx = len(sibling_tags)
    if end_tag and end_tag.name == "summary" and end_tag.parent and end_tag.parent.name == "details":
        end_details = end_tag.parent
        if end_details in sibling_tags:
            end_idx = sibling_tags.index(end_details)

    selected = sibling_tags[start_idx:end_idx]
    return "\n".join(str(tag) for tag in selected).strip()


def nearest_block_container(tag: Tag, root: Tag) -> Tag:
    current = tag
    while current.parent and isinstance(current.parent, Tag) and current.parent != root:
        parent = current.parent
        if parent.name in {"details", "section", "article", "div", "p"}:
            current = parent
        else:
            break
    return current


def extract_generic(root: Tag, start_tag: Tag, end_tag: Optional[Tag]) -> str:
    """
    Fallback extraction για μη-standard δομές.
    Παίρνει sibling tags από το container του 4.8 μέχρι πριν το 4.9.
    """
    start_container = nearest_block_container(start_tag, root)
    parent = start_container.parent if isinstance(start_container.parent, Tag) else None
    if not parent:
        return str(start_container)

    siblings = [child for child in parent.children if isinstance(child, Tag)]
    if start_container not in siblings:
        return str(start_container)

    start_idx = siblings.index(start_container)
    end_idx = len(siblings)

    if end_tag:
        end_container = nearest_block_container(end_tag, root)
        if end_container in siblings:
            end_idx = siblings.index(end_container)

    if end_idx <= start_idx:
        return str(start_container)

    return "\n".join(str(tag) for tag in siblings[start_idx:end_idx]).strip()


def extract_block_html(html_text: str) -> tuple[bool, str]:
    soup = BeautifulSoup(html_text, "html.parser")
    root = get_root_tag(soup)
    if not root:
        return False, ""

    start_tag, end_tag = find_markers(root)
    if not start_tag:
        return False, ""

    block_html = extract_between_summaries(start_tag, end_tag)
    if not block_html:
        block_html = extract_generic(root, start_tag, end_tag)

    return True, block_html.strip()


def load_manifest(path: Path, limit: int) -> list[dict]:
    if not path.exists():
        raise FileNotFoundError(f"Manifest file not found: {path}")

    rows: list[dict] = []
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for idx, row in enumerate(reader):
            rows.append(row)
            if idx + 1 >= limit:
                break
    return rows


def parse_args() -> int:
    parser = ArgumentParser(description="Extract SmPC 4.8 blocks from local HTML files")
    parser.add_argument(
        "--limit",
        type=int,
        default=LIMIT,
        help=f"Πλήθος αρχείων για επεξεργασία (default: {LIMIT})",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Επεξεργασία όλων των εγγραφών του manifest",
    )
    args = parser.parse_args()

    if args.all:
        return 10**9  # πρακτικά χωρίς limit για τα δεδομένα μας
    if args.limit < 1:
        raise ValueError("Το --limit πρέπει να είναι >= 1")
    return args.limit


def init_log(path: Path) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["product_id", "status", "found_4_8", "has_table", "has_list"])


def append_log(
    path: Path,
    product_id: str,
    status: str,
    found_4_8: bool,
    has_table: bool,
    has_list: bool,
) -> None:
    with path.open("a", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([product_id, status, found_4_8, has_table, has_list])


def main() -> None:
    selected_limit = parse_args()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    init_log(LOG_FILE)

    rows = load_manifest(MANIFEST_FILE, selected_limit)
    print(f"Loaded {len(rows)} manifest rows (LIMIT={selected_limit}).")

    for idx, row in enumerate(rows, start=1):
        product_id = str(row.get("product_id", "")).strip()
        file_name = str(row.get("file_name", "")).strip()
        html_path = HTML_DIR / file_name

        found_4_8 = False
        has_table = False
        has_list = False
        status = "Failed"

        try:
            if not product_id or not file_name:
                raise ValueError("Missing product_id or file_name in manifest row")
            if not html_path.exists():
                raise FileNotFoundError(f"HTML file not found: {html_path}")

            html_text = html_path.read_text(encoding="utf-8", errors="replace")
            found_4_8, block_html = extract_block_html(html_text)

            if found_4_8 and block_html:
                block_soup = BeautifulSoup(block_html, "html.parser")
                has_table = block_soup.find("table") is not None
                has_list = block_soup.find(["ul", "ol"]) is not None

                output_payload = {
                    "product_id": int(product_id),
                    "has_table": has_table,
                    "content_html": block_html,
                }
                output_path = OUTPUT_DIR / f"{product_id}_4_8_block.json"
                output_path.write_text(
                    json.dumps(output_payload, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                status = "Success"
            else:
                status = "Failed"

        except Exception as exc:
            # Συνεχίζω το loop, καταγράφοντας ως Failed χωρίς crash.
            print(f"[{idx}/{len(rows)}] Product {product_id}: Failed ({type(exc).__name__}: {exc})")
            status = "Failed"

        append_log(LOG_FILE, product_id, status, found_4_8, has_table, has_list)

        if status == "Success":
            print(
                f"[{idx}/{len(rows)}] Product {product_id}: Success "
                f"(table={has_table}, list={has_list})"
            )

    print("=== Extraction Done ===")
    print(f"Output dir: {OUTPUT_DIR.resolve()}")
    print(f"Log file: {LOG_FILE.resolve()}")


if __name__ == "__main__":
    main()
