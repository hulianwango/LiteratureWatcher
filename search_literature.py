from __future__ import annotations

import argparse
import json
import os
import re
import time
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

import feedparser
import requests
import yaml


OUTPUT_FIELDS = [
    "title_en",
    "title_zh",
    "authors",
    "year",
    "publication_date",
    "journal_or_source",
    "doi",
    "url",
    "abstract_en",
    "abstract_zh",
    "previously_seen",
    "matched_keywords",
    "relevance_score",
    "reason",
]

DEFAULT_ENABLED_SOURCES = [
    "crossref",
    "openalex",
    "publishers",
    "semantic_scholar",
    "pubmed",
    "europe_pmc",
    "arxiv",
    "doaj",
    "biorxiv",
    "medrxiv",
]

DEFAULT_PUBLISHER_SEARCHES = [
    {
        "id": "nature",
        "label": "Nature Portfolio",
        "crossref_member": 297,
        "container_query": "Nature Nature Communications Scientific Reports Nature Photonics Nature Nanotechnology Nature Materials Nature Chemistry Communications Materials Communications Chemistry",
        "match_terms": [
            "Nature",
            "Nature Communications",
            "Scientific Reports",
            "Communications Materials",
            "Communications Chemistry",
        ],
    },
    {"id": "springer", "label": "Springer Nature", "crossref_member": 297},
    {"id": "science_aaas", "label": "Science / AAAS", "crossref_member": 221, "container_query": "Science Science Advances Science Robotics Science Signaling"},
    {"id": "acs", "label": "American Chemical Society", "crossref_member": 316},
    {"id": "rsc", "label": "Royal Society of Chemistry", "crossref_member": 292},
    {"id": "wiley", "label": "Wiley", "crossref_member": 311},
    {"id": "elsevier", "label": "Elsevier", "crossref_member": 78},
    {"id": "taylor_francis", "label": "Taylor & Francis / Informa", "crossref_member": 301},
    {"id": "ieee", "label": "IEEE", "crossref_member": 263},
    {"id": "optica", "label": "Optica Publishing Group", "crossref_member": 285},
    {"id": "iop", "label": "IOP Publishing", "crossref_member": 266},
    {"id": "aip", "label": "AIP Publishing", "crossref_member": 317},
    {"id": "mdpi", "label": "MDPI", "crossref_member": 1968},
    {"id": "frontiers", "label": "Frontiers", "crossref_member": 1965},
    {"id": "plos", "label": "PLOS", "crossref_member": 340},
    {"id": "sage", "label": "SAGE", "crossref_member": 179},
    {"id": "oup", "label": "Oxford University Press", "crossref_member": 286},
    {"id": "cup", "label": "Cambridge University Press", "crossref_member": 56},
]


def load_config(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        return yaml.safe_load(file) or {}


def today_local() -> date:
    return datetime.now().date()


def ensure_data_files(config: dict[str, Any]) -> tuple[Path, Path, Path]:
    paths = config.get("paths", {})
    data_dir = Path(paths.get("data_dir", "data"))
    data_dir.mkdir(parents=True, exist_ok=True)

    seen_file = data_dir / paths.get("seen_items_file", "seen_items.json")
    latest_file = data_dir / paths.get("latest_results_file", "latest_results.json")

    if not seen_file.exists():
        save_json(seen_file, {"doi": [], "title": []})

    return data_dir, seen_file, latest_file


def save_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)


def load_seen(path: Path) -> dict[str, set[str]]:
    if not path.exists():
        return {"doi": set(), "title": set()}

    with path.open("r", encoding="utf-8") as file:
        raw = json.load(file)

    return {
        "doi": {normalize_doi(value) for value in raw.get("doi", []) if value},
        "title": {normalize_title(value) for value in raw.get("title", []) if value},
    }


def save_seen(path: Path, seen: dict[str, set[str]]) -> None:
    payload = {
        "doi": sorted(value for value in seen["doi"] if value),
        "title": sorted(value for value in seen["title"] if value),
    }
    save_json(path, payload)


def normalize_doi(value: str | None) -> str:
    if not value:
        return ""

    doi = value.strip().lower()
    doi = re.sub(r"^https?://(dx\.)?doi\.org/", "", doi)
    doi = doi.removeprefix("doi:")
    return doi.strip()


def normalize_title(value: str | None) -> str:
    if not value:
        return ""

    title = re.sub(r"\s+", " ", value).strip().lower()
    return re.sub(r"[^a-z0-9]+", "", title)


def clean_text(value: Any) -> str:
    if not value:
        return ""

    text = str(value)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def first_text(values: Any) -> str:
    if isinstance(values, list) and values:
        return clean_text(values[0])
    return clean_text(values)


def max_results_for_source(config: dict[str, Any], source: str, default: int = 30) -> int:
    configured = config.get("max_results_per_source", {})
    if source.startswith("publisher_"):
        return int(configured.get(source, configured.get("publishers", default)))
    return int(configured.get(source, default))


