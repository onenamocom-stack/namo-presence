"""The presence gate.

Takes a decoded clip plus a character contract and returns a verdict. The gate is the
component that makes 200-500 episodes possible: it rejects takes mechanically so that
human review only ever sees candidates that already hold the performance.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any

from .measure import (
    Box, thumbnail, signature_diff, frame_deltas, duplicate_rate,
    motion_energy, motion_variance, accel_peaks, motion_texture, mouth_activity,
)

# Defaults are calibrated against a real 124s generated clip that read as
# "pleasant actor performing at camera" rather than divine stillness.
DEFAULTS: dict[str, float] = {
    "stillness_max": 4.0,      # mean inter-frame delta inside the character box
    "duplicate_max": 0.05,     # fraction of duplicated frames
    "expression_max": 0.55,    # mouth-region activity; high = talking head
    "eyeline_max_deg": 15.0,   # reserved for a landmark pass
    "accents_min": 1,          # gesture accents expected in a held shot
    "texture_min": 0.18,       # articulation vs uniform drift
}


@dataclass
class GateResult:
    accepted: bool
    reasons: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    character: str = ""
    presence_version: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    def summary(self) -> str:
        verdict = "ACCEPT" if self.accepted else "REJECT"
        head = f"[{verdict}] {self.character}@{self.presence_version}"
        if self.reasons:
            head += "  <- " + "; ".join(self.reasons)
        return head


def _thresholds(contract: dict, name: str) -> float:
    return float(contract.get("gates", {}).get(name, DEFAULTS[name]))


def evaluate(frames: list[tuple[int, int, bytes]], contract: dict,
             box: Box | None = None, tw: int = 32, th: int = 32) -> GateResult:
    """Score one clip.

    frames: list of (w, h, rgb) in playback order. Sampling 8-24 frames evenly across
    the clip is enough; decoding every frame is never required for a verdict.
    """
    contract_id = contract.get("id", "?")
    version = contract.get("presence_version", "0.0.0")

    if len(frames) < 3:
        return GateResult(False, ["too few frames to judge"], {}, contract_id, version)

    box = box or Box.full()
    thumb_sigs: list[bytes] = []
    lumas: list[bytes] = []
    mouth: list[float] = []
    for w, h, rgb in frames:
        thumb_sigs.append(thumbnail(rgb, w, h, tw, th, box))
        lumas.append(thumbnail(rgb, w, h, th, th, box))
        # mouth proxy uses the lower third of the character box
        mb = Box(box.x0 + (box.x1 - box.x0) * 0.28, box.y0 + (box.y1 - box.y0) * 0.58,
                 box.x0 + (box.x1 - box.x0) * 0.72, box.y0 + (box.y1 - box.y0) * 0.82)
        mouth.append(mouth_activity(rgb, w, h, mb))

    sigs = [signature_diff(thumb_sigs[i - 1], thumb_sigs[i]) for i in range(1, len(thumb_sigs))]
    deltas = frame_deltas(lumas)
    dup = duplicate_rate(deltas)
    energy = motion_energy(deltas)
    var = motion_variance(deltas)
    peaks = accel_peaks(deltas)
    texture = motion_texture(deltas)
    mouth_mean = sum(mouth) / len(mouth)
    mouth_var = max(mouth) - min(mouth) if mouth else 0.0

    metrics = {
        "frames": len(frames),
        "signature_mean_diff": round(sum(sigs) / len(sigs), 3) if sigs else 0.0,
        "motion_energy": round(energy, 3),
        "motion_variance": round(var, 3),
        "duplicate_rate": round(dup, 3),
        "accents": peaks,
        "motion_texture": round(texture, 3),
        "mouth_mean": round(mouth_mean, 3),
        "mouth_variance": round(mouth_var, 3),
    }

    reasons: list[str] = []
    if dup > _thresholds(contract, "duplicate_max"):
        reasons.append(f"duplicate_rate {dup:.2f} > {_thresholds(contract,'duplicate_max'):.2f}")
    if energy > _thresholds(contract, "stillness_max"):
        reasons.append(f"motion_energy {energy:.2f} > {_thresholds(contract,'stillness_max'):.2f} (too busy)")
    if mouth_var > _thresholds(contract, "expression_max"):
        reasons.append(f"mouth_variance {mouth_var:.2f} > {_thresholds(contract,'expression_max'):.2f} (performing)")
    if texture < _thresholds(contract, "texture_min") and energy > 0.8:
        reasons.append(f"motion_texture {texture:.2f} < {_thresholds(contract,'texture_min'):.2f} (uniform drift, no articulation)")

    return GateResult(not reasons, reasons, metrics, contract_id, version)


def review_batch(results: list[GateResult]) -> dict[str, Any]:
    """Aggregate accept rate across a batch — the number that decides throughput."""
    if not results:
        return {"count": 0, "accepted": 0, "accept_rate": 0.0, "failures": {}}
    failures: dict[str, int] = {}
    for r in results:
        for reason in r.reasons:
            key = reason.split()[0]
            failures[key] = failures.get(key, 0) + 1
    accepted = sum(1 for r in results if r.accepted)
    return {
        "count": len(results),
        "accepted": accepted,
        "accept_rate": round(accepted / len(results), 3),
        "failures": dict(sorted(failures.items(), key=lambda kv: -kv[1])),
    }
