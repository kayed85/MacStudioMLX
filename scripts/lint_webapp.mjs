#!/usr/bin/env node
// Lint the extracted frontend: webapp/js/*.js as ES modules, plus the
// (shrinking) inline <script> block in webapp/index.html as a classic
// script. Two rules only — no-undef and no-redeclare — because those are
// the two failure modes of THIS migration:
//
//   * no-redeclare is the built-twice incident (the health chip, twice):
//     a symbol declared in two places, the later one silently winning.
//   * no-undef is the module-split hazard: ES modules are strict-mode and
//     module-scoped, so a cross-file reference that nobody published to
//     globalThis breaks at event time, in the browser, silently.
//
// The globals story, precisely:
//   - decls at the top level of the INLINE block are visible everywhere
//     (classic-script functions/vars land on window; top-level let/const
//     live in the global lexical environment, which module code can read);
//   - a module's PUBLISHED names — Object.assign(globalThis, {...}) and
//     window.X= / globalThis.X= assignments — are visible everywhere;
//   - a module's other top-level decls are module-private: another file
//     referencing one is a real error, and this script must catch it.
//
// Style rules are deliberately absent: the code was written as one 26k-line
// embedded block over months, and reformatting it during the extraction
// would bury the real diffs.
//
// Usage: node scripts/lint_webapp.mjs      (exit 0 = clean)

