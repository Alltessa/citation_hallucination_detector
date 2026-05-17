"""
Citation Hallucination Detector
================================
Checks whether AI-cited sources (titles, authors, URLs, DOIs) are real
AND verifies that the citation actually appears in the given source document.

Requirements:
    pip install requests beautifulsoup4 fake-useragent

Usage:
    python citation_hallucination_detector.py
    python citation_hallucination_detector.py --demo
"""

import argparse
import re
import time
import random
from unittest import result, signals
import urllib.parse
from dataclasses import dataclass, field
from typing import Optional

import requests
from bs4 import BeautifulSoup

try:
    from fake_useragent import UserAgent
    _UA = UserAgent()
except Exception:
    _UA = None


# ─────────────────────────────────────────────
# REGEX PATTERNS
# ─────────────────────────────────────────────

PATTERNS = {
    "doi": re.compile(r"\b10\.\d{4,9}/[^\s\"'<>]+", re.IGNORECASE),
    "arxiv": re.compile(r"\barxiv[:\s]?(\d{4}\.\d{4,5}(?:v\d+)?)\b", re.IGNORECASE),
    "pubmed": re.compile(r"\bPMID[:\s]?(\d{7,8})\b", re.IGNORECASE),
    "isbn": re.compile(
        r"\bISBN[:\s-]?(?:97[89][- ]?\d{1,5}[- ]?\d{1,7}[- ]?\d{1,6}[- ]?\d|"
        r"\d{9}[\dX])\b", re.IGNORECASE,
    ),
    "url": re.compile(
        r"https?://(?:[-\w.]|(?:%[\da-fA-F]{2}))+(?:/[^\s\"'<>]*)?", re.IGNORECASE,
    ),
    "academic_citation": re.compile(
        r"([A-Z][a-z]+(?:,\s[A-Z]\.?)+)\s*\((\d{4})\)\.\s*(.+?)\.\s*"
        r"([A-Z][^,]+),\s*(\d+)(?:\((\d+)\))?,\s*([\d\-–]+)\.", re.DOTALL,
    ),
    "author_year": re.compile(
        r"([A-Z][a-z]+(?: et al\.?)?),?\s+\(?(\d{4}[a-z]?)\)?",
    ),
    "quoted_title": re.compile(r'["""](.{10,200}?)["""]'),
    "journal_ref": re.compile(
        r"(?:Vol\.?|Volume)\s*(\d+),?\s*(?:No\.?|Issue)?\s*(\d+)?,?\s*"
        r"(?:pp?\.?)?\s*([\d]+\s*[-–]\s*[\d]+)", re.IGNORECASE,
    ),
    "web_title": re.compile(r'^(.{10,200}?)\.\s+(?:[A-Z][^.]+\.)\s+https?://', re.DOTALL),
    "multi_author": re.compile(
        r'([A-Z][a-z]+,\s+[A-Z]\.(?:,\s+[A-Z][a-z]+,\s+[A-Z]\.)*(?:,?\s+&\s+[A-Z][a-z]+,\s+[A-Z]\.)?)\s*\((\d{4})\)',
    ),
    "semicolon_author": re.compile(        
        r"([A-Z][a-z]+,\s+[A-Z][a-z]+(?:\s+[A-Z]\.)?)"
        r"(?:;\s*[A-Z][a-z]+,\s+[A-Z][a-z]+(?:\s+[A-Z]\.)?)+"
        r"\s*\((\d{4})\)\.\s*(.+?)\.",
        re.DOTALL
    ),
}


# ─────────────────────────────────────────────
# DATA STRUCTURES
# ─────────────────────────────────────────────

@dataclass
class Citation:
    raw_text: str
    doi: Optional[str] = None
    arxiv_id: Optional[str] = None
    pubmed_id: Optional[str] = None
    isbn: Optional[str] = None
    url: Optional[str] = None
    title: Optional[str] = None
    authors: Optional[str] = None
    year: Optional[str] = None
    journal: Optional[str] = None
    url_domain: Optional[str] = None
    claim: Optional[str] = None   # the asserted sentence, stripped of the parenthetical


@dataclass
class VerificationResult:
    citation: Citation
    source: Optional[str] = None
    verdict: str = "UNKNOWN"          # REAL | HALLUCINATED | UNVERIFIABLE
    confidence: float = 0.0
    evidence: list[str] = field(default_factory=list)
    search_hits: int = 0
    checked_via: list[str] = field(default_factory=list)


