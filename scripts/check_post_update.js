#!/usr/bin/env node
/*
 * Gate: scripts/post_update.sh keeps the two properties that were paid for in
 * blood, and that collapsing 18 Pinokio steps into one shell script can silently
 * take away.
 *
 * ---------------------------------------------------------------------------
 * 1. ORDER — the codec patch runs before anything optional
 * ---------------------------------------------------------------------------
 * On v3.8.1 the patch sat eleven steps down, behind litellm / smolagents /
 * mflux / three weight fetches. The owner's Pinokio run ended at step 7 of 18
 * — exit code 0, no error, the update presented as finished — and the patch
 * never executed. Every render on that install encoded 4:2:0. v3.8.2 moved the
 * patch to run immediately after the reinstall that replaces site-packages and
 * enforced the ordering with a gate rather than a convention. This is that
 * gate, re-pointed at the file the work now lives in.
 *
 * It must ALSO stay after the package reinstall: that step overwrites
 * site-packages, so a patch applied before it is thrown away.
 *
 * ---------------------------------------------------------------------------
 * 2. FATALITY — a failed load-bearing step fails the Update
 * ---------------------------------------------------------------------------
 * Under Pinokio each of these was its own step, so a non-zero exit aborted the
 * run for free. Inside one script it does not, and the first draft of
 * post_update.sh lost it: patch_ltx_codec.py printed its CODEC PATCH FAILURE
 * banner and the update carried on and reported success — reinstating the exact
 * silent-4:2:0 outcome the banner exists to prevent. Found by the v4.0 journey
 * sim. The four load-bearing steps must go through `require`.
 *
 * Run: node scripts/check_post_update.js      exit 0 = PASS
 */
const fs = require("fs")
const path = require("path")

const file = path.resolve(__dirname, "post_update.sh")
const lines = fs.readFileSync(file, "utf8").split("\n")

// Executable lines only — the rationale above each step names these commands.
const code = lines.map((l, i) => ({ n: i + 1, t: l }))
  .filter((l) => l.t.trim() && !l.t.trim().startsWith("#"))

const failures = []
const find = (re) => code.find((l) => re.test(l.t))
const at = (re, label) => {
  const hit = find(re)
  if (!hit) { failures.push(`no line matching ${label}`); return Infinity }
  return hit.n
}

const reinstall = at(/uv pip install .*--reinstall .*\.\/packages\/ltx-core-mlx/, "the vendored package reinstall")
const patch = at(/patch_ltx_codec\.py/, "the codec patch")
const optional = [
  ["mflux", /mflux==/],
  ["litellm", /litellm>=/],
  ["smolagents", /smolagents>=/],
  ["the mosaic upscaler fetch", /spatial_upscaler_x2_v1_1/],
  ["the LTX-2.5 weight fetch", /fetch_pack_release\.py/],
  ["the H3 compact-engine build", /h3_build_q8\.sh/],
  ["the model trim", /^rm -f /],
]

console.log(`package reinstall : line ${reinstall}`)
console.log(`codec patch       : line ${patch}`)

if (patch < reinstall) {
  failures.push(`the codec patch (line ${patch}) runs BEFORE the package reinstall (line ${reinstall}) — the reinstall overwrites site-packages, so the patch would be thrown away.`)
} else {
  console.log("  ok    patch runs after the reinstall that replaces site-packages")
}

for (const [label, re] of optional) {
  const n = at(re, label)
  if (n < patch) {
    failures.push(`${label} (line ${n}) runs BEFORE the codec patch (line ${patch}). Everything optional must come after it.`)
  } else {
    console.log(`  ok    patch(${patch}) < ${label}(${n})`)
  }
}

// --- fatality ---------------------------------------------------------------
if (!/^require\(\)/m.test(lines.join("\n"))) {
  failures.push("post_update.sh defines no `require()` helper — load-bearing steps have no way to fail the Update.")
}
const mustRequire = [
  ["the vendored pin move", /ltx_checkout\.sh/],
  ["the mlx pin", /mlx==0\.31\.1/],
  ["the vendored package reinstall", /--reinstall .*\.\/packages\/ltx-core-mlx/],
  ["the codec patch", /patch_ltx_codec\.py/],
]
for (const [label, re] of mustRequire) {
  const hit = code.find((l) => re.test(l.t) && !/^\s*require\(\)/.test(l.t))
  if (!hit) { failures.push(`no executable line for ${label}`); continue }
  if (!/^require\s/.test(hit.t.trim())) {
    failures.push(`${label} (line ${hit.n}) is not wrapped in \`require\` — it would print its error and let the Update report success.`)
  } else {
    console.log(`  ok    ${label} is fatal on failure`)
  }
}