def enabled_sources(config: dict[str, Any]) -> list[str]:
    configured = config.get("enabled_sources")
    if not configured:
        return DEFAULT_ENABLED_SOURCES

    return [
        str(source).strip().lower()
        for source in configured
        if str(source).strip()
    ]


def publisher_profiles(config: dict[str, Any]) -> list[dict[str, Any]]:
    raw_profiles = config.get("publisher_searches", DEFAULT_PUBLISHER_SEARCHES)
    if not isinstance(raw_profiles, list):
        return DEFAULT_PUBLISHER_SEARCHES

    profiles: list[dict[str, Any]] = []
    for raw_profile in raw_profiles:
        if not isinstance(raw_profile, dict):
            continue
        profile_id = clean_text(raw_profile.get("id")).lower()
        label = clean_text(raw_profile.get("label"))
        if not profile_id or not label:
            continue
        profiles.append(raw_profile)

    return profiles


def parse_year_from_date_parts(parts: Any) -> str:
    try:
        return str(parts["date-parts"][0][0])
    except (KeyError, IndexError, TypeError):
        return ""


def parse_date_from_date_parts(parts: Any) -> date | None:
    try:
        values = parts["date-parts"][0]
        year = int(values[0])
        month = int(values[1]) if len(values) > 1 and values[1] else 1
        day = int(values[2]) if len(values) > 2 and values[2] else 1
    except (KeyError, IndexError, TypeError, ValueError):
        return None

    month = min(max(month, 1), 12)
    day = min(max(day, 1), 31)
    try:
        return date(year, month, day)
    except ValueError:
        return date(year, month, 1)


def parse_iso_date(value: str | None) -> date | None:
    if not value:
        return None

    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def parse_year_text(value: Any) -> str:
    text = clean_text(value)
    match = re.search(r"\b(19|20)\d{2}\b", text)
    return match.group(0) if match else ""


def doi_url(doi: str) -> str:
    doi = normalize_doi(doi)
    return f"https://doi.org/{doi}" if doi else ""


def compact_people(names: list[str], limit: int = 20) -> str:
    return "; ".join(name for name in names[:limit] if name)


def parse_pubmed_month(value: str) -> int:
    clean = value.strip()
    if clean.isdigit():
        return min(max(int(clean), 1), 12)

    month_names = {
        "jan": 1,
        "feb": 2,
        "mar": 3,
        "apr": 4,
        "may": 5,
        "jun": 6,
        "jul": 7,
        "aug": 8,
        "sep": 9,
        "oct": 10,
        "nov": 11,
        "dec": 12,
    }
    return month_names.get(clean[:3].lower(), 1)


def parse_pubmed_publication_date(article_node: ET.Element) -> date | None:
    pub_date = article_node.find("./Journal/JournalIssue/PubDate")
    if pub_date is None:
        return None

    year_text = pub_date.findtext("Year", default="").strip()
    if not year_text:
        medline_date = pub_date.findtext("MedlineDate", default="")
        match = re.search(r"\d{4}", medline_date)
        year_text = match.group(0) if match else ""
    if not year_text:
        return None

    month = parse_pubmed_month(pub_date.findtext("Month", default=""))
    day_text = pub_date.findtext("Day", default="").strip()
    day = int(day_text) if day_text.isdigit() else 1
    try:
        return date(int(year_text), month, day)
    except ValueError:
        return date(int(year_text), month, 1)


def item_publication_date(item: dict[str, Any]) -> date:
    parsed = parse_iso_date(str(item.get("publication_date", "")))
    if parsed is not None:
        return parsed

    year = str(item.get("year", "")).strip()
    if year.isdigit():
        return date(int(year), 1, 1)
    return date.min


def item_relevance_score(item: dict[str, Any]) -> float:
    try:
        return float(item.get("relevance_score", 0))
    except (TypeError, ValueError):
        return 0.0


def build_keyword_map(config: dict[str, Any]) -> list[tuple[str, float]]:
    keywords = []
    for item in config.get("keywords", []):
        term = str(item.get("term", "")).strip()
        if not term:
            continue
        keywords.append((term, float(item.get("weight", 1))))
    return keywords


def source_terms(config: dict[str, Any], source: str) -> list[str]:
    configured = config.get("source_query_terms", {}).get(source)
    if not configured:
        configured = config.get("source_query_terms", {}).get("default")

    if configured:
        terms = [str(term).strip() for term in configured if str(term).strip()]
    else:
        terms = [term for term, _weight in build_keyword_map(config)]

    max_terms = config.get("max_query_terms", 40)
    source_max_terms = config.get("max_query_terms_per_source", {}).get(source)
    if source_max_terms is not None:
        max_terms = source_max_terms

    try:
        max_terms_count = int(max_terms)
    except (TypeError, ValueError):
        max_terms_count = 0

    if max_terms_count > 0:
        return terms[:max_terms_count]
    return terms


