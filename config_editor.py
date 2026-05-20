from __future__ import annotations

import copy
import re
import tkinter as tk
from datetime import date
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Any

import yaml

from app_paths import resolve_config_path, setup_utf8_console

CONFIG_PATH = resolve_config_path("config.yaml")
DEFAULT_WEIGHT = 3.0


def load_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        return yaml.safe_load(file) or {}


def save_config(config: dict[str, Any], path: Path = CONFIG_PATH) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as file:
        yaml.safe_dump(config, file, allow_unicode=True, sort_keys=False, width=120)


def clean_term(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def normalize_term(value: str) -> str:
    return clean_term(value).casefold()


def format_weight(value: float) -> str:
    return f"{value:g}"


def parse_positive_weight(value: Any) -> float:
    try:
        weight = float(str(value).strip())
    except (TypeError, ValueError) as error:
        raise ValueError("权重必须是数字。") from error

    if weight <= 0:
        raise ValueError("权重必须大于 0。")
    return weight


def parse_year(value: Any, label: str) -> int:
    text = str(value or "").strip()
    if not re.fullmatch(r"\d{4}", text):
        raise ValueError(f"{label}必须是 4 位年份，例如 2020。")
    return int(text)


def parse_positive_int(value: Any, label: str) -> int:
    text = str(value or "").strip()
    if not re.fullmatch(r"\d+", text):
        raise ValueError(f"{label}必须是正整数。")

    parsed = int(text)
    if parsed <= 0:
        raise ValueError(f"{label}必须大于 0。")
    return parsed


def coerce_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on", "是"}


def coerce_weight(value: Any) -> float:
    try:
        return parse_positive_weight(value)
    except ValueError:
        return DEFAULT_WEIGHT


def yaml_number(value: float) -> int | float:
    return int(value) if value.is_integer() else value


def extract_keyword_rows(config: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    raw_keywords = config.get("keywords", [])

    if isinstance(raw_keywords, list):
        for item in raw_keywords:
            if isinstance(item, dict):
                term = clean_term(item.get("term"))
                weight = coerce_weight(item.get("weight", DEFAULT_WEIGHT))
            else:
                term = clean_term(item)
                weight = DEFAULT_WEIGHT

            key = normalize_term(term)
            if not term or key in seen:
                continue
            rows.append({"term": term, "weight": weight})
            seen.add(key)

    if rows:
        return rows

    raw_source_terms = config.get("source_query_terms", {})
    if isinstance(raw_source_terms, dict):
        for terms in raw_source_terms.values():
            if not isinstance(terms, list):
                continue
            for term_value in terms:
                term = clean_term(term_value)
                key = normalize_term(term)
                if not term or key in seen:
                    continue
                rows.append({"term": term, "weight": DEFAULT_WEIGHT})
                seen.add(key)

    return rows


def lookback_days_default(config: dict[str, Any]) -> int:
    try:
        return max(1, int(config.get("lookback_days", 3)))
    except (TypeError, ValueError):
        return 3


def publication_year_range_defaults(config: dict[str, Any], current_year: int | None = None) -> tuple[bool, int, int]:
    current_year = current_year or date.today().year
    options = config.get("publication_year_range", {})
    options = options if isinstance(options, dict) else {}
    enabled = coerce_bool(options.get("enabled"), False)

    try:
        start_year = int(options.get("start_year"))
    except (TypeError, ValueError):
        historical = config.get("historical_search", {})
        lookback = historical.get("lookback_years", 15) if isinstance(historical, dict) else 15
        try:
            start_year = current_year - max(1, int(lookback)) + 1
        except (TypeError, ValueError):
            start_year = current_year - 14

    try:
        end_year = int(options.get("end_year"))
    except (TypeError, ValueError):
        end_year = current_year

    return enabled, start_year, end_year


def validate_keywords(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not rows:
        raise ValueError("请至少保留一个检索词。")

    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        term = clean_term(row.get("term"))
        if not term:
            raise ValueError("检索词不能为空。")

        key = normalize_term(term)
        if key in seen:
            raise ValueError(f"检索词重复：{term}")
        seen.add(key)

        normalized.append({"term": term, "weight": parse_positive_weight(row.get("weight"))})

    return normalized


def validate_year_range(start_value: Any, end_value: Any, current_year: int | None = None) -> tuple[int, int]:
    current_year = current_year or date.today().year
    start_year = parse_year(start_value, "开始年")
    end_year = parse_year(end_value, "结束年")

    if start_year > end_year:
        raise ValueError("开始年不能晚于结束年。")
    if end_year > current_year:
        raise ValueError(f"结束年不能超过当前年份 {current_year}。")
    return start_year, end_year


def validate_lookback_days(value: Any) -> int:
    return parse_positive_int(value, "最近检索天数")


def sync_source_query_terms(config: dict[str, Any], terms: list[str]) -> None:
    shared_terms = list(terms)
    source_query_terms = config.get("source_query_terms")
    if not isinstance(source_query_terms, dict) or not source_query_terms:
        config["source_query_terms"] = {"default": shared_terms}
        return

    for source in list(source_query_terms.keys()):
        source_query_terms[source] = shared_terms


def build_updated_config(
    config: dict[str, Any],
    rows: list[dict[str, Any]],
    year_range_enabled: bool,
    start_year: Any,
    end_year: Any,
    *,
    lookback_days: Any | None = None,
    current_year: int | None = None,
) -> dict[str, Any]:
    normalized_rows = validate_keywords(rows)
    normalized_start_year, normalized_end_year = validate_year_range(start_year, end_year, current_year)
    normalized_lookback_days = validate_lookback_days(
        lookback_days if lookback_days is not None else config.get("lookback_days", 3)
    )

    updated = copy.deepcopy(config)
    updated["lookback_days"] = normalized_lookback_days
    terms = [row["term"] for row in normalized_rows]
    updated["keywords"] = [
        {"term": row["term"], "weight": yaml_number(row["weight"])}
        for row in normalized_rows
    ]
    sync_source_query_terms(updated, terms)
    updated["publication_year_range"] = {
        "enabled": bool(year_range_enabled),
        "start_year": normalized_start_year,
        "end_year": normalized_end_year,
    }
    return updated


def save_editor_config(
    path: Path,
    rows: list[dict[str, Any]],
    year_range_enabled: bool,
    start_year: Any,
    end_year: Any,
    lookback_days: Any | None = None,
) -> dict[str, Any]:
    config = load_config(path)
    updated = build_updated_config(
        config,
        rows,
        year_range_enabled,
        start_year,
        end_year,
        lookback_days=lookback_days,
    )
    save_config(updated, path)
    return updated


class ConfigEditor(tk.Tk):
    def __init__(self, config_path: Path = CONFIG_PATH) -> None:
        super().__init__()
        self.config_path = config_path
        self.config = load_config(config_path)
        self.rows = extract_keyword_rows(self.config)
        lookback_days = lookback_days_default(self.config)
        enabled, start_year, end_year = publication_year_range_defaults(self.config)

        self.lookback_days_var = tk.StringVar(value=str(lookback_days))
        self.year_enabled_var = tk.BooleanVar(value=enabled)
        self.start_year_var = tk.StringVar(value=str(start_year))
        self.end_year_var = tk.StringVar(value=str(end_year))
        self.term_var = tk.StringVar()
        self.weight_var = tk.StringVar(value=format_weight(DEFAULT_WEIGHT))

        self.title("LiteratureWatcher 检索设置")
        self.geometry("880x620")
        self.minsize(760, 520)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        self._build_year_frame()
        self._build_terms_frame()
        self._build_actions()
        self._refresh_tree()

    def _build_year_frame(self) -> None:
        frame = ttk.LabelFrame(self, text="时间限制")
        frame.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 8))
        frame.columnconfigure(6, weight=1)

        ttk.Label(frame, text="最近检索天数").grid(row=0, column=0, sticky="e", padx=(10, 6), pady=(10, 4))
        ttk.Entry(frame, textvariable=self.lookback_days_var, width=10).grid(
            row=0, column=1, sticky="w", pady=(10, 4)
        )
        ttk.Label(frame, text="天").grid(row=0, column=2, sticky="w", padx=(6, 18), pady=(10, 4))

        ttk.Checkbutton(frame, text="启用发表年份范围", variable=self.year_enabled_var).grid(
            row=1, column=0, sticky="w", padx=10, pady=(4, 10)
        )
        ttk.Label(frame, text="开始年").grid(row=1, column=1, sticky="e", padx=(0, 6), pady=(4, 10))
        ttk.Entry(frame, textvariable=self.start_year_var, width=10).grid(row=1, column=2, sticky="w", pady=(4, 10))
        ttk.Label(frame, text="结束年").grid(row=1, column=3, sticky="e", padx=(18, 6), pady=(4, 10))
        ttk.Entry(frame, textvariable=self.end_year_var, width=10).grid(row=1, column=4, sticky="w", pady=(4, 10))

    def _build_terms_frame(self) -> None:
        frame = ttk.LabelFrame(self, text="检索词和权重")
        frame.grid(row=1, column=0, sticky="nsew", padx=12, pady=8)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)

        columns = ("term", "weight")
        self.tree = ttk.Treeview(frame, columns=columns, show="headings", selectmode="extended")
        self.tree.heading("term", text="检索词")
        self.tree.heading("weight", text="权重")
        self.tree.column("term", minwidth=260, width=620, stretch=True)
        self.tree.column("weight", minwidth=80, width=110, stretch=False, anchor="center")
        self.tree.grid(row=0, column=0, sticky="nsew", padx=(10, 0), pady=10)
        self.tree.bind("<<TreeviewSelect>>", self._fill_selected_row)
        self.tree.bind("<Double-1>", self._fill_selected_row)

        scrollbar = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        scrollbar.grid(row=0, column=1, sticky="ns", pady=10)
        self.tree.configure(yscrollcommand=scrollbar.set)

        edit_frame = ttk.Frame(frame)
        edit_frame.grid(row=1, column=0, columnspan=2, sticky="ew", padx=10, pady=(0, 10))
        edit_frame.columnconfigure(1, weight=1)

        ttk.Label(edit_frame, text="检索词").grid(row=0, column=0, sticky="e", padx=(0, 6), pady=4)
        ttk.Entry(edit_frame, textvariable=self.term_var).grid(row=0, column=1, sticky="ew", pady=4)
        ttk.Label(edit_frame, text="权重").grid(row=0, column=2, sticky="e", padx=(12, 6), pady=4)
        ttk.Entry(edit_frame, textvariable=self.weight_var, width=10).grid(row=0, column=3, sticky="w", pady=4)
        ttk.Button(edit_frame, text="添加", command=self.add_term).grid(
            row=0, column=4, sticky="ew", padx=(12, 0), pady=4
        )
        ttk.Button(edit_frame, text="更新选中", command=self.update_selected_term).grid(
            row=0, column=5, sticky="ew", padx=(8, 0), pady=4
        )
        ttk.Button(edit_frame, text="全选", command=self.select_all_terms).grid(
            row=0, column=6, sticky="ew", padx=(8, 0), pady=4
        )
        ttk.Button(edit_frame, text="删除选中", command=self.delete_selected_term).grid(
            row=0, column=7, sticky="ew", padx=(8, 0), pady=4
        )
        ttk.Button(edit_frame, text="清空输入", command=self.clear_inputs).grid(
            row=0, column=8, sticky="ew", padx=(8, 0), pady=4
        )

    def _build_actions(self) -> None:
        frame = ttk.Frame(self)
        frame.grid(row=2, column=0, sticky="ew", padx=12, pady=(8, 12))
        frame.columnconfigure(0, weight=1)

        ttk.Label(frame, text=f"配置文件：{self.config_path}").grid(row=0, column=0, sticky="w")
        ttk.Button(frame, text="重新载入", command=self.reload_config).grid(row=0, column=1, sticky="e", padx=(8, 0))
        ttk.Button(frame, text="保存到 config.yaml", command=self.save_changes).grid(row=0, column=2, sticky="e", padx=(8, 0))

    def _refresh_tree(self) -> None:
        for item_id in self.tree.get_children():
            self.tree.delete(item_id)
        for index, row in enumerate(self.rows):
            self.tree.insert("", "end", iid=str(index), values=(row["term"], format_weight(float(row["weight"]))))

    def _selected_index(self) -> int | None:
        selection = self.tree.selection()
        if not selection:
            return None
        try:
            return int(selection[0])
        except ValueError:
            return None

    def _selected_indices(self) -> list[int]:
        indices: list[int] = []
        for item_id in self.tree.selection():
            try:
                index = int(item_id)
            except ValueError:
                continue
            if 0 <= index < len(self.rows):
                indices.append(index)
        return sorted(set(indices))

    def _single_selected_index(self, action: str) -> int | None:
        indices = self._selected_indices()
        if not indices:
            messagebox.showinfo("请选择词条", f"请先在列表中选择要{action}的检索词。", parent=self)
            return None

        if len(indices) > 1:
            messagebox.showinfo("只选择一个词条", f"一次只能{action}一个检索词，请只选中一行。", parent=self)
            return None

        return indices[0]

    def _fill_selected_row(self, _event: tk.Event | None = None) -> None:
        index = self._selected_index()
        if index is None or index >= len(self.rows):
            return
        row = self.rows[index]
        self.term_var.set(row["term"])
        self.weight_var.set(format_weight(float(row["weight"])))

    def _read_term_input(self) -> tuple[str, float] | None:
        term = clean_term(self.term_var.get())
        try:
            weight = parse_positive_weight(self.weight_var.get())
        except ValueError as error:
            messagebox.showerror("输入有误", str(error), parent=self)
            return None

        if not term:
            messagebox.showerror("输入有误", "检索词不能为空。", parent=self)
            return None
        return term, weight

    def add_term(self) -> None:
        parsed = self._read_term_input()
        if parsed is None:
            return
        term, weight = parsed

        if any(normalize_term(row["term"]) == normalize_term(term) for row in self.rows):
            messagebox.showerror("输入有误", "这个检索词已经存在，请选择它后点击“更新选中”。", parent=self)
            return

        self.rows.append({"term": term, "weight": weight})
        target_index = len(self.rows) - 1

        self._refresh_tree()
        self.tree.selection_set(str(target_index))
        self.tree.see(str(target_index))

    def update_selected_term(self) -> None:
        index = self._single_selected_index("更新")
        if index is None:
            return

        parsed = self._read_term_input()
        if parsed is None:
            return
        term, weight = parsed

        for row_index, row in enumerate(self.rows):
            if row_index != index and normalize_term(row["term"]) == normalize_term(term):
                messagebox.showerror("输入有误", "这个检索词已经存在，不能重复。", parent=self)
                return

        self.rows[index] = {"term": term, "weight": weight}
        self._refresh_tree()
        self.tree.selection_set(str(index))
        self.tree.see(str(index))

    def select_all_terms(self) -> None:
        item_ids = self.tree.get_children()
        if not item_ids:
            return

        self.tree.selection_set(item_ids)
        self.tree.focus(item_ids[0])
        self.tree.see(item_ids[0])

    def delete_selected_term(self) -> None:
        indices = self._selected_indices()
        if not indices:
            messagebox.showinfo("请选择词条", "请先在列表中选择要删除的检索词。", parent=self)
            return

        terms = [self.rows[index]["term"] for index in indices]
        if len(terms) == 1:
            confirm_text = f"删除检索词：{terms[0]}？"
        else:
            preview = "\n".join(f"- {term}" for term in terms[:8])
            if len(terms) > 8:
                preview += f"\n... 共 {len(terms)} 个"
            confirm_text = f"删除选中的 {len(terms)} 个检索词？\n\n{preview}"

        if not messagebox.askyesno("确认删除", confirm_text, parent=self):
            return

        for index in sorted(indices, reverse=True):
            self.rows.pop(index)
        self._refresh_tree()
        self.clear_inputs()

    def clear_inputs(self) -> None:
        self.term_var.set("")
        self.weight_var.set(format_weight(DEFAULT_WEIGHT))
        self.tree.selection_remove(self.tree.selection())

    def reload_config(self) -> None:
        try:
            self.config = load_config(self.config_path)
            self.rows = extract_keyword_rows(self.config)
            lookback_days = lookback_days_default(self.config)
            enabled, start_year, end_year = publication_year_range_defaults(self.config)
        except Exception as error:
            messagebox.showerror("载入失败", str(error), parent=self)
            return

        self.lookback_days_var.set(str(lookback_days))
        self.year_enabled_var.set(enabled)
        self.start_year_var.set(str(start_year))
        self.end_year_var.set(str(end_year))
        self._refresh_tree()
        self.clear_inputs()

    def save_changes(self) -> None:
        try:
            updated = build_updated_config(
                self.config,
                self.rows,
                self.year_enabled_var.get(),
                self.start_year_var.get(),
                self.end_year_var.get(),
                lookback_days=self.lookback_days_var.get(),
            )
            save_config(updated, self.config_path)
        except Exception as error:
            messagebox.showerror("保存失败", str(error), parent=self)
            return

        self.config = updated
        self.rows = extract_keyword_rows(self.config)
        self._refresh_tree()
        messagebox.showinfo("已保存", "检索设置已保存到 config.yaml。", parent=self)


def main() -> None:
    setup_utf8_console()
    app = ConfigEditor()
    app.mainloop()


if __name__ == "__main__":
    main()
