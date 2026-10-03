"""
Phase 2 - Step 1: Discover valid medicines.org.uk EMC product IDs.

"""

from __future__ import annotations

import json
import re
import time
from collections import deque
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse
import xml.etree.ElementTree as ET

import requests


# Primary sitemap entry points. The script will use the first one that works.
SITEMAP_CANDIDATES = [
    "https://www.medicines.org.uk/sitemap.xml",
    "https://www.medicines.org.uk/emc/sitemap.xml",
]

# Match product IDs from URLs like:
# - /emc/product/3861
# - /emc/product/3861/smpc
# - /emc/product/3861/smpc/print
PRODUCT_ID_PATTERN = re.compile(r"/emc/product/(\d+)(?:/|$)")

OUTPUT_FILE = Path("data/output/valid_ids.json")

# Small polite pause between sitemap requests.
# (This is for sitemap crawling only, not the strict 6s used in the main scraper.)
SITEMAP_REQUEST_DELAY = 0.5

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    )
}


def is_xml_sitemap(url: str) -> bool:
    """Heuristic check: URL path looks like an XML sitemap."""
    path = urlparse(url).path.lower()
    return path.endswith(".xml")


def extract_product_id(url: str) -> int | None:
    """Return product ID if URL contains /emc/product/{id}, otherwise None."""
    match = PRODUCT_ID_PATTERN.search(url)
    if not match:
        return None
    return int(match.group(1))


def fetch_xml(url: str, session: requests.Session) -> ET.Element:
    """Download XML URL and return parsed root element."""
    response = session.get(url, headers=HEADERS, timeout=30)
    response.raise_for_status()
    return ET.fromstring(response.content)


def iter_loc_values(xml_root: ET.Element) -> Iterable[str]:
    """
    Yield all <loc> values from XML root.
    Supports namespaced and non-namespaced sitemap XML.
    """
    for elem in xml_root.iter():
        if elem.tag.endswith("loc") and elem.text:
            value = elem.text.strip()
            if value:
                yield value


def find_working_seed(session: requests.Session) -> str:
    """Return the first reachable sitemap candidate."""
    errors: list[str] = []

    for url in SITEMAP_CANDIDATES:
        try:
            response = session.get(url, headers=HEADERS, timeout=20)
            response.raise_for_status()
            if "xml" in response.headers.get("Content-Type", "").lower() or response.text.lstrip().startswith("<?xml"):
                return url
            errors.append(f"{url} -> unexpected content-type")
        except requests.RequestException as exc:
            errors.append(f"{url} -> {exc}")

    raise RuntimeError("No working sitemap seed found:\n- " + "\n- ".join(errors))


def discover_valid_ids() -> list[int]:
    """
    Crawl sitemap graph and collect all valid EMC product IDs.

    Returns:
        Sorted unique list of IDs.
    """
    session = requests.Session()
    seed = find_working_seed(session)
    print(f"[*] Using sitemap seed: {seed}")

    queue = deque([seed])
    visited_sitemaps: set[str] = set()
    product_ids: set[int] = set()

    while queue:
        sitemap_url = queue.popleft()
        if sitemap_url in visited_sitemaps:
            continue

        visited_sitemaps.add(sitemap_url)
        print(f"[*] Reading sitemap: {sitemap_url}")

        try:
            root = fetch_xml(sitemap_url, session)
        except requests.RequestException as exc:
            print(f"[!] Request failed for {sitemap_url}: {exc}")
            continue
        except ET.ParseError as exc:
            print(f"[!] XML parse failed for {sitemap_url}: {exc}")
            continue

        for loc in iter_loc_values(root):
            maybe_id = extract_product_id(loc)
            if maybe_id is not None:
                product_ids.add(maybe_id)
            elif is_xml_sitemap(loc):
                queue.append(loc)

        if queue:
            time.sleep(SITEMAP_REQUEST_DELAY)

    ids = sorted(product_ids)
    print(f"[✓] Discovered {len(ids)} valid product IDs.")
    print(f"[i] Sitemaps visited: {len(visited_sitemaps)}")
    return ids


def save_ids(ids: list[int], output_path: Path = OUTPUT_FILE) -> None:
    """Save IDs to JSON file as a sorted list."""
    payload = {
        "source": "medicines.org.uk sitemap crawl",
        "count": len(ids),
        "ids": ids,
    }
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"[✓] Saved IDs to: {output_path.resolve()}")


def main() -> None:
    ids = discover_valid_ids()
    save_ids(ids)


if __name__ == "__main__":
    main()