def build_plain_query(terms: list[str]) -> str:
    return " ".join(terms)


def contains_any_term(text: str, terms: list[str]) -> bool:
    haystack = text.lower()
    return any(term.lower() in haystack for term in terms)


def maybe_source_date_in_window(value: Any, cutoff: date, end_date: date) -> bool:
    parsed = parse_iso_date(clean_text(value))
    if parsed is None:
        return True
    return cutoff <= parsed <= end_date


def score_item(item: dict[str, Any], keywords: list[tuple[str, float]], config: dict[str, Any]) -> dict[str, Any]:
    title = item.get("title_en") or item.get("title", "")
    abstract = item.get("abstract_en") or item.get("abstract", "")
    title_text = title.lower()
    abstract_text = abstract.lower()
    title_multiplier = float(config.get("scoring", {}).get("title_multiplier", 3.0))
    abstract_multiplier = float(config.get("scoring", {}).get("abstract_multiplier", 1.0))

    score = 0.0
    matched: list[str] = []
    title_hits: list[str] = []
    abstract_hits: list[str] = []

    for term, weight in keywords:
        needle = term.lower()
        in_title = needle in title_text
        in_abstract = needle in abstract_text
        if not in_title and not in_abstract:
            continue

        matched.append(term)
        if in_title:
            title_hits.append(term)
            score += weight * title_multiplier
        if in_abstract:
            abstract_hits.append(term)
            score += weight * abstract_multiplier

    reason_parts = []
    if title_hits:
        reason_parts.append("标题命中关键词：" + ", ".join(title_hits[:8]))
    if abstract_hits:
        reason_parts.append("摘要命中关键词：" + ", ".join(abstract_hits[:8]))
    if not reason_parts:
        reason_parts.append("未匹配配置关键词")

    item["matched_keywords"] = matched
    item["relevance_score"] = round(score, 2)
    item["reason"] = "; ".join(reason_parts)
    return item


def request_headers(config: dict[str, Any]) -> dict[str, str]:
    user_agent = config.get("user_agent", "LiteratureWatcher/0.1")
    return {"User-Agent": user_agent}


def http_get(
    config: dict[str, Any],
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: int | None = None,
) -> requests.Response:
    request_timeout = timeout or int(config.get("request_timeout_seconds", 25))
    retry_count = int(config.get("request_retry_count", 1))
    retry_sleep = float(config.get("request_retry_sleep_seconds", 1.5))
    active_headers = headers or request_headers(config)
    last_error: Exception | None = None

    for attempt in range(retry_count + 1):
        try:
            response = requests.get(url, params=params, headers=active_headers, timeout=request_timeout)
            retry_after = response.headers.get("Retry-After")
            retryable_status = response.status_code == 429 or response.status_code >= 500
            if retryable_status and attempt < retry_count:
                try:
                    sleep_seconds = float(retry_after) if retry_after else retry_sleep * (attempt + 1)
                except ValueError:
                    sleep_seconds = retry_sleep * (attempt + 1)
                time.sleep(sleep_seconds)
                continue
            return response
        except (requests.ConnectionError, requests.Timeout) as error:
            last_error = error
            if attempt >= retry_count:
                raise
            time.sleep(retry_sleep * (attempt + 1))

    if last_error is not None:
        raise last_error
    raise RuntimeError(f"HTTP GET failed without a response: {url}")


def openalex_mailto(config: dict[str, Any]) -> str:
    value = clean_text(config.get("openalex_mailto", ""))
    if value and "example.com" not in value:
        return value

    match = re.search(r"mailto:([^)>\s]+)", clean_text(config.get("user_agent", "")))
    email = match.group(1) if match else ""
    return email if email and "example.com" not in email else ""


def reconstruct_openalex_abstract(inverted_index: Any) -> str:
    if not isinstance(inverted_index, dict):
        return ""

    positions: dict[int, str] = {}
    for word, raw_positions in inverted_index.items():
        if not isinstance(raw_positions, list):
            continue
        for position in raw_positions:
            if isinstance(position, int):
                positions[position] = str(word)

    if not positions:
        return ""
    return " ".join(positions[index] for index in sorted(positions))


