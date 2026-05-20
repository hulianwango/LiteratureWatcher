# LiteratureWatcher

LiteratureWatcher performs daily automated literature searches from major metadata databases, biomedical/preprint indexes, open-access indexes, and publisher-specific Crossref searches, then updates one cumulative Word-only report for DOI copying and manual screening.

Google Scholar is not used.

Default sources now include Crossref, OpenAlex, Semantic Scholar, PubMed, Europe PMC, arXiv, DOAJ, bioRxiv, and medRxiv. The `publishers` source expands into publisher-specific searches for Nature Portfolio, Springer Nature, Science / AAAS, ACS, RSC, Wiley, Elsevier, Taylor & Francis / Informa, IEEE, Optica, IOP, AIP, MDPI, Frontiers, PLOS, SAGE, Oxford University Press, and Cambridge University Press. These publisher searches retrieve metadata and links; they do not require PDF download access.

## Current Topic

The default query is tuned for broad discovery around organic molecules, dyes, chromophores, photosensitizers, molecular or organic antennas, plasmonic gold nanostructures, and lanthanide-doped or rare-earth nanocrystals. It intentionally keeps the relevance threshold loose so that potentially useful papers are not dropped too early.

Representative mechanisms include:

- organic molecule / dye / photosensitizer coupling with gold nanoparticles
- plasmon-mediated or molecule-to-metal energy transfer
- metal-to-lanthanide energy transfer
- plasmon-enhanced lanthanide luminescence or upconversion
- Au nanoparticle / Au nanorod / gold nanostar / gold nanoshell / gold nanoarray coupling with NaErF4, NaYF4:Er, NaYF4:Yb,Er, Er3+, or lanthanide-doped nanoparticles

## Files

- `config.yaml`: Search windows, enabled sources, publisher search profiles, source limits, broad topic keywords, scoring weights, and output paths.
- `config_editor.py`: Tkinter desktop editor for search terms, weights, recent-day window, and publication year range.
- `literature_watcher.py`: Unified executable-friendly launcher for daily search, export-only runs, and the settings editor.
- `app_paths.py`: Shared path helper so scripts and the packaged executable find `config.yaml`, `data/`, and `reports/` from the application folder.
- `search_literature.py`: Queries the configured literature databases and publisher metadata indexes; scores candidates; deduplicates within the current run by DOI or title; marks `previously_seen`; saves JSON results.
- `export_report.py`: Converts JSON results into the Word report and optionally fills Chinese translations during export.
- `tencent_translation.py`: Reads optional Tencent Cloud translation settings from `.env`, translates titles and abstracts into Simplified Chinese, and caches translations.
- `requirements.txt`: Python packages required by the project.
- `requirements-build.txt`: Optional PyInstaller build dependency.
- `run_config_editor.bat`: Windows one-click script for opening the search settings editor.
- `run_daily.bat`: Windows one-click script that runs search first, then exports reports.
- `run_daily_auto.bat`: Non-interactive Windows script for scheduled runs; it writes progress to `logs/daily_literature.log`.
- `data/`: Stores `seen_items.json`, dated JSON result files, `latest_results.json`, and `translation_cache.json`.
- `reports/`: Stores the cumulative `.docx` report.

## Install

Use Python 3.10 or newer.

```powershell
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
```

## Daily Run

```powershell
.\run_daily.bat
```

This runs:

1. `python search_literature.py --config config.yaml`
2. `python export_report.py --config config.yaml --input data\latest_results.json`

The same workflow can also be started through the unified launcher:

```powershell
python literature_watcher.py
```

Useful launcher options:

```powershell
python literature_watcher.py --export-only --no-translate
python literature_watcher.py --edit-config
python literature_watcher.py --lookback-days 1 --no-pause
```

The default daily search window is the most recent 7 days. After that daily search, the run also performs a configurable extended search for related literature from the most recent 15 years and stores those results separately as `historical_items`.

## Search Settings Editor

For non-technical users, double-click:

```powershell
.\run_config_editor.bat
```

The editor opens a local desktop window. Use it to:

