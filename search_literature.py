from __future__ import annotations

import argparse
import json
import re
import time
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import feedparser
import requests
import yaml

from tencent_translation import load_tencent_translation_config, maybe_translate_items


OUTPUT_FIELDS = [
    "title_en",
    "title_zh",
    "authors",
    "year",
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


def parse_year_from_date_parts(parts: Any) -> str:
    try:
        return str(parts["date-parts"][0][0])
    except (KeyError, IndexError, TypeError):
        return ""


def parse_iso_date(value: str | None) -> date | None:
    if not value:
        return None

    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        return None


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
    if configured:
        return [str(term).strip() for term in configured if str(term).strip()]

    return [term for term, _weight in build_keyword_map(config)]


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


def fetch_arxiv(config: dict[str, Any], cutoff: date) -> list[dict[str, Any]]:
    max_results = int(config.get("max_results_per_source", {}).get("arxiv", 30))
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
    response = requests.get(
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


def fetch_crossref(config: dict[str, Any], cutoff: date, end_date: date) -> list[dict[str, Any]]:
    max_results = int(config.get("max_results_per_source", {}).get("crossref", 30))
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
    response = requests.get(
        "https://api.crossref.org/works",
        params=params,
        headers=request_headers(config),
        timeout=timeout,
    )
    response.raise_for_status()

    data = response.json()
    items: list[dict[str, Any]] = []
    for work in data.get("message", {}).get("items", []):
        published = (
            work.get("published-print")
            or work.get("published-online")
            or work.get("published")
            or work.get("issued")
            or {}
        )
        year = parse_year_from_date_parts(published)
        authors = []
        for author in work.get("author", []):
            parts = [author.get("given", ""), author.get("family", "")]
            name = " ".join(part for part in parts if part).strip()
            if name:
                authors.append(name)

        items.append(
            {
                "title_en": first_text(work.get("title")),
                "title_zh": "",
                "authors": "; ".join(authors),
                "year": year,
                "journal_or_source": first_text(work.get("container-title")) or "Crossref",
                "doi": normalize_doi(work.get("DOI")),
                "url": work.get("URL", ""),
                "abstract_en": clean_text(work.get("abstract")),
                "abstract_zh": "",
                "source": "crossref",
            }
        )

        if len(items) >= max_results:
            break

    return items


def pubmed_query(terms: list[str]) -> str:
    quoted = [f'"{term}"[Title/Abstract]' for term in terms]
    return " OR ".join(quoted)


def fetch_pubmed(config: dict[str, Any], cutoff: date, end_date: date) -> list[dict[str, Any]]:
    max_results = int(config.get("max_results_per_source", {}).get("pubmed", 30))
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
    search_response = requests.get(
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
    fetch_response = requests.get(
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

    results.sort(key=lambda value: float(value.get("relevance_score", 0)), reverse=True)
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

    source_fetchers = [
        ("arxiv", lambda: fetch_arxiv(config, cutoff)),
        ("crossref", lambda: fetch_crossref(config, cutoff, end_date)),
        ("pubmed", lambda: fetch_pubmed(config, cutoff, end_date)),
    ]

    for source, fetcher in source_fetchers:
        try:
            items = fetcher()
        except Exception as error:
            print(f"[WARN] {source} failed: {error}")
            items = []
        source_counts[source] = len(items)
        all_items.extend(items)

    return all_items, source_counts


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Search arXiv, Crossref, and PubMed for recent literature.")
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
    translation_cache_path = data_dir / "translation_cache.json"
    results = maybe_translate_items(results, load_tencent_translation_config(cache_path=translation_cache_path))
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