def fetch_openalex(config: dict[str, Any], cutoff: date, end_date: date) -> list[dict[str, Any]]:
    source = "openalex"
    max_results = max_results_for_source(config, source)
    terms = source_terms(config, source)
    timeout = int(config.get("request_timeout_seconds", 25))
    params = {
        "search": build_plain_query(terms),
        "filter": f"from_publication_date:{cutoff.isoformat()},to_publication_date:{end_date.isoformat()}",
        "sort": "publication_date:desc",
        "per-page": max_results * 2,
        "select": "id,doi,display_name,publication_year,publication_date,authorships,primary_location,abstract_inverted_index",
    }
    mailto = openalex_mailto(config)
    if mailto:
        params["mailto"] = mailto

    response = http_get(
        config,
        "https://api.openalex.org/works",
        params=params,
        headers=request_headers(config),
        timeout=timeout,
    )
    response.raise_for_status()

    items: list[dict[str, Any]] = []
    for work in response.json().get("results", []):
        primary_location = work.get("primary_location") or {}
        source_info = primary_location.get("source") or {}
        doi = normalize_doi(work.get("doi"))
        authors = []
        for authorship in work.get("authorships", []):
            author = authorship.get("author") or {}
            name = clean_text(author.get("display_name"))
            if name:
                authors.append(name)

        url = primary_location.get("landing_page_url") or work.get("id") or doi_url(doi)
        items.append(
            {
                "title_en": clean_text(work.get("display_name")),
                "title_zh": "",
                "authors": compact_people(authors),
                "year": str(work.get("publication_year") or ""),
                "publication_date": clean_text(work.get("publication_date")),
                "journal_or_source": clean_text(source_info.get("display_name")) or "OpenAlex",
                "doi": doi,
                "url": url,
                "abstract_en": reconstruct_openalex_abstract(work.get("abstract_inverted_index")),
                "abstract_zh": "",
                "source": source,
            }
        )

        if len(items) >= max_results:
            break

    return items


def semantic_scholar_headers(config: dict[str, Any]) -> dict[str, str]:
    headers = request_headers(config)
    api_key = clean_text(os.environ.get("SEMANTIC_SCHOLAR_API_KEY", ""))
    if api_key:
        headers["x-api-key"] = api_key
    return headers


def fetch_semantic_scholar(config: dict[str, Any], cutoff: date, end_date: date) -> list[dict[str, Any]]:
    source = "semantic_scholar"
    max_results = max_results_for_source(config, source)
    terms = source_terms(config, source)
    timeout = int(config.get("request_timeout_seconds", 25))
    params = {
        "query": build_plain_query(terms),
        "limit": min(max_results * 2, 100),
        "publicationDateOrYear": f"{cutoff.isoformat()}:{end_date.isoformat()}",
        "fields": "title,authors,year,venue,publicationVenue,externalIds,url,abstract,publicationDate",
    }
    response = http_get(
        config,
        "https://api.semanticscholar.org/graph/v1/paper/search",
        params=params,
        headers=semantic_scholar_headers(config),
        timeout=timeout,
    )
    response.raise_for_status()

    items: list[dict[str, Any]] = []
    for paper in response.json().get("data", []):
        publication_venue = paper.get("publicationVenue") or {}
        external_ids = paper.get("externalIds") or {}
        doi = normalize_doi(external_ids.get("DOI"))
        authors = [
            clean_text(author.get("name"))
            for author in paper.get("authors", [])
            if clean_text(author.get("name"))
        ]

        items.append(
            {
                "title_en": clean_text(paper.get("title")),
                "title_zh": "",
                "authors": compact_people(authors),
                "year": str(paper.get("year") or parse_year_text(paper.get("publicationDate"))),
                "publication_date": clean_text(paper.get("publicationDate")),
                "journal_or_source": clean_text(publication_venue.get("name")) or clean_text(paper.get("venue")) or "Semantic Scholar",
                "doi": doi,
                "url": paper.get("url") or doi_url(doi),
                "abstract_en": clean_text(paper.get("abstract")),
                "abstract_zh": "",
                "source": source,
            }
        )

        if len(items) >= max_results:
            break

    return items


def europe_pmc_query(terms: list[str], cutoff: date, end_date: date) -> str:
    quoted = [f'TITLE_ABS:"{term.replace(chr(34), " ")}"' for term in terms]
    return f"({' OR '.join(quoted)}) AND FIRST_PDATE:[{cutoff.isoformat()} TO {end_date.isoformat()}]"


def fetch_europe_pmc(config: dict[str, Any], cutoff: date, end_date: date) -> list[dict[str, Any]]:
    source = "europe_pmc"
    max_results = max_results_for_source(config, source)
    terms = source_terms(config, source)
    timeout = int(config.get("request_timeout_seconds", 25))
    params = {
        "query": europe_pmc_query(terms, cutoff, end_date),
        "format": "json",
        "pageSize": min(max_results * 2, 100),
        "sort": "FIRST_PDATE_D desc",
    }
    response = http_get(
        config,
        "https://www.ebi.ac.uk/europepmc/webservices/rest/search",
        params=params,
        headers=request_headers(config),
        timeout=timeout,
    )
    response.raise_for_status()

    items: list[dict[str, Any]] = []
    for record in response.json().get("resultList", {}).get("result", []):
        doi = normalize_doi(record.get("doi"))
        pmid = clean_text(record.get("pmid"))
        url = doi_url(doi) or (f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else clean_text(record.get("fullTextUrlList")))
        items.append(
            {
                "title_en": clean_text(record.get("title")),
                "title_zh": "",
                "authors": clean_text(record.get("authorString")),
                "year": clean_text(record.get("pubYear")) or parse_year_text(record.get("firstPublicationDate")),
                "publication_date": clean_text(record.get("firstPublicationDate")),
                "journal_or_source": clean_text(record.get("journalTitle")) or "Europe PMC",
                "doi": doi,
                "url": url,
                "abstract_en": clean_text(record.get("abstractText")),
                "abstract_zh": "",
                "source": source,
            }
        )

        if len(items) >= max_results:
            break

    return items


