#!/usr/bin/env python3
"""H3-21: Finish and Load Params must not offer/silently redo an H3 clip
on a Mac without H3.

Finish: `_syncH3FinishAffordance()` computed its target tier purely from
the client-side H3 tier registry (h3TierByKeyExact/h3FinishTierKey) — no
install check anywhere in that math — so a Mac without H3 at all still saw
"Finish at Standard 5s · ~6 min" on an H3 clip. Clicking called
h3FinishActive(), which DOES gate on setEngine('h3') actually landing on
h3, but only found out — and only told the user — after the click, via a
toast. The affordance now checks H3.available up front: unavailable, the
label reads "Finish · needs Hailuo H3" and the click opens the install
card instead of attempting a doomed render; available, behaviour and copy
are unchanged from before.

Load Params: restored geometry, prompt and every H3-specific field
(h3_quality, h3_orientation, ...) using the SIDECAR's engine string
unconditionally, while setEngine() — the actual surface swap — silently
falls back to LTX when H3 isn't installed (same gate Finish checks) and
the fallback was never read. An H3 clip's params landed silently in LTX
Image mode at H3's dimensions with H3's dialogue-tagged prompt, no
explanation. Now the function reads what setEngine() actually landed on;
when it disagrees with the sidecar's engine, a toast names the mismatch
before applying the (now conditional) H3-only setters.

Verified live (booted a 48 GB Mac with nothing installed, real
setEngine() gate): loadParams() against a synthetic H3 sidecar produced
one toast reading "This clip was made with Hailuo H3. ..." and left the
form on LTX/t2v, as it actually rendered. This file pins both code paths
statically.
"""
from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
QUEUE_JS = (ROOT / "webapp" / "js" / "queue.js").read_text(encoding="utf-8")


def _function_body(src: str, signature: str) -> str:
    start = src.index(signature)
    depth = 0
    i = src.index("{", start)
    j = i
    while True:
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[i:j + 1]
        j += 1


class FinishSaysNeedsH3WhenUnavailable(unittest.TestCase):
    def setUp(self):
        self.body = _function_body(QUEUE_JS, "function _syncH3FinishAffordance(")

    def test_checks_h3_available(self):
        self.assertIn("const h3NotHere = !(H3 && H3.available);", self.body)

    def test_label_names_the_requirement(self):
        self.assertIn("'Finish · needs Hailuo H3'", self.body)

    def test_click_opens_install_card_instead_of_finishing(self):
        self.assertIn("btn.onclick = (ev) => { ev.preventDefault(); openH3InstallCard(); };",
                       self.body)

    def test_normal_path_still_restores_the_real_handler(self):
        self.assertIn("btn.onclick = () => h3FinishActive();", self.body)


class LoadParamsNamesTheEngineMismatch(unittest.TestCase):
    def setUp(self):
        self.body = _function_body(QUEUE_JS, "async function loadParams(recipe) {")

    def test_reads_what_setEngine_actually_landed_on(self):
        self.assertIn("_engLanded = setEngine(_eng, { persist: false }) || _eng;", self.body)

    def test_h3_only_setters_gate_on_the_landed_engine_not_the_sidecars(self):
        # The old code branched on `_eng === 'h3'` (the sidecar's claim); it
        # must now branch on where the surface actually landed, or the H3
        # setters run against an LTX form.
        self.assertIn("if (_engLanded === 'h3') {", self.body)
        self.assertNotIn("if (_eng === 'h3') {\n    if (typeof setH3Upscale", self.body)

    def test_toasts_the_mismatch(self):
        self.assertIn("if (_eng === 'h3' && _engLanded !== 'h3') {", self.body)
        self.assertIn("This clip was made with Hailuo H3.", self.body)


if __name__ == "__main__":
    unittest.main()