# ─────────────────────────────────────────────
# CITATION PARSER
# ─────────────────────────────────────────────

def parse_citation(raw_text: str) -> Citation:
    c = Citation(raw_text=raw_text.strip())

    # Extract the bare claim sentence: strip trailing parenthetical (Author, Year)
    # so "Calamansi is ubiquitous... (Wikipedia, 2026)" -> "Calamansi is ubiquitous..."
    claim_text = re.sub(r"\s*\([^)]{1,120}\)\s*\.?\s*$", "", raw_text.strip()).strip(" .")
    claim_text = claim_text.strip("\"'")
    c.claim = claim_text if len(claim_text) > 10 else None

    m = PATTERNS["doi"].search(raw_text)
    c.doi = m.group(0) if m else None

    m = PATTERNS["arxiv"].search(raw_text)
    c.arxiv_id = m.group(1) if m else None

    m = PATTERNS["pubmed"].search(raw_text)
    c.pubmed_id = m.group(1) if m else None

    m = PATTERNS["isbn"].search(raw_text)
    c.isbn = re.sub(r"[^0-9X]", "", m.group(0)) if m else None

    m = PATTERNS["url"].search(raw_text)
    if m:
        c.url = m.group(0)
        domain_m = re.search(r"https?://(?:www\.)?([^/\s]+)", c.url)
        c.url_domain = domain_m.group(1) if domain_m else None

    # Full academic citation
    m = PATTERNS["academic_citation"].search(raw_text)
    if m:
        c.authors, c.year, c.title, c.journal = m.group(1), m.group(2), m.group(3), m.group(4)

    # Multi-author web report style
    if not c.authors or not c.year:
        m = PATTERNS["multi_author"].search(raw_text)
        if m:
            c.authors = c.authors or m.group(1)
            c.year = c.year or m.group(2)
    
    # Fallback: semicolon-separated authors 
    if not c.title or not c.authors:
        m = PATTERNS["semicolon_author"].search(raw_text)
        if m:
            c.authors = c.authors or m.group(1)
            c.year    = c.year    or m.group(2)
            c.title   = c.title   or m.group(3).strip()

    # Fallback: (Year). Title. — grab text between year and next period
    if not c.title:
        m = re.search(r"\(\d{4}\)\.\s*(.+?)\.", raw_text)
        if m:
            candidate = m.group(1).strip()
            if len(candidate) > 10:
                c.title = candidate

    # Fallback: title from quotes
    if not c.title:
        m = PATTERNS["quoted_title"].search(raw_text)
        c.title = m.group(1).strip() if m else None

    # Fallback: author + year
    if not c.authors or not c.year:
        m = PATTERNS["author_year"].search(raw_text)
        if m:
            c.authors = c.authors or m.group(1)
            c.year = c.year or m.group(2)

    # Fallback: infer title from "Authors (Year). TITLE. Publisher. URL"
    if not c.title and c.url:
        stripped = re.sub(r"https?://\S+", "", raw_text).strip()
        year_split = re.split(r"\(\d{4}\)\.\s*", stripped)
        if len(year_split) >= 2:
            candidate = year_split[1].split(".")[0].strip()
            if len(candidate) > 10:
                c.title = candidate

    return c


# ─────────────────────────────────────────────
# HTTP HELPERS
# ─────────────────────────────────────────────