def fetch_doaj(config: dict[str, Any], cutoff: date, end_date: date) -> list[dict[str, Any]]:
    source = "doaj"
    max_results = max_results_for_source(config, source)
    terms = source_terms(config, source)
    timeout = int(config.get("request_timeout_seconds", 25))
    url = "https://doaj.org/api/search/articles/" + quote(build_plain_query(terms))
    response = http_get(
        config,
        url,
        params={"pageSize": min(max_results * 2, 100)},
        headers=request_headers(config),
        timeout=timeout,
    )
    response.raise_for_status()

    items: list[dict[str, Any]] = []
    for result in response.json().get("results", []):
        bibjson = result.get("bibjson") or {}
        if not maybe_source_date_in_window(result.get("last_updated"), cutoff, end_date):
            continue

        doi = ""
        for identifier in bibjson.get("identifier", []):
            if str(identifier.get("type", "")).lower() == "doi":
                doi = normalize_doi(identifier.get("id"))
                break

        journal = bibjson.get("journal") or {}
        author_names = [
            clean_text(author.get("name"))
            for author in bibjson.get("author", [])
            if clean_text(author.get("name"))
        ]
        links = bibjson.get("link", [])
        landing_url = ""
        if links:
            landing_url = clean_text(links[0].get("url"))

        items.append(
            {
                "title_en": clean_text(bibjson.get("title")),
                "title_zh": "",
                "authors": compact_people(author_names),
                "year": clean_text(bibjson.get("year")),
                "publication_date": clean_text(bibjson.get("year")),
                "journal_or_source": clean_text(journal.get("title")) or "DOAJ",
                "doi": doi,
                "url": landing_url or doi_url(doi),
                "abstract_en": clean_text(bibjson.get("abstract")),
                "abstract_zh": "",
                "source": source,
            }
        )

        if len(items) >= max_results:
            break

    return items


def fetch_biorxiv_like(config: dict[str, Any], cutoff: date, end_date: date, server: str) -> list[dict[str, Any]]:
    source = server
    max_results = max_results_for_source(config, source)
    terms = source_terms(config, source)
    timeout = int(config.get("request_timeout_seconds", 25))
    max_pages = int(config.get("recent_feed_pages", {}).get(source, 3))
    base_url = "https://api.biorxiv.org" if server == "biorxiv" else "https://api.medrxiv.org"
    host = "www.biorxiv.org" if server == "biorxiv" else "www.medrxiv.org"
    cursor = 0
    items: list[dict[str, Any]] = []

    for _page in range(max_pages):
        response = http_get(
            config,
            f"{base_url}/details/{server}/{cutoff.isoformat()}/{end_date.isoformat()}/{cursor}",
            headers=request_headers(config),
            timeout=timeout,
        )
        response.raise_for_status()
        data = response.json()
        collection = data.get("collection", [])
        if not collection:
            break

        for record in collection:
            title = clean_text(record.get("title"))
            abstract = clean_text(record.get("abstract"))
            if not contains_any_term(f"{title} {abstract}", terms):
                continue

            doi = normalize_doi(record.get("doi"))
            version = clean_text(record.get("version"))
            version_suffix = f"v{version}" if version and not version.startswith("v") else version
            items.append(
                {
                    "title_en": title,
                    "title_zh": "",
                    "authors": clean_text(record.get("authors")),
                    "year": parse_year_text(record.get("date")),
                    "publication_date": clean_text(record.get("date")),
                    "journal_or_source": "bioRxiv" if server == "biorxiv" else "medRxiv",
                    "doi": doi,
                    "url": f"https://{host}/content/{doi}{version_suffix}" if doi else "",
                    "abstract_en": abstract,
                    "abstract_zh": "",
                    "source": source,
                }
            )

            if len(items) >= max_results:
                return items

        message = (data.get("messages") or [{}])[0]
        count = int(message.get("count", 0) or 0)
        total = int(message.get("total", 0) or 0)
        cursor += count
        if count <= 0 or (total and cursor >= total):
            break
        time.sleep(0.2)

    return items


def fetch_biorxiv(config: dict[str, Any], cutoff: date, end_date: date) -> list[dict[str, Any]]:
    return fetch_biorxiv_like(config, cutoff, end_date, "biorxiv")


def fetch_medrxiv(config: dict[str, Any], cutoff: date, end_date: date) -> list[dict[str, Any]]:
    return fetch_biorxiv_like(config, cutoff, end_date, "medrxiv")


