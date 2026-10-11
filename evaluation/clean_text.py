"""Same cleaning as html_to_clean_text in 02_extractor/gemini_extract_adrs.py, without the [:8000] cut."""
from __future__ import annotations

import re

from bs4 import BeautifulSoup


def clean_text_untruncated(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for br in soup.find_all("br"):
        br.replace_with("\n")
    for td in soup.find_all(["td", "th"]):
        td.append(" | ")
    for tr in soup.find_all("tr"):
        tr.append("\n")
    text = soup.get_text(" ", strip=False)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
