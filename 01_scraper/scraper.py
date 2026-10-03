"""
Phase 1 – Data Acquisition
Κατεβάζει SmPC κείμενα από το medicines.org.uk και τα αποθηκεύει τοπικά σε .txt αρχεία.
"""

import os
import time
import requests
from bs4 import BeautifulSoup

# ─── Ρυθμίσεις ────────────────────────────────────────────────────────────────

BASE_URL = "https://www.medicines.org.uk/emc/product/{product_id}/smpc/print"

# Δοκιμαστικά IDs φαρμάκων
PRODUCT_IDS = [3861, 3862, 3863]

# Καθυστέρηση (σε δευτερόλεπτα) ανάμεσα σε κάθε request – υποχρεωτικό για να
# μην μπλοκαριστεί η IP μου από τον server.
REQUEST_DELAY = 6

# Φάκελος αποθήκευσης κειμένων
OUTPUT_DIR = "data/raw/smpc_texts"

# User-Agent header ώστε ο server να μη με αντιμετωπίζει σαν bot
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    )
}


# ─── Βοηθητικές Συναρτήσεις ───────────────────────────────────────────────────

def fetch_smpc_text(product_id: int) -> str | None:
    """Κατεβάζει τη σελίδα SmPC για ένα product ID και επιστρέφει το καθαρό κείμενο."""
    url = BASE_URL.format(product_id=product_id)
    print(f"[*] Fetching product {product_id}: {url}")

    try:
        response = requests.get(url, headers=HEADERS, timeout=30)
        response.raise_for_status()
    except requests.RequestException as e:
        print(f"[!] Αποτυχία για product {product_id}: {e}")
        return None

    soup = BeautifulSoup(response.text, "html.parser")

    # Αφαιρούμε <script> και <style> tags πριν εξάγουμε το κείμενο
    for tag in soup(["script", "style"]):
        tag.decompose()

    text = soup.get_text(separator="\n", strip=True)
    return text


def save_text(product_id: int, text: str, output_dir: str) -> str:
    """Αποθηκεύει το κείμενο σε αρχείο txt και επιστρέφει το filepath."""
    os.makedirs(output_dir, exist_ok=True)
    filepath = os.path.join(output_dir, f"smpc_{product_id}.txt")

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(text)

    return filepath


# ─── Main ──────────────────────────────────────────────────────────────────────

def main():
    print(f"=== SmPC Scraper – Phase 1 ===")
    print(f"Products to fetch: {PRODUCT_IDS}")
    print(f"Delay between requests: {REQUEST_DELAY}s\n")

    for i, product_id in enumerate(PRODUCT_IDS):
        text = fetch_smpc_text(product_id)

        if text:
            filepath = save_text(product_id, text, OUTPUT_DIR)
            print(f"[✓] Saved {len(text):,} chars → {filepath}")
        else:
            print(f"[✗] Skipped product {product_id}")

        # Delay ανάμεσα στα requests (εκτός αν είναι το τελευταίο)
        if i < len(PRODUCT_IDS) - 1:
            print(f"[~] Waiting {REQUEST_DELAY}s before next request...\n")
            time.sleep(REQUEST_DELAY)

    print("\n=== Done ===")


if __name__ == "__main__":
    main()
