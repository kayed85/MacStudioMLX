#!/usr/bin/env python3
"""FILM-45 (panel-level half): a Unicode-aware slug and full-width-colon
locations, so a Japanese or accented-Latin film title is not mangled.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import mlx_ltx_panel as panel                                        # noqa: E402


class TheSlugKeepsNonAsciiLetters(unittest.TestCase):
    def test_a_japanese_title_is_not_reduced_to_shot(self):
        slug = panel._sb_slug("京都の夜", 6)
        self.assertNotEqual(slug, "shot")
        self.assertIn("京都の夜", slug)

    def test_an_accented_latin_title_keeps_its_accents(self):
        slug = panel._sb_slug("Café à Paris", 6)
        self.assertEqual(slug, "café-à-paris")

    def test_plain_ascii_is_unaffected(self):
        self.assertEqual(panel._sb_slug("Night Drive", 6), "night-drive")

    def test_two_different_japanese_titles_produce_different_slugs(self):
        a = panel._sb_slug("京都の夜")
        b = panel._sb_slug("東京の朝")
        self.assertNotEqual(a, b)

    def test_an_empty_or_symbols_only_title_still_falls_back_to_shot(self):
        self.assertEqual(panel._sb_slug(""), "shot")
        self.assertEqual(panel._sb_slug("!!! *** ///"), "shot")

    def test_word_count_is_still_respected(self):
        slug = panel._sb_slug("one two three four five six seven", 3)
        self.assertEqual(slug, "one-two-three")


class LocationsAcceptTheFullWidthColon(unittest.TestCase):
    def test_a_japanese_location_line_splits_on_the_fullwidth_colon(self):
        locs = panel._sb_parse_locations("台所：戦後の家の裏にある小さな台所")
        self.assertEqual(len(locs), 1)
        self.assertEqual(locs[0]["name"], "台所")
        self.assertEqual(locs[0]["description"], "戦後の家の裏にある小さな台所")

    def test_the_ascii_colon_still_works_unchanged(self):
        locs = panel._sb_parse_locations("Kitchen: a small kitchen behind the house")
        self.assertEqual(locs[0]["name"], "Kitchen")
        self.assertEqual(locs[0]["description"], "a small kitchen behind the house")

    def test_a_line_with_neither_colon_falls_back_as_before(self):
        locs = panel._sb_parse_locations("just a plain sentence with no colon at all")
        self.assertEqual(len(locs), 1)
        self.assertTrue(locs[0]["description"])

    def test_multiple_japanese_locations_get_distinct_ids(self):
        locs = panel._sb_parse_locations(
            "台所：小さな台所\n"
            "居間：広い居間\n")
        self.assertEqual(len(locs), 2)
        self.assertNotEqual(locs[0]["id"], locs[1]["id"])


if __name__ == "__main__":
    unittest.main()
