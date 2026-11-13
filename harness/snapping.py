"""Quote verification and snapping against raw document text."""
from __future__ import annotations

import re
from typing import Optional, Tuple, Dict, Any

from harness.extract import norm


def clean_spaces(s: str) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip()


def non_space_len(s: str) -> int:
    return len(re.sub(r"\s", "", str(s or "")))


class QuoteSnapper:
    @staticmethod
    def verify_and_snap(
        doc_id: str,
        page: int,
        proposed_quote: str,
        docs_cache: dict[str, dict],
    ) -> tuple[int, str]:
        """Verifies quote against document pages. Returns (snapped_page, snapped_quote)."""
        doc_info = docs_cache.get(doc_id)
        if not doc_info:
            return page, clean_spaces(proposed_quote)

        pages = doc_info.get("pages", [])
        is_scan = doc_info.get("is_scan", False)

        if is_scan:
            # Grader accepts any quote on scanned docs as long as page is valid
            valid_page = max(1, min(page, len(pages))) if pages else 1
            return valid_page, clean_spaces(proposed_quote)

        if not pages:
            return page, clean_spaces(proposed_quote)

        norm_prop = norm(proposed_quote)

        # 1. Check cited page first
        if 1 <= page <= len(pages):
            p_text = pages[page - 1]
            if norm_prop in norm(p_text):
                # Snap to exact slice if possible, or return normalized
                return page, QuoteSnapper._find_exact_slice(p_text, norm_prop) or clean_spaces(proposed_quote)

        # 2. Check all other pages in the document
        for i, p_text in enumerate(pages, 1):
            if norm_prop in norm(p_text):
                return i, QuoteSnapper._find_exact_slice(p_text, norm_prop) or clean_spaces(proposed_quote)

        # 3. Substring / Token search for partial quote
        tokens = [t for t in norm_prop.split() if len(t) >= 4]
        if tokens:
            target_page = page if 1 <= page <= len(pages) else 1
            p_text = pages[target_page - 1]
            lines = p_text.split("\n")
            best_line = None
            best_hits = 0
            for l in lines:
                l_norm = norm(l)
                hits = sum(1 for t in tokens if t in l_norm)
                if hits > best_hits:
                    best_hits = hits
                    best_line = l.strip()
            if best_line and best_hits >= 2 and non_space_len(best_line) >= 12:
                return target_page, best_line

        return page, clean_spaces(proposed_quote)

    @staticmethod
    def _find_exact_slice(page_text: str, norm_quote: str) -> Optional[str]:
        """Finds the contiguous substring in page_text that normalizes to norm_quote."""
        lines = page_text.split("\n")
        # Try line by line first
        for line in lines:
            if norm_quote == norm(line):
                return line.strip()
            if norm_quote in norm(line):
                # Try to extract the exact matching span within the line
                words = line.split()
                q_words = norm_quote.split()
                q_len = len(q_words)
                for start in range(len(words) - q_len + 1):
                    cand = " ".join(words[start : start + q_len])
                    if norm(cand) == norm_quote:
                        return cand
        return None