def fetch_arxiv(config: dict[str, Any], cutoff: date) -> list[dict[str, Any]]:
    max_results = max_results_for_source(config, "arxiv")
    terms = source_terms(config, "arxiv")
    query = " OR ".join(f'all:"{term}"' for term in terms)
    timeout = int(config.get("request_timeout_seconds", 25))

    params = {
        "search_query": query,
        "start": 0,
        "max_results": max_results * 3,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
    }
    response = http_get(
        config,
        "https://export.arxiv.org/api/query",
        params=params,
        headers=request_headers(config),
        timeout=timeout,
    )
    response.raise_for_status()

    feed = feedparser.parse(response.text)
    items: list[dict[str, Any]] = []
    for entry in feed.entries:
        published = parse_iso_date(entry.get("published"))
        updated = parse_iso_date(entry.get("updated"))
        record_date = published or updated
        if record_date and record_date < cutoff:
            continue

        authors = [author.get("name", "") for author in entry.get("authors", []) if author.get("name")]
        doi = normalize_doi(entry.get("arxiv_doi") or entry.get("doi"))
        primary_category = entry.get("arxiv_primary_category", {}).get("term", "")
        source = f"arXiv {primary_category}".strip()

        items.append(
            {
                "title_en": clean_text(entry.get("title")),
                "title_zh": "",
                "authors": "; ".join(authors),
                "year": str(record_date.year) if record_date else "",
                "publication_date": record_date.isoformat() if record_date else "",
                "journal_or_source": source,
                "doi": doi,
                "url": entry.get("link", ""),
                "abstract_en": clean_text(entry.get("summary")),
                "abstract_zh": "",
                "source": "arxiv",
            }
        )

        if len(items) >= max_results:
            break

    return items


def crossref_work_to_item(work: dict[str, Any], source: str, source_label: str = "") -> dict[str, Any]:
    published = (
        work.get("published-print")
        or work.get("published-online")
        or work.get("published")
        or work.get("issued")
        or {}
    )
    publication_date = parse_date_from_date_parts(published)
    year = str(publication_date.year) if publication_date else parse_year_from_date_parts(published)
    authors = []
    for author in work.get("author", []):
        parts = [author.get("given", ""), author.get("family", "")]
        name = " ".join(part for part in parts if part).strip()
        if name:
            authors.append(name)

    container_title = first_text(work.get("container-title"))
    publisher = clean_text(work.get("publisher"))
    journal_or_source = container_title or source_label or publisher or "Crossref"
    if source_label and source_label.lower() not in journal_or_source.lower():
        journal_or_source = f"{journal_or_source} ({source_label})"

    doi = normalize_doi(work.get("DOI"))
    return {
        "title_en": first_text(work.get("title")),
        "title_zh": "",
        "authors": compact_people(authors),
        "year": year,
        "publication_date": publication_date.isoformat() if publication_date else "",
        "journal_or_source": journal_or_source,
        "doi": doi,
        "url": work.get("URL", "") or doi_url(doi),
        "abstract_en": clean_text(work.get("abstract")),
        "abstract_zh": "",
        "source": source,
    }


def crossref_publisher_match_terms(profile: dict[str, Any]) -> list[str]:
    raw_terms = profile.get("match_terms") or []
    terms = [clean_text(term) for term in raw_terms if clean_text(term)]
    if terms:
        return terms

    label = clean_text(profile.get("label"))
    container_query = clean_text(profile.get("container_query"))
    terms = [label] if label else []
    if container_query:
        terms.extend(
            token.strip()
            for token in re.split(r"\s{2,}|[,;|]", container_query)
            if token.strip()
        )
    return terms


def work_matches_publisher_profile(work: dict[str, Any], profile: dict[str, Any]) -> bool:
    member = profile.get("crossref_member")
    if member:
        return True

    terms = crossref_publisher_match_terms(profile)
    if not terms:
        return True

    haystack = " ".join(
        [
            first_text(work.get("container-title")),
            clean_text(work.get("publisher")),
        ]
    ).lower()
    return any(term.lower() in haystack for term in terms)


def fetch_crossref_publisher(
    config: dict[str, Any],
    cutoff: date,
    end_date: date,
    profile: dict[str, Any],
) -> list[dict[str, Any]]:
    profile_id = clean_text(profile.get("id")).lower()
    source = f"publisher_{profile_id}"
    max_results = max_results_for_source(config, source)
    terms = source_terms(config, source)
    timeout = int(config.get("request_timeout_seconds", 25))
    filters = [
        f"from-pub-date:{cutoff.isoformat()}",
        f"until-pub-date:{end_date.isoformat()}",
        "type:journal-article",
    ]
    member = clean_text(profile.get("crossref_member"))
    if member:
        filters.append(f"member:{member}")

    params = {
        "query.bibliographic": build_plain_query(terms),
        "filter": ",".join(filters),
        "sort": "published",
        "order": "desc",
        "rows": max_results * 4,
        "select": "DOI,title,author,published-print,published-online,published,issued,container-title,publisher,abstract,URL",
    }
    container_query = clean_text(profile.get("container_query"))
    if container_query:
        params["query.container-title"] = container_query

    response = http_get(
        config,
        "https://api.crossref.org/works",
        params=params,
        headers=request_headers(config),
        timeout=timeout,
    )
    response.raise_for_status()

    label = clean_text(profile.get("label"))
    items: list[dict[str, Any]] = []
    for work in response.json().get("message", {}).get("items", []):
        if not work_matches_publisher_profile(work, profile):
            continue
        items.append(crossref_work_to_item(work, source, label))
        if len(items) >= max_results:
            break

    return items