def _get_headers() -> dict:
    agent = (
        _UA.random if _UA
        else "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
             "AppleWebKit/537.36 (KHTML, like Gecko) "
             "Chrome/124.0 Safari/537.36"
    )
    return {
        "User-Agent": agent,
        "Accept-Language": "en-US,en;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }


def fetch_page_text(url: str, max_chars: int = 50_000) -> Optional[str]:
    """
    Fetch a URL and return its visible text content (stripped of HTML tags).
    Returns None on failure. Caps content at max_chars to avoid memory issues.
    """
    try:
        resp = requests.get(url, headers=_get_headers(), timeout=12, allow_redirects=True)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        # Remove script/style noise
        for tag in soup(["script", "style", "noscript", "meta", "head"]):
            tag.decompose()
        text = soup.get_text(separator=" ", strip=True)
        return text[:max_chars]
    except Exception as e:
        print(f"  [!] Could not fetch {url}: {e}")
        return None


def resolve_doi_to_url(doi: str) -> Optional[str]:
    """Follow a DOI redirect and return the final landing URL."""
    try:
        r = requests.head(
            f"https://doi.org/{doi}", timeout=10,
            allow_redirects=True, headers=_get_headers()
        )
        if r.status_code < 400:
            return r.url
    except Exception:
        pass
    return None


def resolve_arxiv_to_url(arxiv_id: str) -> str:
    return f"https://arxiv.org/abs/{arxiv_id}"


def resolve_pubmed_to_url(pmid: str) -> str:
    return f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"


# ─────────────────────────────────────────────
# SOURCE RESOLVER
# Turns whatever the user typed as "source" into a
# fetchable URL + optional page text.
# ─────────────────────────────────────────────

def resolve_source(source_ref: str) -> tuple[Optional[str], Optional[str]]:
    """
    Given a source reference string, return (resolved_url, page_text).
    page_text may be:
      - str content  → successfully fetched
      - "PAYWALLED"  → source returned 403 (exists but access denied)
      - "BLOCKED"    → source returned other HTTP error
      - None         → could not reach source at all
    Tries DOI → arXiv → PubMed → bare URL → None.
    """

    def _fetch_and_return(url: str) -> tuple[str, Optional[str]]:
        """Fetch a URL and return (url, page_text_or_sentinel)."""
        try:
            resp = requests.get(
                url, headers=_get_headers(), timeout=12, allow_redirects=True
            )
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            for tag in soup(["script", "style", "noscript", "meta", "head"]):
                tag.decompose()
            text = soup.get_text(separator=" ", strip=True)
            return url, text[:50_000]

        except requests.HTTPError as e:
            status = e.response.status_code if e.response is not None else 0
            if status == 403:
                print(f"  [!] {url} is paywalled (403) — marking as UNVERIFIABLE")
                return url, "PAYWALLED"
            else:
                print(f"  [!] {url} returned HTTP {status}")
                return url, "BLOCKED"

        except Exception as e:
            print(f"  [!] Could not fetch {url}: {e}")
            return url, None

    # ── DOI ──────────────────────────────────────────────────────────────
    m = PATTERNS["doi"].search(source_ref)
    if m:
        doi = m.group(0)
        print(f"  [source] Resolving DOI: {doi}")
        try:
            r = requests.head(
                f"https://doi.org/{doi}", timeout=10,
                allow_redirects=True, headers=_get_headers()
            )
            if r.status_code == 403:
                print(f"  [source] DOI landing page is paywalled (403)")
                return r.url, "PAYWALLED"
            if r.status_code < 400:
                print(f"  [source] DOI resolved → {r.url}")
                return _fetch_and_return(r.url)
            print(f"  [source] DOI returned HTTP {r.status_code} — did not resolve")
        except Exception as e:
            print(f"  [source] DOI resolution failed: {e}")

    # ── arXiv ─────────────────────────────────────────────────────────────
    m = PATTERNS["arxiv"].search(source_ref)
    if m:
        arxiv_id = m.group(1)
        url = f"https://arxiv.org/abs/{arxiv_id}"
        print(f"  [source] Fetching arXiv page: {url}")
        return _fetch_and_return(url)

    # ── PubMed ────────────────────────────────────────────────────────────
    m = PATTERNS["pubmed"].search(source_ref)
    if m:
        pmid = m.group(1)
        url = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
        print(f"  [source] Fetching PubMed page: {url}")
        return _fetch_and_return(url)

    # ── Bare URL ──────────────────────────────────────────────────────────
    m = PATTERNS["url"].search(source_ref)
    if m:
        url = m.group(0)
        print(f"  [source] Fetching URL: {url}")
        return _fetch_and_return(url)

    # ── Nothing matched ───────────────────────────────────────────────────
    print(f"  [source] Could not identify a resolvable identifier in: {source_ref[:80]}")
    return None, None


# ─────────────────────────────────────────────
# SEMANTIC MATCHING HELPERS
# ─────────────────────────────────────────────

def _normalize(text: str) -> str:
    return re.sub(r"\W+", " ", text.lower()).strip()


def _token_overlap(query: str, text: str) -> float:
    """Fraction of query tokens found in text."""
    q_tokens = set(_normalize(query).split())
    if not q_tokens:
        return 0.0
    t_tokens = set(_normalize(text).split())
    return len(q_tokens & t_tokens) / len(q_tokens)


def citation_in_source(citation: Citation, source_text: str) -> tuple[int, list[str]]:
    """
    Count how many signals from the citation are found in source_text.
    Returns (signal_count, list_of_matched_evidence).

    Signal weights:
      title match (full)    → 3 points
      title match (partial) → 1 point
      DOI match             → 3 points
      author surname match  → 1 point
      year match            → 1 point
      arxiv/pmid match      → 2 points
    """
    signals = 0
    evidence = []
    text_lower = source_text.lower()

    # Title — primary signal
    if citation.title:
        norm_title = _normalize(citation.title)
        norm_text  = _normalize(source_text)
        overlap    = _token_overlap(citation.title, source_text)

        if norm_title in norm_text:
            signals += 3
            evidence.append(f"✔ Title found verbatim in source: \"{citation.title[:80]}\"")
        elif overlap >= 0.70:
            signals += 3
            evidence.append(f"✔ Title found (>=70% token overlap: {overlap:.0%}): \"{citation.title[:80]}\"")
        elif overlap >= 0.55:
            signals += 1
            evidence.append(f"~ Partial title match ({overlap:.0%} token overlap): \"{citation.title[:80]}\"")
        else:
            evidence.append(f"✘ Title NOT found in source (overlap {overlap:.0%}): \"{citation.title[:80]}\"")

    # DOI — strong signal
    if citation.doi:
        if citation.doi.lower() in text_lower:
            signals += 3
            evidence.append(f"✔ DOI found in source: {citation.doi}")
        else:
            evidence.append(f"✘ DOI NOT found in source: {citation.doi}")

    # arXiv ID
    if citation.arxiv_id:
        if citation.arxiv_id.lower() in text_lower:
            signals += 2
            evidence.append(f"✔ arXiv ID found in source: {citation.arxiv_id}")
        else:
            evidence.append(f"✘ arXiv ID NOT found in source: {citation.arxiv_id}")

    # PubMed ID
    if citation.pubmed_id:
        if citation.pubmed_id in source_text:
            signals += 2
            evidence.append(f"✔ PubMed ID found in source: {citation.pubmed_id}")
        else:
            evidence.append(f"✘ PubMed ID NOT found in source: {citation.pubmed_id}")

    # Author surname
    if citation.authors:
        surname_m = re.search(r"([A-Z][a-z]+)(?=,|\s*$)", citation.authors) \
            or re.match(r"([A-Z][a-z]+)", citation.authors)
        title_overlap = _token_overlap(citation.title, source_text) if citation.title else 0.0
        if surname_m and title_overlap >= 0.60:
            surname = surname_m.group(1)
            if surname.lower() in text_lower:
                signals += 1
                evidence.append(f"✔ Author surname found in source: {surname}")
            else:
                evidence.append(f"✘ Author surname NOT found in source: {surname}")
        elif surname_m:
            evidence.append(f"✘ Author check skipped — title match too weak to validate surname alone")

    # Year
    if citation.year:
        title_overlap = _token_overlap(citation.title, source_text) if citation.title else 0.0
        if citation.year in source_text and title_overlap >= 0.60:
            signals += 1
            evidence.append(f"✔ Year found in source (title also matched): {citation.year}")
        else:
            evidence.append(f"✘ Year skipped — title match too weak ({title_overlap:.0%}) to count year alone")

    # Claim sentence — the core asserted content stripped of the parenthetical.
    # This is the most important check for inline citations like:
    # "Calamansi is ubiquitous in Philippine cuisine (Wikipedia, 2026)"
    # where the claim itself should appear verbatim or near-verbatim in the source.
    if citation.claim:
        norm_claim = _normalize(citation.claim)
        norm_text  = _normalize(source_text)
        claim_overlap = _token_overlap(citation.claim, source_text)

        # Require meaningful length to avoid short trivial matches
        meaningful = len(norm_claim.split()) >= 5

        if meaningful and norm_claim in norm_text:
            signals += 4
            evidence.append('✔ Claim sentence found verbatim in source: "' + citation.claim[:100] + '"')
        elif meaningful and claim_overlap >= 0.75:
            signals += 4
            evidence.append('✔ Claim sentence strongly matches source (' + f'{claim_overlap:.0%}' + ' overlap): "' + citation.claim[:100] + '"')
        elif meaningful and claim_overlap >= 0.65:
            signals += 2
            evidence.append('~ Claim sentence partially matches source (' + f'{claim_overlap:.0%}' + ' overlap): "' + citation.claim[:100] + '"')
        elif meaningful and claim_overlap >= 0.30:
            signals += 1
            evidence.append('~ Weak claim match in source (' + f'{claim_overlap:.0%}' + ' overlap): "' + citation.claim[:100] + '"')
        else:
            evidence.append('✘ Claim sentence NOT found in source (overlap ' + f'{claim_overlap:.0%}' + '): "' + citation.claim[:100] + '"')

    return signals, evidence


def title_similarity(title: str, candidates: list[str]) -> float:
    best = 0.0
    for cand in candidates:
        best = max(best, _token_overlap(title, cand))
    return best


def author_present(authors: str, texts: list[str]) -> bool:
    surname_m = re.match(r"([A-Z][a-z]+)", authors)
    if not surname_m:
        return False
    surname = surname_m.group(1).lower()
    return any(surname in t.lower() for t in texts)


def year_present(year: str, texts: list[str]) -> bool:
    return any(year in t for t in texts)


def domain_present(domain: str, hits: list[dict]) -> bool:
    domain_core = re.sub(r"^www\.", "", domain).lower()
    return any(domain_core in h.get("url", "").lower() for h in hits)


# ─────────────────────────────────────────────
# GOOGLE SEARCH SCRAPER (fallback only)
# ─────────────────────────────────────────────

class GoogleScraper:
    BASE_URL = "https://www.google.com/search"

    def __init__(self, delay_range=(2.5, 5.0)):
        self.delay_range = delay_range

    def search(self, query: str, num_results: int = 5) -> list[dict]:
        time.sleep(random.uniform(*self.delay_range))
        params = {"q": query, "num": num_results, "hl": "en"}
        try:
            resp = requests.get(
                self.BASE_URL, params=params,
                headers=_get_headers(), timeout=10
            )
            resp.raise_for_status()
        except requests.RequestException as e:
            print(f"  [!] Search error: {e}")
            return []

        soup = BeautifulSoup(resp.text, "html.parser")
        results = []
        for g in soup.select("div.g"):
            a_tag = g.select_one("a[href]")
            title_tag = g.select_one("h3")
            snippet_tag = g.select_one("div.VwiC3b, span.aCOpRe, div[data-sncf]")
            if not a_tag or not title_tag:
                continue
            href = a_tag["href"]
            url_match = re.search(r"/url\?q=([^&]+)", href)
            url = urllib.parse.unquote(url_match.group(1)) if url_match else href
            results.append({
                "title":   title_tag.get_text(strip=True),
                "url":     url,
                "snippet": snippet_tag.get_text(strip=True) if snippet_tag else "",
            })
        return results[:num_results]


# ─────────────────────────────────────────────
# ACADEMIC API VALIDATORS (existence only)
# ─────────────────────────────────────────────

def check_doi_exists(doi: str) -> tuple[bool, str]:
    try:
        r = requests.head(f"https://doi.org/{doi}", timeout=8, allow_redirects=True)
        if r.status_code < 400:
            return True, f"DOI resolves → {r.url}"
    except requests.RequestException:
        pass
    return False, "DOI did not resolve"


def check_arxiv_exists(arxiv_id: str) -> tuple[bool, str]:
    url = f"https://export.arxiv.org/abs/{arxiv_id}"
    try:
        r = requests.get(url, timeout=8)
        if r.status_code == 200 and "abs" in r.url:
            title_m = re.search(r"<title>(\[.+?\].+?)</title>", r.text)
            title = title_m.group(1).strip() if title_m else "found"
            return True, f"arXiv paper exists: {title}"
    except requests.RequestException:
        pass
    return False, "arXiv ID not found"


def check_pubmed_exists(pmid: str) -> tuple[bool, str]:
    url = (
        f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
        f"?db=pubmed&id={pmid}&retmode=json"
    )
    try:
        r = requests.get(url, timeout=8)
        data = r.json()
        result = data.get("result", {}).get(pmid, {})
        if result.get("uid") == pmid:
            return True, f"PubMed record: {result.get('title', 'title unavailable')}"
    except Exception:
        pass
    return False, "PubMed ID not found"


def check_openlibrary_isbn(isbn: str) -> tuple[bool, str]:
    url = f"https://openlibrary.org/api/books?bibkeys=ISBN:{isbn}&format=json"
    try:
        r = requests.get(url, timeout=8)
        data = r.json()
        if data:
            key = f"ISBN:{isbn}"
            info = data.get(key, {})
            return True, f"Book found: {info.get('info_url', 'Open Library')}"
    except Exception:
        pass
    return False, "ISBN not found in Open Library"


# ─────────────────────────────────────────────
# CORE VERIFIER
# ─────────────────────────────────────────────

# Signal thresholds for source-content matching
_SIGNAL_STRONG   = 7  # ≥ this → REAL
_SIGNAL_MODERATE = 4   # ≥ this → UNVERIFIABLE (borderline), < → HALLUCINATED


class HallucinationDetector:
    def __init__(self):
        self.scraper = GoogleScraper()

    def _dynamic_threshold(self, citation: Citation) -> tuple[int, int]:
        """
        Adjust STRONG/MODERATE thresholds based on how many
        verifiable fields the citation has.
        """
        has_identifier = any([citation.doi, citation.arxiv_id, citation.pubmed_id, citation.url])

        if has_identifier:
            # DOI/arXiv/URL citations can score high — keep strict thresholds
            return 7, 4
        else:
            # Title-only citations have fewer possible signals — lower thresholds
            return 5, 3

    # ── Public entry point ────────────────────

    def verify(self, raw_citation: str, source: Optional[str] = None) -> VerificationResult:
        citation = parse_citation(raw_citation)
        result   = VerificationResult(citation=citation, source=source.strip() if source else None)

        # ══════════════════════════════════════════════════════
        # PRIMARY PATH: verify citation AGAINST the source doc
        # ══════════════════════════════════════════════════════
        if source:
            source_url, source_text = resolve_source(source)

            # ── handle sentinels before doing any signal matching ──
            if source_text == "PAYWALLED":
                result.verdict    = "UNVERIFIABLE"
                result.confidence = 0.50
                result.evidence.append(
                    "Source is paywalled (403) — cannot verify content. "
                    "Citation may be real but access is restricted."
                )
                return result

            if source_text == "BLOCKED":
                result.verdict    = "UNVERIFIABLE"
                result.confidence = 0.40
                result.evidence.append(
                    "Source server blocked the request — cannot verify content."
                )
                return result

            if source_text:
                result.checked_via.append("Source document content")
                signals, match_evidence = citation_in_source(citation, source_text)
                result.evidence.extend(match_evidence)
                
                strong_thresh, moderate_thresh = self._dynamic_threshold(citation) 
                
                result.evidence.append(
                    f"Total match signals: {signals} "
                    f"(strong≥{strong_thresh}, borderline≥{moderate_thresh})"
                )

                strong_thresh, moderate_thresh = self._dynamic_threshold(citation)
                if signals >= strong_thresh:
                    result.verdict    = "REAL"
                    result.confidence = min(0.60 + signals * 0.06, 0.97)
                    result.evidence.insert(0,
                        f"Citation verified in source document ({source_url or source[:60]})"
                    )
                    return result

                elif signals >= moderate_thresh:
                    result.verdict    = "UNVERIFIABLE"
                    result.confidence = 0.45
                    result.evidence.insert(0,
                        "Partial match in source — some signals found but not enough to confirm."
                    )
                    # Fall through to global checks for additional evidence

                else:
                    # Citation not found in the source at all
                    result.verdict    = "HALLUCINATED"
                    result.confidence = 0.85
                    result.evidence.insert(0,
                        f"Citation NOT found in source document ({source_url or source[:60]}). "
                        "The cited work does not appear to exist in the referenced source."
                    )
                    # Still run global existence checks — maybe it's real but in the
                    # wrong source (mis-attribution vs fabrication are different problems).
                    self._global_existence_check(citation, result)
                    if result.verdict == "REAL":
                        result.verdict    = "HALLUCINATED"
                        result.confidence = 0.80
                        result.evidence.append(
                            "Note: citation may exist elsewhere but is NOT present in the "
                            "provided source — this is a mis-attribution or hallucinated reference."
                        )
                    return result

            else:
                # Source provided but couldn't be fetched
                result.evidence.append(
                    f"Could not fetch source content ({source[:80]}). "
                    "Falling back to global existence checks."
                )

        # ══════════════════════════════════════════════════════
        # FALLBACK: no source provided (or source unfetchable)
        # — check global existence via APIs + Google
        # ══════════════════════════════════════════════════════
        self._global_existence_check(citation, result)
        return result
    # ── Global existence check (no source) ───

    def _global_existence_check(self, citation: Citation, result: VerificationResult):
        """
        Runs DOI / arXiv / PubMed / ISBN / URL / Google checks to determine
        whether the citation plausibly exists anywhere on the web.
        Mutates result in place.
        """

        # DOI
        if citation.doi:
            ok, msg = check_doi_exists(citation.doi)
            result.checked_via.append("DOI resolver")
            result.evidence.append(msg)
            if ok:
                result.verdict    = "REAL"
                result.confidence = max(result.confidence, 0.90)
                return

        # arXiv
        if citation.arxiv_id:
            ok, msg = check_arxiv_exists(citation.arxiv_id)
            result.checked_via.append("arXiv API")
            result.evidence.append(msg)
            if ok:
                result.verdict    = "REAL"
                result.confidence = max(result.confidence, 0.90)
                return
            else:
                result.verdict    = "HALLUCINATED"
                result.confidence = max(result.confidence, 0.90)
                return

        # PubMed
        if citation.pubmed_id:
            ok, msg = check_pubmed_exists(citation.pubmed_id)
            result.checked_via.append("PubMed API")
            result.evidence.append(msg)
            if ok:
                result.verdict    = "REAL"
                result.confidence = max(result.confidence, 0.90)
                return

        # ISBN
        if citation.isbn:
            ok, msg = check_openlibrary_isbn(citation.isbn)
            result.checked_via.append("Open Library")
            result.evidence.append(msg)
            if ok:
                result.verdict    = "REAL"
                result.confidence = max(result.confidence, 0.85)
                return

        # URL live-check
        if citation.url:
            try:
                r = requests.head(
                    citation.url, timeout=8, allow_redirects=True,
                    headers={"User-Agent": "Mozilla/5.0"}
                )
                if r.status_code == 403:
                    r = requests.get(
                        citation.url, timeout=8, allow_redirects=True,
                        headers={"User-Agent": "Mozilla/5.0"}, stream=True
                    )
                    r.close()
                result.checked_via.append("URL check")
                if r.status_code < 400:
                    result.evidence.append(f"URL live ({r.status_code}): {citation.url}")
                    result.verdict    = "REAL"
                    result.confidence = max(result.confidence, 0.75)
                    return
                elif r.status_code == 403:
                    result.evidence.append(f"URL returned 403 (paywalled/blocked — cannot verify content): {citation.url}")
                    result.verdict    = "UNVERIFIABLE"
                    result.confidence = max(result.confidence, 0.50)
                    return
                else:
                    result.evidence.append(f"URL returned {r.status_code}: {citation.url}")
            except requests.RequestException as e:
                result.evidence.append(f"URL unreachable: {e}")

        # Google Search
        self._google_check(citation, result)

    def _google_check(self, citation: Citation, result: VerificationResult):
        queries = []

        if citation.title and citation.authors:
            first = citation.authors.split(",")[0].strip()
            queries.append(f'"{citation.title}" {first}')
        if citation.title:
            queries.append(f'"{citation.title}"')
        if citation.title and citation.url_domain:
            queries.append(f'"{citation.title}" site:{citation.url_domain}')
        if citation.authors and citation.year:
            first = citation.authors.split(",")[0].strip()
            queries.append(f'{first} {citation.year} {citation.journal or ""}')

        if not queries:
            if result.verdict == "UNKNOWN":
                result.verdict    = "UNVERIFIABLE"
                result.confidence = 0.0
                result.evidence.append("Not enough fields to build a search query.")
            return

        all_hits: list[dict] = []
        for q in queries[:3]:
            print(f"  [Google] {q}")
            hits = self.scraper.search(q, num_results=5)
            all_hits.extend(hits)
            if hits:
                break

        # Deduplicate
        seen: set[str] = set()
        unique_hits = [h for h in all_hits if not (h["url"] in seen or seen.add(h["url"]))]

        result.search_hits = len(unique_hits)
        result.checked_via.append("Google Search")
        candidate_texts = [h["title"] + " " + h["snippet"] for h in unique_hits]

        title_score = title_similarity(citation.title, candidate_texts) if citation.title else 0.0
        has_author  = author_present(citation.authors, candidate_texts) if citation.authors else False
        has_year    = year_present(citation.year, candidate_texts) if citation.year else False
        has_domain  = domain_present(citation.url_domain, unique_hits) if citation.url_domain else False

        result.evidence.append(
            f"Google hits: {len(unique_hits)}, title similarity: {title_score:.2f}, "
            f"author match: {has_author}, year match: {has_year}, domain match: {has_domain}"
        )

        if not unique_hits:
            if result.verdict not in ("HALLUCINATED",):
                result.verdict    = "HALLUCINATED"
                result.confidence = max(result.confidence, 0.70)
                result.evidence.append("No Google results found for this citation.")
            return

        if title_score >= 0.55 and (has_author or has_domain or has_year):
            result.verdict    = "REAL"
            result.confidence = max(result.confidence,
                                    min(0.45 + title_score * 0.5 + (0.10 if has_domain else 0), 0.85))
        elif has_domain and title_score >= 0.25:
            result.verdict    = "REAL"
            result.confidence = max(result.confidence, 0.75)
        elif title_score >= 0.40 and (has_author or has_year):
            result.verdict    = "REAL"
            result.confidence = max(result.confidence, 0.65)
        elif title_score < 0.20 and not has_domain:
            if result.verdict not in ("HALLUCINATED",):
                result.verdict    = "HALLUCINATED"
                result.confidence = max(result.confidence, 0.75)
                result.evidence.append(
                    "Search results returned but none match the cited title or domain."
                )
        else:
            if result.verdict == "UNKNOWN":
                result.verdict    = "UNVERIFIABLE"
                result.confidence = max(result.confidence, 0.40)


# ─────────────────────────────────────────────
# PRETTY PRINTER
# ─────────────────────────────────────────────

VERDICT_ICONS = {
    "REAL":         "✅",
    "HALLUCINATED": "❌",
    "UNVERIFIABLE": "⚠️",
    "UNKNOWN":      "❓",
}


def print_result(r: VerificationResult):
    icon = VERDICT_ICONS.get(r.verdict, "❓")
    print(f"\n{'─'*60}")
    if r.source:
        print(f"SOURCE   : {r.source[:90]}")
    print(f"CITATION : {r.citation.raw_text[:90]}")
    print(f"VERDICT  : {icon}  {r.verdict}  (confidence: {r.confidence:.0%})")
    print(f"CHECKED  : {', '.join(r.checked_via) or 'none'}")
    for e in r.evidence:
        print(f"  • {e}")
    print(f"{'─'*60}")


# ─────────────────────────────────────────────
# TEST CASES
# ─────────────────────────────────────────────

TEST_CASES = [
    {
        "description": "Real DOI — citation should be found in the paper",
        "source": "DOI: 10.48550/arXiv.1706.03762",
        "citation": (
            "Vaswani, A. et al. (2017). Attention Is All You Need. "
            "Advances in Neural Information Processing Systems, 30. "
            "DOI: 10.48550/arXiv.1706.03762"
        ),
    },
    {
        "description": "Real PubMed — citation should match PMID page",
        "source": "PMID: 13054692",
        "citation": (
            "Watson, J.D., & Crick, F.H.C. (1953). "
            "Molecular Structure of Nucleic Acids. "
            "Nature, 171, 737–738. PMID: 13054692"
        ),
    },
    {
        "description": "Hallucinated citation — fake paper against real source",
        "source": "DOI: 10.48550/arXiv.1706.03762",
        "citation": (
            "Smith, J. & Johnson, R. (2021). Deep Quantum Neural Bridges for "
            "Multimodal Sentiment Analysis. Journal of Artificial Cognition, 14(3), 201–219."
        ),
    },
]


def run_demo():
    detector = HallucinationDetector()
    print("=" * 60)
    print("  AI CITATION HALLUCINATION DETECTOR DEMO")
    print("=" * 60)
    for case in TEST_CASES:
        print(f"\n[demo] {case['description']}")
        print(f"Source  : {case['source']}")
        print(f"Citation: {case['citation']}")
        result = detector.verify(case["citation"], source=case["source"])
        print_result(result)
        time.sleep(1)


# ─────────────────────────────────────────────
# INTERACTIVE ENTRY POINT
# ─────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Citation hallucination detector")
    parser.add_argument("--demo", action="store_true", help="Run sample citation/source checks")
    args = parser.parse_args()

    if args.demo:
        run_demo()
        exit()

    detector = HallucinationDetector()

    print("=" * 60)
    print("  AI CITATION HALLUCINATION DETECTOR")
    print("=" * 60)
    print("Enter the SOURCE (URL, DOI, PMID, or arXiv ID) where the citation should appear,")
    print("then the CITATION text to verify. Leave citation blank to exit.\n")

    while True:
        while True:
            try:
                source = input("Source   > ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nExiting.")
                exit()
            if source:
                break
            print("  [!] Source is required.")

        while True:
            try:
                raw = input("Citation > ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nExiting.")
                exit()
            if not raw:
                print("Exiting.")
                exit()
            break

        print(f"\n[checking] {raw[:70]}...")
        result = detector.verify(raw, source=source)
        print_result(result)
        time.sleep(1)