- add a search term with a positive weight
- select an existing term, edit the text or weight, then click `更新选中`
- click `全选` to select every term
- select one or more existing terms and click `删除选中`
- set `最近检索天数`
- enable `启用发表年份范围`, then enter `开始年` and `结束年`
- click `保存到 config.yaml`

When search terms are saved, the editor updates both `keywords` and every `source_query_terms` list in `config.yaml`, so the displayed terms affect both database search and relevance scoring.

When `publication_year_range.enabled` is true, `search_literature.py` searches from `start_year-01-01` through `end_year-12-31`. In this mode the separate `historical_search` pass is skipped to avoid retrieving the same range twice. Passing `--lookback-days` on the command line overrides the year range for that run.

## Outputs

Each daily run updates a single cumulative report instead of creating a new Word file every day:

- `data/YYYY-MM-DD_results.json`
- `data/latest_results.json`
- `data/cumulative_results.json`
- `reports/literature_report.docx`
- updated `data/seen_items.json`

When `historical_search.enabled` is true, the dated and latest JSON files also include:

- `historical_lookback_years`
- `historical_cutoff_date`
- `historical_sources`
- `historical_items`

When `publication_year_range.enabled` is true, the dated and latest JSON files also include `publication_year_range`, and the Word report overview shows the selected year range.

The Word report is sorted by first-seen date in descending order. Items in the same date bucket are sorted by relevance score and publication date in descending order. It has these main sections:

- 检索概览: generation time, search window, candidate count, and source fetch counts
- 累计 DOI 清单: first-seen date, publication date, and DOI
- 候选文献总表: first-seen date, publication date, title, source, DOI, link, matched keywords, relevance score, and whether the paper appeared before
- 详细文献信息: English and Chinese metadata, abstracts, and simple keyword-based relevance reasons
- 近15年相关文献: extended-search summary table sorted by relevance score and publication date

## Optional Commands

Run search only:

```powershell
python search_literature.py
```

Override the search window or report date:

```powershell
python search_literature.py --lookback-days 7
python search_literature.py --date 2026-05-17
python search_literature.py --config config.yaml
```

Export the Word report from the latest JSON:

```powershell
python export_report.py
```

Export a specific JSON file:

```powershell
python export_report.py --input data\2026-05-17_results.json
```

Skip translation during export:

```powershell
python export_report.py --no-translate
```

## Build Executable

Install the optional build dependency:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
```

Build the main executable:

```powershell
.\.venv\Scripts\python.exe -m PyInstaller --onefile --name LiteratureWatcher literature_watcher.py
```

Copy `config.yaml` next to `dist\LiteratureWatcher.exe` before running it. Double-clicking the executable opens the search settings editor; command-line options such as `--search-only`, `--export-only`, or `--lookback-days` run the search/export workflow. The executable reads and writes `data\` and `reports\` beside that config file.

## Optional Tencent Cloud Translation

Create a `.env` file in the project root if you want automatic Chinese titles and abstracts:

```text
TENCENTCLOUD_SECRET_ID=your_secret_id_here
TENCENTCLOUD_SECRET_KEY=your_secret_key_here
TENCENTCLOUD_REGION=ap-guangzhou
TENCENT_TRANSLATE_SOURCE=en
TENCENT_TRANSLATE_TARGET=zh
```

Only `TENCENTCLOUD_SECRET_ID` and `TENCENTCLOUD_SECRET_KEY` are required. If `.env` does not exist or either value is blank, `title_zh` and `abstract_zh` stay empty and the scripts continue normally.

Do not put your real Tencent Cloud credentials in source code or share them in committed files.

`run_daily.bat` automatically uses `.venv\Scripts\python.exe` when the virtual environment exists, so the installed Tencent Cloud SDK is available during translation. Translation results are written back to the input JSON during export, which allows interrupted runs to continue from cached translations.

Translations are cached in `data/translation_cache.json`, so repeated titles and abstracts are reused without another API call. The cache stores source text and Chinese translations only; it does not store Tencent Cloud credentials.

## Deduplication And Seen Items

The current run is deduplicated first by DOI. If no DOI is available, normalized title is used.

`data/seen_items.json` no longer suppresses candidates from the report. It only marks whether each candidate was previously seen through the `previously_seen` field, so each run still reflects the actual candidates found that day before they are merged into the cumulative report.
