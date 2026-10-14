# Audio — lip-sync, talking photos, dubbing a song

The Audio tab makes a **video driven by a sound file you bring** — a voice line, a song. The sound steers the picture while it is generated (it is not laid on afterwards), so a mouth moves to the words and motion follows the music. It does not make sound from text; for that, describe the sound in any Video prompt.

This is Phosphene's lip-sync: a picture (optional) and a stretch of audio become one clip whose mouth follows the track — the same idea as a "talking photo" or dubbing a performance to a new soundtrack. There is also a **Lip-sync** chip on the Video tab's mode bar that jumps straight here.

## Making one {#make}

1. Drop the **Audio** — WAV, MP3, M4A or FLAC. The panel reads its length and defaults Duration to match it. Audio already in Outputs (a song you composed, a voice line) needs no Finder trip: **Use…** on its card → **Lip-sync**.
2. Optional: a **Reference image** to open the clip on that frame — a portrait for a talking head. Leave it empty for pure audio-to-video. A closed-mouth, face-forward picture works best — see the tip below.
3. Write the prompt. **Enhance** rewrites it for the model. Keep it short (under ~40 words) and describe the performance (expression, gesture, breathing) rather than the scene or the sound — a sync sentence is appended automatically.
4. Set **Width** and **Height** (default 1024×576), **Start at** (seconds into the file, e.g. 30 to drive the clip from 0:30) and **Duration** (1–30 s). A start or a window that runs past the end of the file is refused or warned about before you render — that stretch would be silence, and silence is exactly where the mouth freezes.
5. **Audio conditioning strength**: leave it on **Auto** unless the mouth is clearly too loose or too stiff — the two render lanes (Q8 / Q4) read this number differently, and Auto is the only value that is always correct for the Mac you're on.
6. **Generate**.

> **Tip** If the mouth does not move: (1) start on a closed-mouth, face-forward picture — an open-mouth anchor tends to freeze open, and the panel warns when it detects one; (2) remove "static" / "holds still" / "motionless" language from the prompt — a stillness word freezes the whole scene, mouth included; (3) for a song, try the vocal-only option so the model listens to the voice instead of the full mix. A large canvas with a long duration is a very long render; the tab warns you and suggests a smaller size.

**Continue the song** (on the finished clip's player): starts the next clip on THIS clip's most-closed-mouth frame near its end — not the literal last frame, which is usually mid-syllable — with Start at advanced to match. Chain a whole song this way without a jump cut at every join.

Audio always renders on LTX: on the High (Q8) pipeline when it is installed, otherwise on the fast Q4 pipeline (also the lane used on Macs under 64 GB).
