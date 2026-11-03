"""Contract -> prompt compiler.

The core problem: 'Krishna' produces a pleasant actor. 'Krishna who has already seen this
happen' produces presence. This module turns the structured contract into the exact text a
video model needs, including the per-beat timeline the H3 graphs already accept.

Deterministic by design: the same contract and shot always compile to the same prompt,
so a season stays coherent and diffs are reviewable.
"""
from __future__ import annotations

from typing import Any

# Wording that reliably drags a model toward "performing at camera". Stripped from any
# prompt we emit, because negatives stated as positives are read as instructions.
PERFORMANCE_LEAKS = [
    "looking at", "turns to camera", "faces the viewer", "smiles warmly",
    "speaks excitedly", "gestures expressively", "lively", "animated", "charismatic",
    "charming", "engaging", "friendly", "warm smile", "bright eyes",
]

# How each negative is expressed as a positive physical fact. Models follow physics,
# not prohibitions.
NEGATIVE_TO_PHYSICAL = {
    "brow_raise":            "brows lowered and still, forehead smooth",
    "open_mouth_explain":    "jaw closed between phrases, no mid-word gaping",
    "head_tilt":             "head level, no tilt toward camera",
    "gesture_midword":       "hands settled before the line begins, not raised mid-sentence",
    "eyeline_to_lens":       "eyes fixed past the other person, never into the lens",
    "smile_complete":        "no full smile; the mouth stays at rest",
    "shifting_while_speaking": "weight already settled, no shifting while speaking",
    "same_beat_reaction":    "reacts one beat after the other speaks, not on the same beat",
    "forward_lean_to_other": "shoulders back, upright, does not lean in",
}


def _as_text(v: Any) -> str:
    if isinstance(v, str):
        return v
    if isinstance(v, dict):
        return v.get("do", "")
    return ""


def _never(v: Any) -> str:
    if isinstance(v, dict):
        return v.get("never", "")
    return ""


def presence_block(contract: dict) -> str:
    """The six-line behavioural spine that goes at the top of every prompt."""
    p = contract.get("presence", {})
    lines = []
    for key in ("gaze", "mouth", "hands", "head", "timing", "weight", "breath"):
        t = _as_text(p.get(key))
        if t:
            lines.append(f"- {t}")
    return "\n".join(lines)


def physical_negatives(contract: dict) -> str:
    out = [NEGATIVE_TO_PHYSICAL[n] for n in contract.get("negatives", [])
           if n in NEGATIVE_TO_PHYSICAL]
    return "; ".join(out)


def scrub(text: str) -> str:
    """Remove phrasing that pulls the model toward a performance."""
    lowered = text.lower()
    for leak in PERFORMANCE_LEAKS:
        if leak in lowered:
            # drop the offending clause
            parts = [p for p in text.split(",") if leak not in p.lower()]
            text = ",".join(parts)
            lowered = text.lower()
    return text.strip().strip(",").strip()


def compile_shot(contract: dict, shot: dict, style_lock: str = "",
                 location: str = "", seconds: float | None = None) -> str:
    """Compile one shot into a full H3 prompt with a directed timeline."""
    name = contract.get("id", "character")
    sec = seconds or float(shot.get("sec", 4))
    frame = scrub(shot.get("frame", ""))
    move = shot.get("move", "static")
    beats = shot.get("beats") or []

    head = []
    if style_lock:
        head.append(style_lock)
    if location:
        head.append(location)
    head.append(
        f"{name}. {frame}. {presence_block(contract)}"
    )
    phys = physical_negatives(contract)
    if phys:
        head.append(f"Physical facts: {phys}.")

    # Timeline: if the shot defines beats, use them. Otherwise derive a hold-first
    # timeline, which is what gives presence: settle, hold, one accent, hold.
    tl = ["Timeline:"]
    if beats:
        for b in beats:
            tl.append(f"[{b['from']:.1f}s-{b['to']:.1f}s] {scrub(b['action'])}")
    else:
        a = sec * 0.30
        b = sec * 0.70
        tl.append(f"[0s-{a:.1f}s] Settles into the frame; weight already on the back foot; no new gesture.")
        tl.append(f"[{a:.1f}s-{b:.1f}s] Almost still, {move}; only breath and ambient light move. Eyes stay past the other person.")
        tl.append(f"[{b:.1f}s-{sec:.1f}s] One small accent, then stops; no cut, no new action, holds to the end.")

    return "\n\n".join(head) + "\n\n" + "\n".join(tl) + "\n"


def compile_keyframe(contract: dict, shot: dict, style_lock: str = "",
                     location: str = "") -> str:
    """Still-image prompt. The still must already carry presence — I2V animates what it is given."""
    name = contract.get("id", "character")
    frame = scrub(shot.get("frame", ""))
    parts = [style_lock, location, f"{name}. {frame}."]
    parts.append(
        "Single frame from a film, not a portrait. The figure is not aware of the camera."
    )
    pb = presence_block(contract)
    if pb:
        parts.append(pb)
    phys = physical_negatives(contract)
    if phys:
        parts.append(f"Physical facts: {phys}.")
    return " ".join(p for p in parts if p)


def diff(a: str, b: str) -> list[str]:
    """Line-level diff so a contract change is reviewable before it costs renders."""
    import difflib
    return [l for l in difflib.unified_diff(a.splitlines(), b.splitlines(),
                                            lineterm="", n=0) if l[:1] in "+-" and l[:3] not in ("+++", "---")]
