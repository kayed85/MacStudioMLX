"""The maintainer-broadcast channel and the update pop-up's server half.

BROADCAST.json on public main is the maintainer's one-way channel to every
install: the 30-minute version poll fetches it, the panel shows it once per
message id, dismissal is local and permanent. The parser is a closed shape —
a malformed file must read as "no message", never break a panel.
"""

import json
import unittest

import mlx_ltx_panel as panel


class ParseBroadcast(unittest.TestCase):
    def test_valid_message(self):
        m = panel._parse_broadcast(json.dumps(
            {"id": "2026-09-update", "title": "Please update",
             "body": "4.8.2 fixes the errors that said nothing."}))
        self.assertEqual(m["id"], "2026-09-update")
        self.assertEqual(m["title"], "Please update")

    def test_garbage_is_no_message(self):
        for raw in ("", "not json", "[1,2]", json.dumps({"title": "no id"}),
                    json.dumps({"id": "x"})):
            self.assertIsNone(panel._parse_broadcast(raw), raw)

    def test_version_gate_skips_updated_installs(self):
        raw = json.dumps({"id": "m1", "title": "Update!",
                          "show_if_version_below": "4.9.0"})
        self.assertIsNone(panel._parse_broadcast(raw, "4.9.0"))
        self.assertIsNone(panel._parse_broadcast(raw, "4.10.1"))
        self.assertIsNotNone(panel._parse_broadcast(raw, "4.8.2"))

    def test_version_gate_tolerates_junk_versions(self):
        raw = json.dumps({"id": "m1", "title": "T",
                          "show_if_version_below": "banana"})
        # unparseable gate → message shows (fail open: a typo must not
        # silently mute a broadcast the maintainer meant to send)
        self.assertIsNotNone(panel._parse_broadcast(raw, "4.8.2"))

    def test_lengths_are_bounded(self):
        m = panel._parse_broadcast(json.dumps(
            {"id": "x" * 500, "title": "t" * 500, "body": "b" * 9000}))
        self.assertLessEqual(len(m["id"]), 64)
        self.assertLessEqual(len(m["title"]), 120)
        self.assertLessEqual(len(m["body"]), 2000)


class SeenIdsSetting(unittest.TestCase):
    def _patch(self, **kw):
        out, err = panel._validate_settings_patch(kw)
        self.assertIsNone(err, err)
        return out

    def test_accepts_csv_string_from_the_form_layer(self):
        got = self._patch(broadcast_seen_ids="a,b,c")["broadcast_seen_ids"]
        self.assertEqual(got, ["a", "b", "c"])

    def test_accepts_list_and_caps_at_50(self):
        got = self._patch(broadcast_seen_ids=[str(i) for i in range(80)])
        self.assertEqual(len(got["broadcast_seen_ids"]), 50)
        self.assertEqual(got["broadcast_seen_ids"][-1], "79")

    def test_exposed_publicly(self):
        self.assertIn("broadcast_seen_ids", panel.get_settings_public())


if __name__ == "__main__":
    unittest.main()