// --- 3. THE mlx PIN AND THE mflux PIN ARE ONE DECISION -----------------------
// Step 2 pins mlx. Step 7 installs mflux WITH deps (the resolving call, there
// so a fresh install gets mflux's transitive set). mflux declares its OWN mlx
// range, so that later resolve can MOVE the version step 2 just pinned, in the
// one direction nobody would look for it.
//
// Measured 2026-08-28, in a copy of the real venv:
//   mlx 0.32.2 installed, then `uv pip install 'mflux==0.18.0'` →
//     - mlx==0.32.2  + mlx==0.31.2
//     - mlx-metal==0.32.2  + mlx-metal==0.31.2
// mflux 0.18.0 declares `mlx>=0.27.0,<0.32.0` on darwin, so the resolver walks
// mlx DOWN to the newest thing that satisfies it — which is 0.31.2, the exact
// release the audio ship-blocker exists to keep off users' machines. It is
// invisible today only because 0.31.1 sits inside mflux's range; the moment
// the mlx pin moves to 0.32.x on its own, every fresh install and every Update
// ends on 0.31.2 with a -42 dB vocoder, and nothing prints a warning.
//
// So the two pins move together or not at all. mflux 0.19.1 is the first
// release that declares `mlx>=0.32.0,<0.33.0`, and the whole pair was BUILT AND
// MEASURED on 2026-08-28 before being put back: in a copy of the real venv the
// post_update sequence in order (step 2 --no-deps, step 2b with the transformers
// cap, step 7 with deps, step 7 again --no-deps) lands on mlx 0.32.1 /
// mlx-metal 0.32.1 / mlx-lm 0.31.1 / mflux 0.19.1 / transformers 5.7.0 with
// nothing walked back, and both FBCache anchors applied cleanly to a real
// 0.19.1 install (idempotent on re-run). The mechanics are not the blocker.
//
// The blocker is memory, re-measured against the cache policy that shipped
// first: 0.32.1 sits at a higher share of physical RAM than 0.31.1 on three of
// four tiers, and the excess is ACTIVE memory (+12.0 GB Q4, +29.2 GB Q8) which
// no cache cap can reach. Full table in install.js above the pin and in
// CLAUDE.md sec 4. mlx-lm has no 0.32.x and declares `mlx>=0.30.4` with no upper
// bound, so it is not part of the pair.
//
// This gate is the place that knowledge lives. Change the pair here, and in
// every file below, in ONE commit — and re-read this comment first.
const PIN_PAIR = { mlx: "0.31.1", mflux: "0.18.0" }
const pinSites = [
  ["scripts/post_update.sh", "mlx"], ["install.js", "mlx"],
  ["scripts/post_update.sh", "mflux"], ["install.js", "mflux"],
  ["scripts/pinokio/mflux_pack.sh", "mflux"], ["install_qwen.js", "mflux"],
]
const repoRoot = path.resolve(__dirname, "..")
const seenPins = new Map()
for (const [rel, pkg] of pinSites) {
  const p = path.join(repoRoot, rel)
  let text
  try { text = fs.readFileSync(p, "utf8") } catch (e) {
    failures.push(`${rel} is missing — the ${pkg} pin cannot be checked.`); continue
  }
  // install.js delegates the mflux install to mflux_pack.sh; that is fine, the
  // script itself is in the list. Only assert on files that actually name it.
  //
  // Comment lines are stripped first, deliberately: the rationale above these
  // pins QUOTES the versions it is arguing about ("mlx==0.31.1 (NOT 0.31.2)"),
  // and a gate that reads prose would fail on the explanation instead of the
  // command. Only what actually runs is asserted.
  const runnable = text.split("\n").filter((l) => !/^\s*(\/\/|#)/.test(l)).join("\n")
  const found = [...runnable.matchAll(new RegExp(`(?:^|[^\\w.-])${pkg}==([0-9][0-9.]*)`, "gm"))].map((m) => m[1])
  if (!found.length) continue
  const wrong = found.filter((v) => v !== PIN_PAIR[pkg])
  const key = `${rel}:${pkg}`
  seenPins.set(key, found)
  if (wrong.length) {
    failures.push(`${rel} pins ${pkg}==${[...new Set(wrong)].join("/")} but this gate's pair says ${pkg}==${PIN_PAIR[pkg]}. mlx and mflux constrain each other (see the comment above): move both, in one commit, and update PIN_PAIR.`)
  } else {
    console.log(`  ok    ${rel} pins ${pkg}==${PIN_PAIR[pkg]}`)
  }
}
if (!seenPins.size) failures.push("no mlx/mflux pin found in any install path — the coupling gate is not looking at anything.")

// --- and the optional ones must NOT be fatal --------------------------------
// An Update that a network hiccup can brick is worse than one that warns.
for (const [label, re] of [["the IC-LoRA fetches", /IC-LoRA-Colorizer/], ["the LTX-2.5 weight fetch", /fetch_pack_release\.py/]]) {
  const hit = code.find((l) => re.test(l.t))
  const guarded = hit && (/\|\|/.test(hit.t) || /\|\|/.test((code[code.indexOf(hit) + 1] || {}).t || ""))
  if (!guarded) failures.push(`${label} (line ${hit && hit.n}) is not guarded with \`|| echo WARN\` — an Update must not be brickable by a network hiccup.`)
  else console.log(`  ok    ${label} is best-effort`)
}

console.log("")
console.log(failures.length ? `RESULT: FAIL (${failures.length})` : "RESULT: PASS")
for (const f of failures) console.log("FAIL  " + f)
process.exit(failures.length ? 1 : 0)