def fetch_crossref(config: dict[str, Any], cutoff: date, end_date: date) -> list[dict[str, Any]]:
    max_results = max_results_for_source(config, "crossref")
    terms = source_terms(config, "crossref")
    timeout = int(config.get("request_timeout_seconds", 25))
    params = {
        "query.bibliographic": " OR ".join(terms),
        "filter": f"from-pub-date:{cutoff.isoformat()},until-pub-date:{end_date.isoformat()}",
        "sort": "published",
        "order": "desc",
        "rows": max_results * 2,
        "select": "DOI,title,author,published-print,published-online,published,issued,container-title,abstract,URL",
    }
    response = http_get(
        config,
        "https://api.crossref.org/works",
        params=params,
        headers=request_headers(config),
        timeout=timeout,
    )
    response.raise_for_status()

    data = response.json()
    items: list[dict[str, Any]] = []
    for work in data.get("message", {}).get("items", []):
        items.append(crossref_work_to_item(work, "crossref"))

        if len(items) >= max_results:
            break

    return items


def pubmed_query(terms: list[str]) -> str:
    quoted = [f'"{term}"[Title/Abstract]' for term in terms]
    return " OR ".join(quoted)


def fetch_pubmed(config: dict[str, Any], cutoff: date, end_date: date) -> list[dict[str, Any]]:
    max_results = max_results_for_source(config, "pubmed")
    terms = source_terms(config, "pubmed")
    timeout = int(config.get("request_timeout_seconds", 25))
    headers = request_headers(config)

    search_params = {
        "db": "pubmed",
        "term": pubmed_query(terms),
        "retmode": "json",
        "retmax": max_results * 2,
        "sort": "pub date",
        "datetype": "pdat",
        "mindate": cutoff.isoformat(),
        "maxdate": end_date.isoformat(),
    }
    search_response = http_get(
        config,
        "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",
        params=search_params,
        headers=headers,
        timeout=timeout,
    )
    search_response.raise_for_status()
    ids = search_response.json().get("esearchresult", {}).get("idlist", [])
    if not ids:
        return []

    time.sleep(0.35)
    fetch_params = {
        "db": "pubmed",
        "id": ",".join(ids[: max_results * 2]),
        "retmode": "xml",
    }
    fetch_response = http_get(
        config,
        "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi",
        params=fetch_params,
        headers=headers,
        timeout=timeout,
    )
    fetch_response.raise_for_status()

    root = ET.fromstring(fetch_response.text)
    items: list[dict[str, Any]] = []
    for article in root.findall(".//PubmedArticle"):
        citation = article.find(".//MedlineCitation")
        article_node = citation.find("Article") if citation is not None else None
        if article_node is None:
            continue

        title = "".join(article_node.findtext("ArticleTitle", default=""))
        abstract_parts = [
            "".join(part.itertext())
            for part in article_node.findall(".//AbstractText")
        ]
        journal_title = article_node.findtext("./Journal/Title", default="PubMed")
        year = (
            article_node.findtext("./Journal/JournalIssue/PubDate/Year")
            or article_node.findtext("./Journal/JournalIssue/PubDate/MedlineDate", default="")[:4]
        )
        publication_date = parse_pubmed_publication_date(article_node)
        if publication_date is not None:
            year = str(publication_date.year)

        authors = []
        for author in article_node.findall(".//Author"):
            last = author.findtext("LastName", default="")
            fore = author.findtext("ForeName", default="")
            collective = author.findtext("CollectiveName", default="")
            name = " ".join(part for part in [fore, last] if part).strip() or collective
            if name:
                authors.append(name)

        doi = ""
        for article_id in article.findall(".//ArticleId"):
            if article_id.attrib.get("IdType") == "doi":
                doi = normalize_doi(article_id.text)
                break

        pmid = article.findtext(".//PMID", default="")
        url = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else ""

        items.append(
            {
                "title_en": clean_text(title),
                "title_zh": "",
                "authors": "; ".join(authors),
                "year": year,
                "publication_date": publication_date.isoformat() if publication_date else "",
                "journal_or_source": clean_text(journal_title),
                "doi": doi,
                "url": url,
                "abstract_en": clean_text(" ".join(abstract_parts)),
                "abstract_zh": "",
                "source": "pubmed",
            }
        )

        if len(items) >= max_results:
            break

    return items


