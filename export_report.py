from __future__ import annotations

import argparse
import csv
import json
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from tencent_translation import load_tencent_translation_config, maybe_translate_items


ITEM_FIELDS = [
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

CSV_FIELDS = ["date", *ITEM_FIELDS]

TABLE_COLUMNS = [
    "序号",
    "英文题目",
    "中文题目",
    "年份",
    "期刊 / 来源",
    "DOI",
    "链接",
    "关键词命中",
    "是否以前出现过",
]


def load_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as file:
        return yaml.safe_load(file) or {}


def load_results(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        payload = json.load(file)

    if isinstance(payload, list):
        return {"report_date": date.today().isoformat(), "items": payload}
    return payload


def save_results(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def same_path(left: Path, right: Path) -> bool:
    return left.resolve() == right.resolve()


def save_translated_results(
    input_path: Path,
    config: dict[str, Any],
    payload: dict[str, Any],
    items: list[dict[str, Any]],
) -> None:
    payload["items"] = items
    report_date = text_value(payload.get("report_date", "")).strip()
    paths = config.get("paths", {})
    data_dir = Path(paths.get("data_dir", "data"))
    latest_path = data_dir / paths.get("latest_results_file", "latest_results.json")

    output_paths = [input_path]
    if report_date:
        dated_path = data_dir / f"{report_date}_results.json"
        if same_path(input_path, latest_path) and dated_path.exists():
            output_paths.append(dated_path)
        elif same_path(input_path, dated_path) and latest_path.exists():
            output_paths.append(latest_path)

    seen_paths: set[str] = set()
    for path in output_paths:
        key = str(path.resolve()).lower()
        if key in seen_paths:
            continue
        save_results(path, payload)
        seen_paths.add(key)


def ensure_reports_dir(config: dict[str, Any]) -> Path:
    reports_dir = Path(config.get("paths", {}).get("reports_dir", "reports"))
    reports_dir.mkdir(parents=True, exist_ok=True)
    return reports_dir


def text_value(value: Any) -> str:
    if isinstance(value, list):
        return "; ".join(str(item) for item in value)
    if value is None:
        return ""
    return str(value)


def bool_value(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    text = text_value(value).strip().lower()
    return text in {"1", "true", "yes", "y", "是", "previously_seen"}


def seen_text(item: dict[str, Any]) -> str:
    return "是" if bool_value(item.get("previously_seen", False)) else "否"


def normalize_item(item: dict[str, Any]) -> dict[str, Any]:
    normalized = {field: text_value(item.get(field, "")) for field in ITEM_FIELDS}
    normalized["title_en"] = normalized["title_en"] or text_value(item.get("title", ""))
    normalized["abstract_en"] = normalized["abstract_en"] or text_value(item.get("abstract", ""))
    normalized["previously_seen"] = item.get("previously_seen", False)
    return normalized


def unique_dois(items: list[dict[str, Any]]) -> list[str]:
    seen: set[str] = set()
    dois: list[str] = []
    for item in items:
        doi = text_value(item.get("doi", "")).strip()
        key = doi.lower()
        if not doi or key in seen:
            continue
        seen.add(key)
        dois.append(doi)
    return dois


def markdown_cell(value: Any) -> str:
    return text_value(value).replace("\n", " ").replace("|", "\\|").strip()


def item_text_blob(item: dict[str, Any]) -> str:
    parts = [
        item.get("title_en", ""),
        item.get("abstract_en", ""),
        item.get("matched_keywords", ""),
        item.get("reason", ""),
    ]
    return " ".join(text_value(part) for part in parts).lower()


def has_any(text: str, needles: list[str]) -> bool:
    return any(needle.lower() in text for needle in needles)


def relevance_reasons(item: dict[str, Any]) -> list[str]:
    text = item_text_blob(item)
    organic_terms = [
        "organic molecule",
        "dye",
        "chromophore",
        "photosensitizer",
        "molecular antenna",
        "organic antenna",
        "dye-sensitized",
    ]
    gold_terms = [
        "gold nanoparticle",
        "gold nanoparticles",
        "au nanoparticle",
        "au nanoparticles",
        "au nanorod",
        "gold nanorod",
        "gold nanostar",
        "gold nanoshell",
        "gold shell",
        "gold film",
        "gold nanoarray",
        "plasmonic gold",
        "gold nanostructure",
    ]
    plasmon_terms = [
        "plasmon",
        "lspr",
        "localized surface plasmon resonance",
        "plasmon-mediated energy transfer",
        "plasmon-enhanced luminescence",
        "plasmonic enhancement",
    ]
    lanthanide_terms = [
        "lanthanide",
        "rare-earth",
        "upconversion nanoparticle",
        "upconversion nanoparticles",
        "ucnp",
        "naerf4",
        "nayf4",
        "er3+",
        "er 3+",
        "erbium",
    ]
    reasons: list[str] = []

    if has_any(text, organic_terms) and has_any(text, gold_terms):
        reasons.append("包含 organic molecule / dye 与 gold nanoparticle coupling 相关关键词")
    if "plasmon-mediated energy transfer" in text or (has_any(text, plasmon_terms) and "energy transfer" in text):
        reasons.append("包含 plasmon-mediated energy transfer 或 plasmonic energy-transfer 线索")
    if has_any(text, plasmon_terms) and has_any(text, lanthanide_terms):
        reasons.append("包含 plasmon-enhanced lanthanide luminescence / upconversion 相关线索")
    if has_any(text, gold_terms):
        reasons.append("包含 gold nanorod / Au nanoparticle / plasmonic gold nanostructure 相关线索")
    if has_any(text, organic_terms) and has_any(text, lanthanide_terms):
        reasons.append("包含 organic antenna sensitization of lanthanide nanoparticles 线索")
    if "molecule-to-metal energy transfer" in text:
        reasons.append("包含 molecule-to-metal energy transfer")
    if "metal-to-lanthanide energy transfer" in text:
        reasons.append("包含 metal-to-lanthanide energy transfer")
    if "plasmon-exciton coupling" in text:
        reasons.append("包含 plasmon-exciton coupling")
    if "4f" in text or "energy level" in text:
        reasons.append("包含 Er3+ specific energy level / 4f transition / lanthanide energy-transfer 线索")

    if not reasons:
        matched = text_value(item.get("matched_keywords", "")).strip()
        if matched:
            reasons.append(f"关键词命中：{matched}")
        else:
            existing_reason = text_value(item.get("reason", "")).strip()
            reasons.append(existing_reason or "根据题目或摘要命中的宽关键词列为候选")

    return reasons[:6]


def write_csv(path: Path, report_date: str, items: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for item in items:
            row = {"date": report_date}
            for field in ITEM_FIELDS:
                row[field] = seen_text(item) if field == "previously_seen" else text_value(item.get(field, ""))
            writer.writerow(row)


def write_markdown(path: Path, payload: dict[str, Any], items: list[dict[str, Any]]) -> None:
    report_date = payload.get("report_date", date.today().isoformat())
    generated_at = payload.get("generated_at", "")
    lookback_days = payload.get("lookback_days", "")
    cutoff_date = payload.get("cutoff_date", "")
    sources = payload.get("sources", {})
    dois = unique_dois(items)

    lines = [
        f"# 每日文献检索报告 - {report_date}",
        "",
        f"生成时间：{generated_at}",
        f"检索窗口：最近 {lookback_days} 天，起始日期 {cutoff_date}",
        f"候选文献数：{len(items)}",
        f"来源抓取数：arXiv={sources.get('arxiv', 0)}, Crossref={sources.get('crossref', 0)}, PubMed={sources.get('pubmed', 0)}",
        "",
        "## 一、今日 DOI 清单",
        "",
    ]

    if dois:
        lines.extend(dois)
    else:
        lines.append("No DOI available")

    lines.extend(
        [
            "",
            "## 二、候选文献总表",
            "",
        ]
    )

    if items:
        lines.append("| " + " | ".join(TABLE_COLUMNS) + " |")
        lines.append("| " + " | ".join("---" for _ in TABLE_COLUMNS) + " |")
        for index, item in enumerate(items, start=1):
            row = [
                str(index),
                markdown_cell(item.get("title_en", "")),
                markdown_cell(item.get("title_zh", "")),
                markdown_cell(item.get("year", "")),
                markdown_cell(item.get("journal_or_source", "")),
                markdown_cell(item.get("doi", "")),
                markdown_cell(item.get("url", "")),
                markdown_cell(item.get("matched_keywords", "")),
                seen_text(item),
            ]
            lines.append("| " + " | ".join(row) + " |")
    else:
        lines.append("No candidate literature was found.")

    lines.extend(["", "## 三、详细文献信息", ""])

    if not items:
        lines.append("No candidate literature was found.")
    for index, item in enumerate(items, start=1):
        title_en = text_value(item.get("title_en", "")).strip() or "(No English title)"
        lines.extend(
            [
                f"### {index}. {title_en}",
                "",
                f"中文题目：{text_value(item.get('title_zh', ''))}",
                f"DOI：{text_value(item.get('doi', ''))}",
                f"链接：{text_value(item.get('url', ''))}",
                f"年份：{text_value(item.get('year', ''))}",
                f"期刊 / 来源：{text_value(item.get('journal_or_source', ''))}",
                f"作者：{text_value(item.get('authors', ''))}",
                f"关键词命中：{text_value(item.get('matched_keywords', ''))}",
                f"是否以前出现过：{seen_text(item)}",
                "",
                "英文摘要：",
                "",
                text_value(item.get("abstract_en", "")),
                "",
                "中文摘要：",
                "",
                text_value(item.get("abstract_zh", "")),
                "",
                "可能相关原因：",
            ]
        )
        lines.extend(f"- {reason}" for reason in relevance_reasons(item))
        lines.extend(["", "---", ""])

    path.write_text("\n".join(lines), encoding="utf-8")


def write_field(document: Any, label: str, value: Any) -> None:
    paragraph = document.add_paragraph()
    label_run = paragraph.add_run(f"{label}：")
    label_run.bold = True
    paragraph.add_run(text_value(value))


def write_word(path: Path, report_date: str, items: list[dict[str, Any]]) -> None:
    try:
        from docx import Document
    except ImportError as error:
        raise RuntimeError("python-docx is required to generate Word reports. Run: pip install -r requirements.txt") from error

    document = Document()
    document.add_heading(f"每日文献检索报告 - {report_date}", level=0)

    document.add_heading("一、今日 DOI 清单", level=1)
    dois = unique_dois(items)
    if dois:
        for doi in dois:
            document.add_paragraph(doi)
    else:
        document.add_paragraph("No DOI available")

    document.add_heading("二、候选文献总表", level=1)
    if items:
        table = document.add_table(rows=1, cols=len(TABLE_COLUMNS))
        table.style = "Table Grid"
        table.autofit = True
        header_cells = table.rows[0].cells
        for column_index, column_name in enumerate(TABLE_COLUMNS):
            header_cells[column_index].text = column_name

        for index, item in enumerate(items, start=1):
            cells = table.add_row().cells
            values = [
                str(index),
                text_value(item.get("title_en", "")),
                text_value(item.get("title_zh", "")),
                text_value(item.get("year", "")),
                text_value(item.get("journal_or_source", "")),
                text_value(item.get("doi", "")),
                text_value(item.get("url", "")),
                text_value(item.get("matched_keywords", "")),
                seen_text(item),
            ]
            for column_index, value in enumerate(values):
                cells[column_index].text = value
    else:
        document.add_paragraph("No candidate literature was found.")

    document.add_heading("三、详细文献信息", level=1)
    if not items:
        document.add_paragraph("No candidate literature was found.")

    for index, item in enumerate(items, start=1):
        title_en = text_value(item.get("title_en", "")).strip() or "(No English title)"
        document.add_heading(f"{index}. {title_en}", level=2)
        write_field(document, "中文题目", item.get("title_zh", ""))
        write_field(document, "DOI", item.get("doi", ""))
        write_field(document, "链接", item.get("url", ""))
        write_field(document, "年份", item.get("year", ""))
        write_field(document, "期刊 / 来源", item.get("journal_or_source", ""))
        write_field(document, "作者", item.get("authors", ""))
        write_field(document, "关键词命中", item.get("matched_keywords", ""))
        write_field(document, "是否以前出现过", seen_text(item))

        document.add_paragraph("英文摘要：")
        document.add_paragraph(text_value(item.get("abstract_en", "")))
        document.add_paragraph("中文摘要：")
        document.add_paragraph(text_value(item.get("abstract_zh", "")))
        document.add_paragraph("可能相关原因：")
        for reason in relevance_reasons(item):
            document.add_paragraph(reason, style="List Bullet")

    document.save(path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export literature search results to Word, Markdown, and CSV.")
    parser.add_argument("--config", default="config.yaml", help="Path to config.yaml.")
    parser.add_argument("--input", default=None, help="Input JSON file. Defaults to data/latest_results.json.")
    parser.add_argument("--date", default=None, help="Override report date in YYYY-MM-DD format.")
    parser.add_argument("--no-translate", action="store_true", help="Do not use optional Tencent Cloud translation even if .env has Tencent Cloud credentials.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = load_config(Path(args.config))
    paths = config.get("paths", {})
    input_path = Path(args.input or Path(paths.get("data_dir", "data")) / paths.get("latest_results_file", "latest_results.json"))
    payload = load_results(input_path)

    report_date = args.date or payload.get("report_date") or date.today().isoformat()
    payload["report_date"] = report_date
    items = [normalize_item(item) for item in payload.get("items", [])]
    translation_cache_path = Path(paths.get("data_dir", "data")) / "translation_cache.json"
    translation_config = None if args.no_translate else load_tencent_translation_config(cache_path=translation_cache_path)
    items = maybe_translate_items(items, translation_config)
    save_translated_results(input_path, config, payload, items)

    reports_dir = ensure_reports_dir(config)
    word_path = reports_dir / f"{report_date}_daily_literature.docx"
    markdown_path = reports_dir / f"{report_date}_daily_literature.md"
    csv_path = reports_dir / f"{report_date}_daily_literature.csv"

    write_word(word_path, report_date, items)
    write_markdown(markdown_path, payload, items)
    write_csv(csv_path, report_date, items)

    print(f"Saved Word report to {word_path}")
    print(f"Saved Markdown report to {markdown_path}")
    print(f"Saved CSV report to {csv_path}")


if __name__ == "__main__":
    main()