import { ESLint } from "eslint";
import { readFileSync, readdirSync, existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const INDEX = path.join(ROOT, "webapp", "index.html");
const JSDIR = path.join(ROOT, "webapp", "js");

// ---- gather the sources ----------------------------------------------------
const html = readFileSync(INDEX, "utf-8");
let inline = "";
const m = html.match(/<script>([\s\S]*?)<\/script>/);
if (m) inline = m[1];

const moduleFiles = existsSync(JSDIR)
  ? readdirSync(JSDIR).filter((f) => f.endsWith(".js")).sort()
      .map((f) => path.join(JSDIR, f))
  : [];

// ---- compute the shared-globals manifest ----------------------------------
const IDENT = String.raw`[A-Za-z_$][\w$]*`;

function topLevelDecls(src) {
  // Top-level = column 0. The panel's embedded block is uniformly indented
  // that way, and the extracted modules keep the convention.
  const names = new Set();
  for (const line of src.split("\n")) {
    let mm = line.match(new RegExp(`^(?:async\\s+)?function\\s+(${IDENT})\\s*\\(`));
    if (mm) names.add(mm[1]);
    mm = line.match(new RegExp(`^(?:let|const|var)\\s+(${IDENT})`));
    if (mm) names.add(mm[1]);
  }
  return names;
}

function publishedNames(src) {
  const names = new Set();
  // Column-0 only: a top-level `window.X = ...` / `globalThis.X = ...` is a
  // declaration-substitute and claims the name; an INDENTED assignment is a
  // runtime write to shared state, which legitimately happens from many
  // files (it did from many places in the single block too) and must not
  // read as a second publisher.
  for (const mm of src.matchAll(new RegExp(`^(?:window|globalThis)\\.(${IDENT})\\s*=`, "gm")))
    names.add(mm[1]);
  // Object.assign(globalThis, { a, b, c }) — the module publish block.
  for (const mm of src.matchAll(/Object\.assign\(globalThis,\s*\{([\s\S]*?)\}\s*\)/g)) {
    // Comments inside the block are legal; a brace inside one is not a
    // terminator (the block is matched up to the first `}` — keep braces
    // out of publish-block comments, and strip the comments before parsing).
    const body = mm[1].replace(/\/\/[^\n]*/g, "");
    for (const name of body.split(",")) {
      const t = name.trim().split(":")[0].trim();
      if (new RegExp(`^${IDENT}$`).test(t)) names.add(t);
    }
  }
  return names;
}

const shared = new Set();
for (const n of topLevelDecls(inline)) shared.add(n);
for (const n of publishedNames(inline)) shared.add(n);
const modSrc = new Map();
for (const f of moduleFiles) {
  const src = readFileSync(f, "utf-8");
  modSrc.set(f, src);
  for (const n of publishedNames(src)) shared.add(n);
}
// Optional hooks the code probes with `typeof x === 'function'` before
// calling — declared, guarded, legitimately absent.
shared.add("_syncLorasJsonField");

const BROWSER = Object.fromEntries(
  ("window document navigator location localStorage sessionStorage fetch " +
   "FormData URLSearchParams URL Blob File FileReader Image Audio " +
   "AbortController setTimeout setInterval clearTimeout clearInterval " +
   "requestAnimationFrame cancelAnimationFrame console alert confirm prompt " +
   "getComputedStyle MutationObserver ResizeObserver IntersectionObserver " +
   "DOMParser XMLHttpRequest WebSocket history performance crypto " +
   "structuredClone globalThis queueMicrotask CustomEvent Event " +
   "KeyboardEvent PointerEvent HTMLElement HTMLVideoElement Element Node " +
   "NodeList screen matchMedia scrollTo innerWidth innerHeight " +
   "devicePixelRatio CSS getSelection").split(" ").map((k) => [k, "readonly"]));
// The one template seam page() substitutes inside the inline block.
BROWSER.__BOOTSTRAP__ = "readonly";

const sharedGlobals = Object.fromEntries([...shared].map((k) => [k, "writable"]));

// ---- lint ------------------------------------------------------------------
// Each file is linted with the SHARED set minus its own declarations — a
// file must not see its own names as pre-existing globals (every decl
// would read as a redeclaration), and it must not see another module's
// PRIVATE names at all (a typo landing on one should flag).
async function lintText(text, name, sourceType, ownNames) {
  const globalsHere = { ...BROWSER };
  for (const [k, v] of Object.entries(sharedGlobals))
    if (!ownNames.has(k)) globalsHere[k] = v;
  const eslint = new ESLint({
    cwd: ROOT,
    overrideConfigFile: true,
    overrideConfig: [{
      languageOptions: {
        ecmaVersion: "latest",
        sourceType,
        globals: globalsHere,
      },
      rules: { "no-undef": "error", "no-redeclare": "error" },
    }],
  });
  return eslint.lintText(text, { filePath: name });
}

let errors = 0;
const report = (results) => {
  for (const r of results) {
    for (const msg of r.messages) {
      errors++;
      console.error(`${path.relative(ROOT, r.filePath)}:${msg.line}:${msg.column}  ${msg.message}  [${msg.ruleId}]`);
    }
  }
};

// `own` is the file's LEXICAL top-level declarations only: those must not
// arrive as config globals (every decl would read as a redeclaration).
// Its own window.X= / globalThis.X= publishes stay IN the globals — the
// file legitimately reads those back through the global scope.
for (const f of moduleFiles) {
  const src = modSrc.get(f);
  report(await lintText(src, f, "module", topLevelDecls(src)));
}
if (inline.trim()) {
  report(await lintText(inline, path.join(ROOT, "webapp", "index.inline.js"),
                        "script", topLevelDecls(inline)));
}

// ---- inline handler targets must be published ------------------------------
// The v4.9.0 regression (PR #69): markup generated INSIDE a module —
// `onclick="deleteOutput('x')"` in a template literal — resolves its
// function name against the GLOBAL scope at click time, not the module
// scope. A function referenced only from its own module's generated markup
// looked module-internal to the extraction analysis and went unpublished;
// 38 buttons died silently. eslint cannot see into attribute strings, so
// this pass scans every on*="…", href="javascript:…" and
// setAttribute('on…', …) string in the page and the modules, and requires
// each function it calls — if it is a top-level declaration in ANY module —
// to be published.
{
  const topLevelAnywhere = new Map(); // name -> file
  for (const [f, src] of modSrc)
    for (const n of topLevelDecls(src)) topLevelAnywhere.set(n, f);
  const publishedAll = new Set(shared);
  const handlerRe = new RegExp(
    String.raw`(?:\bon[a-z]+\s*=\s*\\?["']|href\s*=\s*\\?["']javascript:|setAttribute\(\s*['"]on[a-z]+['"]\s*,\s*['"\x60])([^"'\x60]{0,400})`, "g");
  const callRe = new RegExp(String.raw`\b(${IDENT})\s*\(`, "g");
  const sources = [[INDEX, html], ...modSrc.entries()];
  const seen = new Set();
  for (const [file, src] of sources) {
    for (const m of src.matchAll(handlerRe)) {
      for (const c of m[1].matchAll(callRe)) {
        const name = c[1];
        const owner = topLevelAnywhere.get(name);
        if (!owner || publishedAll.has(name) || seen.has(name)) continue;
        seen.add(name);
        errors++;
        console.error(`inline handler calls '${name}' (declared in ${path.relative(ROOT, owner)}) but it is not published — add it to that module's Object.assign(globalThis, {...}) block. Referenced from ${path.relative(ROOT, file)}`);
      }
    }
  }
}

// ---- duplicate publishes across module files -------------------------------
// no-redeclare is per-file; the cross-file version of the built-twice
// accident is two modules publishing the SAME name to globalThis.
const owners = new Map();
const claim = (name, file) => {
  if (owners.has(name) && owners.get(name) !== file) {
    errors++;
    console.error(`global '${name}' published by both ${path.relative(ROOT, owners.get(name))} and ${path.relative(ROOT, file)}`);
  } else owners.set(name, file);
};
for (const [f, src] of modSrc) for (const n of publishedNames(src)) claim(n, f);
for (const n of topLevelDecls(inline)) claim(n, INDEX);

if (errors) {
  console.error(`\nlint_webapp: ${errors} problem(s).`);
  process.exit(1);
}
console.log(`lint_webapp: clean — ${moduleFiles.length} module(s)` +
            (inline.trim() ? " + the inline block" : ", inline block empty") + ".");
