"""The update banner's server half: the two settings and the star-click count.

Design notes worth keeping, because both were live bugs during the build:

1. `/settings` is FORM-ENCODED. A JSON body parses to nothing and the endpoint
   cheerfully returns ok:true having saved absolutely nothing — the banner
   dismissed itself on screen and came back on the next boot.
2. Form values arrive as STRINGS, and `bool("false")` is True. Parsing the flag
   with a bare bool() makes "don't ask me again" impossible to clear.

`update_banner_dismissed` stores a VERSION, not a boolean, so dismissing 4.1.1
does not silence the banner for 4.2.0. The star ask is a local flag and not an
analytics lookup: GitHub can only answer "did this authenticated user star it",
which would mean asking someone to sign into GitHub inside a local video panel
to suppress a prompt.
"""

import unittest

import mlx_ltx_panel as panel


class SettingsPatch(unittest.TestCase):
    def _patch(self, **kw):
        out, err = panel._validate_settings_patch(kw)
        self.assertIsNone(err, err)
        return out

    def test_dismissed_stores_the_version_string(self):
        self.assertEqual(self._patch(update_banner_dismissed="4.1.1")["update_banner_dismissed"], "4.1.1")

    def test_dismissed_can_be_cleared(self):
        self.assertEqual(self._patch(update_banner_dismissed="")["update_banner_dismissed"], "")

    def test_dismissed_is_length_bounded(self):
        got = self._patch(update_banner_dismissed="x" * 500)["update_banner_dismissed"]
        self.assertLessEqual(len(got), 32)

    def test_star_flag_from_form_strings(self):
        """The bool("false") trap — every one of these arrives as a string."""
        for raw, want in (("true", True), ("1", True), ("on", True), ("yes", True),
                          ("false", False), ("0", False), ("", False), ("off", False)):
            self.assertIs(self._patch(star_prompt_done=raw)["star_prompt_done"], want, raw)

    def test_star_flag_from_real_bools(self):
        self.assertIs(self._patch(star_prompt_done=True)["star_prompt_done"], True)
        self.assertIs(self._patch(star_prompt_done=False)["star_prompt_done"], False)

    def test_both_keys_are_public(self):
        """The banner reads these from /settings before Settings is ever opened."""
        pub = panel.get_settings_public()
        self.assertIn("update_banner_dismissed", pub)
        self.assertIn("star_prompt_done", pub)


if __name__ == "__main__":
    unittest.main()
