"""Character bundle runtime — the panel imports this to drive the
character picker, the auto-router, and the dialogue helper.

Reads `mlx_models/characters/<trigger>/bundle.json` files. Also scans
`mlx_models/loras/*.safetensors` for legacy flat LoRAs (one per file)
and presents them as synthetic face-only characters so the picker
shows everything.

Public surface:

    list_characters()        → list[CharacterRow]
    resolve_character(id)    → CharacterBundle
    auto_route(p, character) → mutated params dict (LoRAs + quality)
    build_dialogue_snippet(bundle, line, action=None) → str

This module is import-safe with no side effects. Designed to be copied
into mlx_ltx_panel.py or imported via sys.path. Has no dependencies on
phosphene-dev itself (only on the filesystem layout).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional


# Default locations — derived from the panel install root the same
# way the other vendored lora_lab modules resolve their model dir.
# Override either by reassigning these constants at import time or by
# setting LTX_MODELS_DIR which the resolver also honors.
def _default_models_root() -> Path:
    import os
    env_dir = os.environ.get("LTX_MODELS_DIR")
    if env_dir:
        return Path(env_dir).expanduser()
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "mlx_models"
        if candidate.is_dir():
            return candidate
        if (parent / ".git").exists():
            return parent / "mlx_models"
    return here.parents[1] / "mlx_models"

_MODELS_ROOT = _default_models_root()
CHARACTERS_DIR = _MODELS_ROOT / "characters"
LORAS_DIR = _MODELS_ROOT / "loras"


@dataclass
class CharacterRow:
    """One row in the character picker UI."""
    id: str
    name: str
    has_face: bool
    has_voice: bool
    preview_path: Optional[str]  # absolute, or None
    source: str                  # "bundle" | "legacy_flat"


@dataclass
class CharacterBundle:
    """Resolved bundle ready for generation. All paths absolute."""
    id: str
    name: str
    pronoun: str            # "he" | "she" | "they"
    subject_noun: str       # "man" | "woman" | "person"
    default_action: str
    face_lora_path: str
    face_strength: float
    audio_lora_path: Optional[str]
    audio_strength: float
    voice_clip_path: Optional[str]
    preview_path: Optional[str]
    bundle_json: dict       # the full manifest, for advanced consumers


# ---------------------------------------------------------------------------
# Listing

def list_characters(
    characters_dir: Path = CHARACTERS_DIR,
    loras_dir: Path = LORAS_DIR,
) -> list[CharacterRow]:
    """Walk both bundle and legacy-flat locations. Return one row per character."""
    rows: list[CharacterRow] = []

    # Bundle directories
    if characters_dir.is_dir():
        for sub in sorted(characters_dir.iterdir()):
            if not sub.is_dir():
                continue
            manifest = sub / "bundle.json"
            if not manifest.is_file():
                continue
            try:
                bundle = json.loads(manifest.read_text())
            except Exception:
                continue
            if bundle.get("schema") != "phosphene/character_bundle@1":
                continue
            preview = bundle.get("preview")
            preview_path = (
                str(sub / preview["path"])
                if (preview and preview.get("path") and (sub / preview["path"]).exists())
                else None
            )
            rows.append(CharacterRow(
                id=bundle["id"],
                name=bundle.get("name", bundle["id"]),
                has_face=bool(bundle.get("face")),
                has_voice=bool(bundle.get("voice")),
                preview_path=preview_path,
                source="bundle",
            ))

    # Legacy flat LoRAs — scan .safetensors that aren't part of a bundle and
    # don't look like internal/utility files (skip *.video_attn_only.safetensors
    # which are individual bundle inputs, not standalone character LoRAs).
    bundle_ids = {r.id for r in rows}
    if loras_dir.is_dir():
        for fp in sorted(loras_dir.glob("*.safetensors")):
            name = fp.stem
            # Skip files that look like bundle inputs already represented in a bundle
            if name.endswith(".video_attn_only"):
                continue
            base = name.split(".")[0]  # bizarrotrn.voice -> bizarrotrn; bizarrotrn -> bizarrotrn
            if base in bundle_ids:
                continue
            rows.append(CharacterRow(
                id=name,
                name=name,
                has_face=True,
                has_voice=False,
                preview_path=None,
                source="legacy_flat",
            ))

    return rows


def resolve_character(
    character_id: str,
    characters_dir: Path = CHARACTERS_DIR,
    loras_dir: Path = LORAS_DIR,
) -> CharacterBundle:
    """Look up a character by ID. Returns a resolved bundle with absolute paths.

    Raises FileNotFoundError if neither a bundle nor a legacy flat LoRA matches.
    """
    bundle_dir = characters_dir / character_id
    manifest = bundle_dir / "bundle.json"
    if manifest.is_file():
        bundle = json.loads(manifest.read_text())
        face = bundle["face"]
        voice = bundle.get("voice")
        preview = bundle.get("preview")
        return CharacterBundle(
            id=bundle["id"],
            name=bundle.get("name", bundle["id"]),
            pronoun=bundle.get("pronoun", "they"),
            subject_noun=bundle.get("subject_noun", "person"),
            default_action=bundle.get("default_action", "smiles softly"),
            face_lora_path=str(bundle_dir / face["path"]),
            face_strength=float(face.get("recommended_strength", 1.0)),
            audio_lora_path=str(bundle_dir / voice["path"]) if voice else None,
            audio_strength=float(voice.get("recommended_strength", 1.0)) if voice else 0.0,
            voice_clip_path=(
                str(bundle_dir / voice["voice_clip"])
                if (voice and voice.get("voice_clip"))
                else None
            ),
            preview_path=(
                str(bundle_dir / preview["path"])
                if (preview and preview.get("path"))
                else None
            ),
            bundle_json=bundle,
        )

    # Legacy flat fallback
    flat = loras_dir / f"{character_id}.safetensors"
    if flat.is_file():
        return CharacterBundle(
            id=character_id,
            name=character_id,
            pronoun="they",
            subject_noun="person",
            default_action="smiles softly",
            face_lora_path=str(flat),
            face_strength=1.0,
            audio_lora_path=None,
            audio_strength=0.0,
            voice_clip_path=None,
            preview_path=None,
            bundle_json={"source": "legacy_flat", "id": character_id},
        )

    raise FileNotFoundError(f"character not found: {character_id}")


# ---------------------------------------------------------------------------
# Auto-router — picks pipeline + LoRA set based on bundle + mode + WAV input

def auto_route(p: dict, character_id: str, **kwargs) -> dict:
    """Mutate `p` to attach LoRAs + force HQ quality based on the bundle.

    Args:
        p: the generation params dict (mode, audio, mode_override, ...)
        character_id: the character to use
        characters_dir, loras_dir (kwargs): override default locations

    Returns:
        A NEW dict with `loras` set, `quality="high"`, `character_id` recorded.
        Doesn't mutate the caller's dict.
    """
    bundle = resolve_character(
        character_id,
        characters_dir=kwargs.get("characters_dir", CHARACTERS_DIR),
        loras_dir=kwargs.get("loras_dir", LORAS_DIR),
    )

    mode = p.get("mode") or "t2v"
    wav_input = (
        p.get("audio") and Path(p["audio"]).exists()
        and mode in ("a2v", "i2v")
    )

    loras: list[dict] = [{
        "path": bundle.face_lora_path,
        "strength": bundle.face_strength,
    }]

    # Attach voice LoRA only when:
    #  - bundle has a voice LoRA
    #  - mode is t2v or i2v
    #  - user did NOT provide a WAV input (in which case the WAV wins)
    if bundle.audio_lora_path and mode in ("t2v", "i2v") and not wav_input:
        loras.append({
            "path": bundle.audio_lora_path,
            "strength": bundle.audio_strength,
        })

    return {
        **p,
        "loras": loras,
        "character_id": bundle.id,
        "quality": "high",  # dev-trained LoRAs require the HQ dev path
    }


# ---------------------------------------------------------------------------
# Dialogue helper — splice "<line> + <action>" into a prompt cleanly

def build_dialogue_snippet(
    bundle: CharacterBundle | dict,
    line: str,
    action: Optional[str] = None,
    delivery: Optional[str] = None,   # e.g. "deadpan", "softly", "dry and amused"
) -> str:
    """Build the canonical dialogue snippet:

        The {subject_noun} says aloud{', ' + delivery}: "<line>". After speaking, {pronoun} {action}.

    The subject_noun comes from the bundle ("man" / "woman" / "person").
    If `action` is None, uses bundle's default_action.

    Pass either a CharacterBundle or a dict (the panel may have only the json).
    """
    if isinstance(bundle, CharacterBundle):
        pronoun = bundle.pronoun
        subject = bundle.subject_noun
        default_action = bundle.default_action
    else:
        pronoun = bundle.get("pronoun", "they")
        subject = bundle.get("subject_noun", "person")
        default_action = bundle.get("default_action", "smiles softly")

    action_text = action.strip() if action and action.strip() else default_action.strip()
    line_text = line.strip()
    # Strip surrounding quotes if user pasted them
    if line_text.startswith('"') and line_text.endswith('"'):
        line_text = line_text[1:-1]
    if line_text.startswith("'") and line_text.endswith("'"):
        line_text = line_text[1:-1]

    delivery_part = f", {delivery.strip()}" if delivery and delivery.strip() else ""

    pronoun_capitalized = pronoun.capitalize() if pronoun not in ("they",) else pronoun.capitalize()
    return (
        f'The {subject} says aloud{delivery_part}: "{line_text}". '
        f'After speaking, {pronoun} {action_text}.'
    )


# ---------------------------------------------------------------------------
# Self-test

def _self_test() -> int:
    print("=== character_runtime self-test ===")
    print(f"characters_dir: {CHARACTERS_DIR}")
    print()

    rows = list_characters()
    print(f"list_characters() → {len(rows)} entries:")
    for r in rows:
        chips = []
        if r.has_face: chips.append("face")
        if r.has_voice: chips.append("voice")
        print(f"  [{r.source:13s}] {r.id:30s} [{', '.join(chips):12s}]  preview={'yes' if r.preview_path else '—'}")

    print()
    for row in rows:
        if row.source != "bundle":
            continue
        bundle = resolve_character(row.id)
        print(f"resolve_character({row.id!r}):")
        print(f"  face: {Path(bundle.face_lora_path).name}")
        if bundle.audio_lora_path:
            print(f"  voice: {Path(bundle.audio_lora_path).name}")
        if bundle.preview_path:
            print(f"  preview: {Path(bundle.preview_path).name}")
        print(f"  pronoun: {bundle.pronoun}, subject: {bundle.subject_noun}")
        print(f"  default_action: {bundle.default_action}")

        # auto_route smoke
        p = {"mode": "t2v", "prompt": "test"}
        routed = auto_route(p, row.id)
        print(f"  auto_route(mode=t2v) → {len(routed['loras'])} LoRA(s), quality={routed['quality']}")

        # dialogue snippet smoke
        snippet = build_dialogue_snippet(
            bundle, "Hello, world", delivery="deadpan"
        )
        print(f"  dialogue snippet: {snippet[:120]}...")
        print()

    return 0


if __name__ == "__main__":
    import sys
    sys.exit(_self_test())