def dedupe_and_filter(
    raw_items: list[dict[str, Any]],
    seen: dict[str, set[str]],
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    min_score = float(config.get("min_relevance_score", 1.0))
    keywords = build_keyword_map(config)
    current_doi: set[str] = set()
    current_title: set[str] = set()
    results: list[dict[str, Any]] = []

    for raw_item in raw_items:
        item = score_item(raw_item, keywords, config)
        if item["relevance_score"] < min_score:
            continue

        doi_key = normalize_doi(item.get("doi"))
        title_key = normalize_title(item.get("title_en") or item.get("title"))

        if doi_key and doi_key in current_doi:
            continue
        if not doi_key and title_key and title_key in current_title:
            continue

        item["previously_seen"] = bool(
            (doi_key and doi_key in seen["doi"])
            or (not doi_key and title_key and title_key in seen["title"])
        )

        if doi_key:
            current_doi.add(doi_key)
        elif title_key:
            current_title.add(title_key)

        results.append({field: item.get(field, "") for field in OUTPUT_FIELDS})

    results.sort(key=lambda value: (item_publication_date(value), item_relevance_score(value)), reverse=True)
    return results


def update_seen_with_results(seen: dict[str, set[str]], results: list[dict[str, Any]]) -> None:
    for item in results:
        doi = normalize_doi(item.get("doi"))
        title = normalize_title(item.get("title_en") or item.get("title"))
        if doi:
            seen["doi"].add(doi)
        elif title:
            seen["title"].add(title)


def fetch_all(config: dict[str, Any], cutoff: date, end_date: date) -> tuple[list[dict[str, Any]], dict[str, int]]:
    source_counts: dict[str, int] = {}
    all_items: list[dict[str, Any]] = []

    source_fetchers = {
        "arxiv": lambda: fetch_arxiv(config, cutoff),
        "crossref": lambda: fetch_crossref(config, cutoff, end_date),
        "openalex": lambda: fetch_openalex(config, cutoff, end_date),
        "semantic_scholar": lambda: fetch_semantic_scholar(config, cutoff, end_date),
        "pubmed": lambda: fetch_pubmed(config, cutoff, end_date),
        "europe_pmc": lambda: fetch_europe_pmc(config, cutoff, end_date),
        "doaj": lambda: fetch_doaj(config, cutoff, end_date),
        "biorxiv": lambda: fetch_biorxiv(config, cutoff, end_date),
        "medrxiv": lambda: fetch_medrxiv(config, cutoff, end_date),
    }

    configured_sources = enabled_sources(config)
    expanded_fetchers: list[tuple[str, Any]] = []
    for source in configured_sources:
        if source == "publishers":
            for profile in publisher_profiles(config):
                profile_id = clean_text(profile.get("id")).lower()
                if profile_id:
                    expanded_fetchers.append(
                        (
                            f"publisher_{profile_id}",
                            lambda profile=profile: fetch_crossref_publisher(config, cutoff, end_date, profile),
                        )
                    )
            continue

        fetcher = source_fetchers.get(source)
        if fetcher is None:
            print(f"[WARN] Unknown source skipped: {source}")
            continue
        expanded_fetchers.append((source, fetcher))

    for source, fetcher in expanded_fetchers:
        try:
            items = fetcher()
        except Exception as error:
            print(f"[WARN] {source} failed: {error}")
            items = []
        source_counts[source] = len(items)
        all_items.extend(items)

    return all_items, source_counts


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Search literature databases and publisher metadata indexes for recent literature.")
    parser.add_argument("--config", default="config.yaml", help="Path to config.yaml.")
    parser.add_argument("--lookback-days", type=int, default=None, help="Override lookback_days from config.")
    parser.add_argument("--date", default=None, help="Report date in YYYY-MM-DD format. Defaults to today's local date.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = load_config(Path(args.config))
    data_dir, seen_file, latest_file = ensure_data_files(config)

    end_date = date.fromisoformat(args.date) if args.date else today_local()
    lookback_days = args.lookback_days or int(config.get("lookback_days", 3))
    cutoff = end_date - timedelta(days=lookback_days)

    seen = load_seen(seen_file)
    raw_items, source_counts = fetch_all(config, cutoff, end_date)
    results = dedupe_and_filter(raw_items, seen, config)
    update_seen_with_results(seen, results)
    save_seen(seen_file, seen)

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "report_date": end_date.isoformat(),
        "lookback_days": lookback_days,
        "cutoff_date": cutoff.isoformat(),
        "sources": source_counts,
        "items": results,
    }

    dated_file = data_dir / f"{end_date.isoformat()}_results.json"
    save_json(dated_file, payload)
    save_json(latest_file, payload)

    print(f"Saved {len(results)} candidate items to {dated_file}")
    print(f"Updated latest results at {latest_file}")


if __name__ == "__main__":
    main()
