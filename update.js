// =============================================================================
// update.js — THIN AND STABLE. Pull, then delegate to the post-pull tree.
// =============================================================================
//
// THIS FILE SHOULD IDEALLY NEVER BE EDITED AGAIN. Everything that can break
// lives in `scripts/post_update.sh`.
//
// WHY. Pinokio wires the menu item as { text: "Update", href: "update.js" } —
// it loads this file from disk INTO MEMORY and then executes its steps. Step 1
// below is what pulls the repo, so the repo moves to the new version MID-RUN
// and execution continues from the STALE in-memory copy. cocktailpeanut (who
// wrote Pinokio) hit this himself on 2026-08-12 and confirmed the sequencing
// with timestamps: the repo moved to v3.8.0 at 13:12:17 and the *old* script's
// command failed ~18 s later. His verdict was that it does not require updater
// restructuring — and it doesn't, on his side. On ours it means:
//
//   an app can never deliver a fix to its own installer on the click that
//   pulls that fix. It always lands one click late, and the user sees a
//   failure they have already downloaded the cure for.
//
// `scripts/post_update.sh` is read AFTER the pull, so a broken update step is
// fixable by shipping — the normal way. So is THE VENDORED PIN, which is why
// it lives in `scripts/pinokio/ltx_checkout.sh` and not here: a pin held in
// this file would move one click late on every single bump, forever.
//
// Full analysis of the sequencing is in the header of
// `scripts/post_update.sh`, which exists because of it.
//
// RULES FOR EDITING THIS FILE
// ---------------------------
//   - No pip/uv calls, no patches, no downloads, no trims. Those all belong in
//     scripts/post_update.sh.
//   - Assume the copy on the user's disk is ANY historical version. A change
//     here gets the scrutiny of a schema migration.
//   - Every dispatch stays under 350 chars - the width @Morac2 actually
//     bisected as safe (#50), which `node scripts/check_pinokio_scripts.js`
//     now enforces for every launcher script (4.20; it was 500).
//   - Everything before the pull may only reference files that ALREADY exist
//     in the user's current tree. That does not mean "inline it": the
//     obstruction guard and the converge step are `scripts/pinokio/*.sh`,
//     which ship in the same commit as this file and are therefore exactly as
//     current as this file is. Step 1 stays inline because it is what
//     establishes the upstream ref the other two depend on.
//
// Note: fs.link is only declared in install.js, not here. Running it on every
// Update would migrate existing model folders into the drive without a Reset,
// conflating durability of model assets with routine code updates and adding a
// 36 GB merge step to a flow that should be fast.
module.exports = {
  run: [
    // ---- 1. Move the panel repo -------------------------------------------
    // Branch- and remote-aware, because the same update.js serves the public
    // panel (origin/main) and the private dev clone (beta/main) — the
    // explicit-origin form `git pull origin <branch>` broke when public
    // origin/dev was deleted in the 2026-05-22 split.
    //
    // THE DISPATCH TEXT MUST NOT CARRY "error:" / "errno " OR A NEWLINE (#114,
    // reported by @Morac2, 2026-10-20). Pinokio 8.2 stops a shell.run step when
    // its OUTPUT matches /error:/i or /errno /i and ignores the exit code — and
    // the shell ECHOES the command before running it, so the fetch step's own
    //   [ -n "$U" ] || { echo 'FATAL error: no upstream configured'; exit 1; }
    // tripped that matcher ON THE SUCCESS PATH, before the step ran: Update
    // detached mid-echo with input.event ["error:"] and died. The fetch logic
    // therefore moved into scripts/pinokio/update_fetch.sh (behaviour byte-for-
    // byte unchanged); its FATAL lines now print only at runtime on a real
    // fault, where we still want Pinokio to stop.
    //
    // That same matcher retires the old "one joined string" note below: a
    // `"\n"`-joined message is itself a multi-line echo Pinokio trips on, so
    // every dispatch here is now a single `bash scripts/pinokio/*.sh` line and
    // the steps that need shell state across commands own it inside that script.
    // (The guard and converge steps already moved out for the 350-char ceiling;
    // this run finished the job for the fetch step and routed the three file
    // guards through scripts/pinokio/must.sh, which turns a missing script's
    // non-zero exit into a FATAL line at runtime instead of an echoed one.)
    //
    // v4.0 FIX — `git pull --ff-only $UPSTREAM` HAD NEVER ONCE SUCCEEDED.
    // `$UPSTREAM` expands to "origin/main", and `git pull --ff-only origin/main`
    // passes that as the REPOSITORY argument, not a refspec:
    //     fatal: 'origin/main' does not appear to be a git repository
    // So the ff-only attempt always failed and EVERY Phosphene update since
    // Y1.002 silently took the reset --hard fallback, printing "history
    // diverged from upstream" on a repo that had never diverged. Harmless in
    // practice (a panel install carries no local commits, and neither
    // `git pull --ff-only` nor `git reset --hard` touches untracked files) but
    // it is alarming noise and it meant we had no real ff-only guard at all.
    // The bare form uses the configured upstream and fast-forwards correctly;
    // the reset --hard fallback is kept, and now only fires when it is true.
    // Measured against the real update path, not reasoned about.
    // v4.0.1 FIX — THE UPDATE IS TRANSACTIONAL NOW, AND IT WAS NOT.
    //
    // The old block ignored the fetch result and sent EVERY pull failure to
    // `git reset --hard $UPSTREAM`. `$UPSTREAM` is the LOCAL tracking ref, so
    // when the fetch was the thing that failed the reset landed on the stale
    // ref that was already checked out — a no-op that exits 0. The sequence a
    // user offline (or behind an auth failure) actually got was:
    //
    //     failed fetch → failed pull → "successful" reset onto old code →
    //     successful rev-parse → post_update runs against the old tree →
    //     Update reports success and nothing has changed.
    //
    // The same fallback also could not tell a dirty worktree from genuine
    // divergent history, so it answered "you have local edits" by destroying
    // them.
    //
    // Split into two steps, both short enough for the dispatch gate, and the
    // ordering is the guarantee: NOTHING moves the tree until a fetch has
    // provably succeeded, and reset only ever runs against a ref that fetch
    // just refreshed.
    // A dispatch's own script can be MISSING (a damaged tree): `bash x.sh` then
    // exits 127 with a bare "No such file" that Pinokio does not stop on, and
    // Update marched on without a fetch (4.19.7 review). Each dispatch ends in
    // `|| [ -f x.sh ] || printf 'FATAL \145rror: ...'`: the file test keeps a
    // script's OWN failure (which already printed its FATAL line) quiet, and the
    // octal \145 ("e") prints the stop-word at RUNTIME without it ever sitting
    // in the echoed text (the #114 crash class; check_ltx_pin.js 2b).
    {
      method: "shell.run",
      params: {
        message: "bash scripts/pinokio/update_fetch.sh || [ -f scripts/pinokio/update_fetch.sh ] || printf 'FATAL \\145rror: scripts/pinokio/update_fetch.sh is missing - repair with: git checkout -- scripts/pinokio\\n'"
      }
    },
    // Converge. A reset is reached ONLY on proven history divergence.
    //
    // v4.0.1 second pass — THE UNTRACKED-FILE DELETION PATH. The previous
    // version classified every merge failure that left tracked files clean as
    // "divergence" and reset. But `git diff --quiet` IGNORES UNTRACKED FILES,
    // so the most common non-divergence failure — an untracked path that a
    // newly tracked upstream path would overwrite — passed both checks, and
    // `git reset --hard` then deleted the user's file as the "obstruction".
    // A user who dropped a note or a config beside the app lost it to an
    // update that had no local commits to reconcile in the first place.
    //
    // Divergence is now PROVEN, not inferred from what else failed:
    // `git rev-list --count $U..HEAD` is the number of commits this clone has
    // that upstream does not. Zero means the history did not diverge, whatever
    // made the merge fail — so we stop and print git's own error, which names
    // the obstructing paths. Nothing is deleted on that path, ever.
    // THE OBSTRUCTION GUARD, and it runs BEFORE anything can move the tree.
    //
    // The rev-list proof added in the previous pass only covered the
    // zero-local-commit case. A clone that has genuinely diverged (A > 0) AND
    // holds an untracked file where upstream now tracks one still reached
    // `reset --hard`, because both `git diff` checks ignore untracked files —
    // so the reset deleted it while legitimately reconciling real divergence.
    // Divergence and obstruction are independent facts and the destructive
    // step must clear BOTH.
    //
    // The set is computed from paths the fetched tree will write, then checked
    // against the worktree itself. That direction is deliberate: `git status`
    // omits ignored files and reports only leaf paths, so it misses both an
    // untracked FILE where upstream needs a DIRECTORY and an untracked
    // DIRECTORY where upstream needs a FILE. Walking each upstream path toward
    // its first existing worktree ancestor covers exact and parent/child
    // collisions, including ignored files, without enumerating a multi-GB
    // ignored cache. A path already present in HEAD is tracked and belongs to
    // the tracked-dirty/divergence checks below, not this obstruction set.
    //
    // v4.0.2 — A DIRECTORY IS NOT AN OBSTRUCTION, and treating it as one was a
    // fleet-wide REFUSAL TO UPDATE. The walk tested only `[ -e ]`, so it
    // stopped at the first ancestor that existed and flagged it without asking
    // what it was. git cannot create a directory where a FILE sits — a real
    // obstruction — but it writes a new file into an existing untracked
    // directory without complaint. So a release adding `notes/new.md` to a
    // clone with an untracked `notes/` aborted with
    // "OBSTRUCTIONS=[notes -> notes/new.md]" while `git merge --ff-only @{u}`
    // on that same clone succeeded and both files coexisted. Any release
    // adding a tracked file under a folder users commonly have untracked or
    // ignored (logs/, cache/, __pycache__/, mlx_models/, ltx-2-mlx/,
    // minimax-h3-mlx/, anything they dropped in the app dir) would have
    // refused to install itself, fleet-wide, including the fix for it.
    //
    // BOTH STEPS NOW LIVE IN scripts/pinokio/. They were 455 and 484 chars —
    // the two largest dispatches in the repo, both above the ~350 @Morac2
    // bisected as safe (#50) even though both sat under the 500 ceiling
    // `scripts/check_pinokio_scripts.js` enforces. As one-line `bash` calls
    // they are ~250, and the guard's rule is finally legible enough to argue
    // with: it is stated as a table of measured `git merge --ff-only`
    // outcomes in that file's header.
    //
    // These are the ONLY steps here that may not be delegated to
    // post_update.sh — they run BEFORE the pull. Reading them from
    // scripts/pinokio/ is therefore exactly as one-click-late as update.js
    // itself, no worse: the two land in the same commit, so a tree carrying
    // this update.js carries these scripts. If one is missing the tree is
    // damaged, and the guard MUST NOT be skipped — the destructive step below
    // is what it protects — so the step must abort with a repair instruction
    // instead of proceeding without it.
    //
    // That abort is now scripts/pinokio/must.sh's job, not an inline
    // `if [ -f ... ]; else echo 'FATAL error: ...'` (whose echoed "error:" was
    // the #114 crash). must.sh runs the guard; a missing guard makes `bash`
    // exit non-zero, and must.sh turns ANY non-zero exit into one
    // "FATAL error: <reason> (exit N)" line at RUNTIME, so Pinokio still stops
    // — without that word ever sitting in the echoed dispatch. The reason text
    // stays free of "error:" / "errno " for the same reason (it rides the
    // dispatch line too); check_pinokio_scripts.js enforces that.
    {
      method: "shell.run",
      params: {
        message: "bash scripts/pinokio/must.sh 'the update obstruction guard is missing - repair with: git checkout -- scripts/pinokio' bash scripts/pinokio/update_obstruction_guard.sh || [ -f scripts/pinokio/must.sh ] || printf 'FATAL \\145rror: scripts/pinokio/must.sh is missing - repair with: git checkout -- scripts/pinokio\\n'"
      }
    },
    {
      method: "shell.run",
      params: {
        message: "bash scripts/pinokio/must.sh 'the update converge step is missing - repair with: git checkout -- scripts/pinokio' bash scripts/pinokio/update_converge.sh || [ -f scripts/pinokio/must.sh ] || printf 'FATAL \\145rror: scripts/pinokio/must.sh is missing - repair with: git checkout -- scripts/pinokio\\n'"
      }
    },
    // ---- 2. Everything else, from the tree we just pulled ------------------
    // One step on purpose. v3.8.2's failure was a Pinokio RUN that stopped
    // after a step which exited 0, because that step's stdout carried pip's
    // "ERROR: pip's dependency resolver…" block — steps 8-17 of 18 never ran,
    // including the codec patch, and the update still presented as finished.
    // Inside one shell script Pinokio sees one step, so no amount of scary
    // text in the middle can silently drop the tail.
    //
    // `bash <path>`, never `./<path>` — no execute bit, nothing depends on git
    // preserving the mode. The missing-file concession to history (a tree that
    // somehow did not pull would otherwise hit a bare "No such file" and say
    // nothing useful) is now scripts/pinokio/must.sh's: it runs post_update.sh
    // and, if that is missing or fails, prints the one FATAL line Pinokio stops
    // on — at runtime, never in the echoed dispatch (the #114 "error:" crash).
    {
      method: "shell.run",
      params: {
        message: "bash scripts/pinokio/must.sh 'post_update did not run - the repo pull above may not have landed; re-run Update' bash scripts/post_update.sh || [ -f scripts/pinokio/must.sh ] || printf 'FATAL \\145rror: scripts/pinokio/must.sh is missing - repair with: git checkout -- scripts/pinokio\\n'"
      }
    }
  ]
}
