# Contributing to Phosphene

**Short version: yes, send it.** Bug fixes, new features, new engines, docs,
copy fixes, a better ETA estimate — all of it is welcome, and none of it needs
to be asked about first.

---

## Read this before you open a PR

**Your PR will say CONFLICTING. That is expected and it is not about your work.**

Public `main` is published as a curated snapshot commit rather than by merging
branches — each release replaces the tree in one commit. Nothing can
fast-forward onto that, so *every* incoming PR shows as conflicting regardless
of how clean the patch is.

It still gets read, and it still gets shipped. What happens is:

1. Your PR is reviewed as a patch — the diff, the reasoning, the validation.
2. If it holds up, the change is applied to the working tree.
3. It ships in the next release, and **you are credited by name in the release
   notes and usually in the commit message**.
4. Your PR is closed with a comment saying exactly what landed and where.

Closed does not mean rejected here. Two recent examples: a 48 GB RAM-gate
mismatch and a Pinokio shell-dispatch hang were both contributor-diagnosed;
the second one became an enforced build gate across all ten launcher scripts,
which is more than the original patch asked for.

If a change is *not* going to land, you will be told that and why, in the PR.

---

## What is most useful

**A good bug report is worth as much as a patch.** The most valuable
contributions this project has had were reports where someone captured the
actual crash line, showed what *did* work on the same machine, and narrowed the
difference. That turns an unreproducible report into a ten-minute fix.

If you are reporting a render problem, the things that matter:

- **Chip and macOS version** (`Apple M2 Max`, `macOS 26.5.2`) — several bugs
  have been chip-specific, including one where macOS reports the same failure
  under a different name on different silicon.
- **Phosphene version** and the vendored engine tag (both are in the header).
- **What the panel log says**, and if it crashed hard, what the process printed
  to stderr.
- **Whether the engine CLI reproduces it** outside the panel. This single fact
  splits "engine bug" from "panel bug" immediately.

## New engines are welcome

The engine table is data-driven on purpose. Adding one is an `ENGINES` entry, a
`<symbol>` for its mark, and a probe — the picker, the tooltips, the mode
gating, the install offer and the CSS accents all derive from that row. If you
want to add an engine, open an issue first so we can agree on the shape before
you write the adapter.

## Things that will get pushed back on

- **A dependency added without a reason in the commit message.** Every pin in
  this repo is a paid lesson and they are documented as such in `CLAUDE.md`.
- **A change to a render path with no evidence it is identical where it should
  be.** Byte-identical output at a fixed seed, or a measured difference — not
  "looks the same to me".
- **Long Pinokio shell payloads.** `node scripts/check_pinokio_scripts.js`
  enforces this and will fail the build.
- **A claim in user-facing copy that is not true on a fresh install.**

## Before you send

```bash
node scripts/check_ltx_pin.js          # engine pin agreement
node scripts/check_pinokio_scripts.js  # launcher payload sizes
python3 scripts/check_output_codec.py  # the last render is what was asked for
node --check install.js && node --check update.js
```

and run the panel test suites:

```bash
for t in test_*.py; do ./ltx-2-mlx/env/bin/python -m unittest "${t%.py}"; done
```

Tests are not a formality here. Several of them exist because a thing shipped
broken in a way that looked fine — they encode the failure, not the feature.

## Licence

By contributing you agree your work ships under the repository's licence.
Third-party model weights and brand marks are **not** vendored into this repo;
if your change needs an asset, it downloads it at install time.
