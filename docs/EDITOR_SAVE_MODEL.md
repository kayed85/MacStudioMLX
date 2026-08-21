# The Editor's save model — an owner architecture ruling

Status: **ruling**, 2026-08-19. Supersedes the two-lane save/backup design that
grew up between `save_edit`, `write_backup` and the recovery bar.

## The ruling, in the owner's words

> "Either the autosave is something on the side that you can always go and
> check the older versions... it only saves the real saves that are manually
> saved and named by the author. We start working in a new timeline, you put a
> name and you save it, and then you save it manually every time. But if you
> forgot to save, you have drafts; you have versioning that you can go back to
> if you want. Like in a normal program. It's intertwined in a way that is
> causing a lot of problems."

The last sentence is the defect. Everything below follows from separating the
two things that were intertwined.

## What went wrong

The editor had **two writers of record** and no rule about which one won.

`save_edit()` writes `edit.json` — the document. `write_backup()` writes
`history/backup-<draft>.json` — a parallel, complete, validated document with
its own revision stamp. On load, `pending_backup()` compared them and, on any
difference, raised a full-width bar offering to install one over the other.

That is not a safety net. It is a second head on the same body, and it
produced, in one session:

* **A permanent interrogation.** The comparison was `json.dumps(clips)` on
  both sides. But `_sbe_payload` REWRITES every clip's `proxy` pointer on the
  way out ("a proxy built after the edit was saved must not need a re-save to
  become visible"), so the client's copy legitimately differs from the file
  for a field the user has never heard of. Any board whose proxies were built
  after its last save compared unequal forever. The bar that resulted read
  *"A backup from 3 min ago holds 10 clip(s), 0:55.11 — the saved draft has
  10. Nothing has been changed."* — a question about a difference it could not
  name, on a document that had loaded correctly.
* **A stale tab stomping a live one.** A second tab left open on revision 93
  kept writing its own state into the single backup slot on every debounce.
  Minutes after a rev-94 save landed, the backup lane held rev-93 content and
  the offer re-armed. There was no notion of which session was current.
* **A lane that could refuse.** `write_backup` validated like a real save, so
  a document the validator disliked lost BOTH its save and its safety net at
  once. (Fixed in ccae93b for overlap; the shape of the bug was the coupling.)

## The model

### 1. Manual saves are the only truth

A timeline is a **named draft**. `Save` (and ⌘S) writes that draft's
`edit.json` and archives the outgoing document into the draft's history. The
revision counter belongs to the file on disk and moves forward only there.

**Loading a draft always loads its last manual save.** Nothing else is ever
installed on load, by anything, for any reason. This is the property the whole
model exists to guarantee, and it is the one that was negotiable before.

The header says one honest thing: `draft name · saved rev N`, or
`unsaved changes`. Never both, never a third state.

### 2. Autosave is a side archive, full stop

The autosnapshot lane writes continuously and **has no opinions**. It
- never blocks a save,
- never refuses a document (only the errors that would make a snapshot
  unrestorable),
- never competes on load,
- never interrogates the user.

It is bounded and pruned. Losing the oldest snapshot is not an event.

### 3. One versions browser per draft

Manual saves, named versions and autosnapshots are listed **together and
visually distinct**, newest first:

| lane | prefix | pruned | meaning |
|---|---|---|---|
| named | `keep-r*` | never | a version a person stopped to name |
| manual | `save-r*` | never | a save the user pressed |
| auto | `edit-r*` | capped | a snapshot the machine took |

One click restores. **Restore archives the current state first**, so restoring
can never lose anything — including restoring by mistake.

### 4. No recovery wall

If the newest snapshot is newer **and content-different** from the last manual
save, the panel shows **one quiet, dismissible chip**, folded into the single
notice surface: *"Unsaved snapshot from 14:52 — open Versions."* It is an
offer to go and look, not a question that must be answered before working.

**Content-equal shows nothing at all.** Equality is judged by
`edit_digest()`, a canonical fingerprint of the things a person can see and
change — the clip windows, the film slots, the sound, the adjustments, the
soundtrack, the beats — and explicitly NOT of derived or server-rewritten
fields (`proxy`, `revision`, `updated_at`, `origin`, timestamps, the backup's
own bookkeeping). A field the user cannot see may never be the reason they are
asked a question.

### 5. Multi-writer sanity

Each tab holds a **session token**, and the board records which session last
**wrote**. That is all it records, and the only thing it is for is telling a
person that somebody else is editing the same film.

**The claim is taken by writing, never by loading, and it never refuses
anybody.** Both halves of that sentence are paid for.

This used to read: the claim goes to whoever loaded last, and a writer from an
older session is *refused* — it stops writing and says so. It cost the owner an
afternoon on 2026-08-20. A page LOAD claimed the board, so a second window, a
headless browser, an agent reading the board or a preview took the claim
without editing anything; the tab he was actually cutting in was answered
`stale_session` on its next snapshot and stopped writing for seven hours. What
made it fatal rather than annoying is that it went quiet: the tab set a flag,
showed one nine-second toast, and the state line it wrote was overwritten by
the very next edit. The 12-second watchdog built to catch exactly this could
not fire, because the early return sat ABOVE the line that arms its clock.

The refusal was written for a lane with ONE slot per draft, where a stale tab's
write destroyed newer content. §2 removed that lane — one file per snapshot,
pruned, never overwritten — and the refusal outlived its reason. A snapshot
from any tab now costs one file and can only ever ADD a way back; refusing one
can only ever remove one.

Server-side and agent edits are just normal saves. A client sitting on an older
revision gets a quiet chip — *"draft advanced to rev N — reload"* — not a
conflict wall. A conflict wall is for two humans editing the same second; a
revision moving forward underneath a tab that has not touched anything is not
that.

### 5b. `expect_revision` is a compare-and-swap, and both halves are in one place

The guard existed and a race walked straight through it. The HTTP handler read
the revision off disk, compared it, validated, and only *then* called
`save_edit` — and the panel runs on a `ThreadingHTTPServer`. Two tabs whose
debounces landed together both read revision 7, both compared 7 == 7, and both
wrote. **Both got HTTP 200.** One arrangement was gone, recoverable only from
`history/` and only by somebody who knew to look.

So the compare and the swap are now the same critical section, and it lives in
`save_edit()` — the function every writer already goes through — rather than in
the handler, because a guard the caller has to remember to take is a guard.

* `save_edit(board_dir, edit, expect=N)` takes the board's write lock, re-reads
  the on-disk revision **inside** it, and raises `EditConflict` (a subclass of
  `EditError`, so every existing `except` still refuses the write) if it moved.
* The lock is **per board**, keyed by the resolved directory. Two people
  cutting two films never queue behind one another; two tabs on one film
  always do.
* The handler's early check stays, as the *early answer* — so an overtaken tab
  is told without the server first validating and archiving a document it is
  going to refuse. The loser gets the same 409 body either way, which is the
  one the client already knows how to answer.

**A save with no `expect_revision` is accepted, and logged.** Refusing it was
the other option and it is the wrong one: the client deliberately sends no
guard for the "Keep mine" button — a person looking at a conflict notice and
choosing to overwrite — and a 409 that button cannot answer would strand the
arrangement on screen. What changes is that the write stops being silent: the
log names the revision it landed on and says "last write wins", and the
outgoing document is in `history/` either way. A script or an agent that omits
the guard is therefore visible rather than invisible.

### 6. Migration is lossless

Nothing on disk is deleted. The single `history/backup-<draft>.json` is read
as the newest autosnapshot of that draft and folded into the versions list.
Old flat history layouts continue to migrate into per-draft folders as they
already do.

## Invariants a test must hold

1. `load_edit()` returns the last manual save, always. No lane can change that.
2. Content-equal snapshot → `pending_backup()` is `None` → nothing on screen.
3. A `proxy` pointer, a `revision`, or a timestamp differing is not a content
   difference.
4. A snapshot is never refused on session, and a READ never takes the claim —
   a passive viewer cannot disarm the writer. The lane also never writes a
   snapshot identical to its newest (`edit_digest`), so a debounce that fires
   over an unchanged film does not spend the cap.
5. `restore_edit()` archives the current document before overwriting it.
6. The autosnapshot lane never raises on a document a manual save would take.
7. Peaks are invalidated when the soundtrack path changes — a cache that
   outlives its subject is the music strip reading `44.99s` under a different
   file.
8. Two saves carrying the SAME `expect_revision` cannot both be answered 200,
   however close together they arrive. One writes; the other gets the 409 with
   the revision it was overtaken by. Driven with two real threads held past the
   read-and-compare, because a serial test cannot see this defect at all.

## Deliberately not built

* **Merging.** Two divergent arrangements are not mergeable in a way anybody
  would trust. The answer is versions and a restore that archives first.
* **Autosave to `edit.json`.** Explicitly refused by the owner, twice. The
  side archive exists precisely so this stays refused.
* **A lock on the SNAPSHOT lane.** Two tabs both writing snapshots is not a
  conflict — it is two ways back instead of one. The document itself is
  protected by `expect_revision` on the manual save, which is where a conflict
  can actually do damage — and that guard is now atomic per board (§5b). The
  lock there is around one read-check-write, not around a session.

## The rule the whole file reduces to

A control that has stopped protecting the user **says so, loudly, and does not
stop trying**. Every defect this document records is the same shape: something
switched the net off for a defensible local reason and nothing told anybody.
`if (SBE.backup) return false`, then `if (SBE.superseded) return`. The next one
will look reasonable too — so the test to apply to any new guard on a write
path is not "is this correct" but "what does the person see if it fires".
