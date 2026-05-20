from __future__ import annotations

import unittest

import config_editor
import search_literature


class SearchLiteratureHelperTests(unittest.TestCase):
    def test_source_terms_deduplicate_configured_terms(self) -> None:
        config = {
            "source_query_terms": {"default": ["Alpha", " alpha ", "Beta", ""]},
            "max_query_terms": 10,
        }

        self.assertEqual(search_literature.source_terms(config, "crossref"), ["Alpha", "Beta"])

    def test_source_terms_fall_back_to_keywords_when_mapping_is_invalid(self) -> None:
        config = {
            "keywords": [{"term": "Alpha", "weight": 2}, "Beta"],
            "source_query_terms": [],
            "max_query_terms": 10,
        }

        self.assertEqual(search_literature.source_terms(config, "crossref"), ["Alpha", "Beta"])

    def test_exclude_matching_results_uses_doi_or_title_identity(self) -> None:
        items = [
            {"doi": "10.1000/example"},
            {"title_en": "Same Title"},
            {"title_en": "Keep This"},
        ]
        excluded = [
            {"doi": "https://doi.org/10.1000/example"},
            {"title_en": "Same   Title"},
        ]

        self.assertEqual(
            search_literature.exclude_matching_results(items, excluded),
            [{"title_en": "Keep This"}],
        )


class ConfigEditorHelperTests(unittest.TestCase):
    def test_sync_source_query_terms_reuses_one_list_for_yaml_aliases(self) -> None:
        config = {"source_query_terms": {"default": ["old"], "pubmed": ["old"]}}

        config_editor.sync_source_query_terms(config, ["new", "term"])

        source_terms = config["source_query_terms"]
        self.assertEqual(source_terms["default"], ["new", "term"])
        self.assertIs(source_terms["default"], source_terms["pubmed"])


if __name__ == "__main__":
    unittest.main()
