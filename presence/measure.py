"""Frame measurement primitives.

Everything here works on raw RGB24 bytes produced by the ffmpeg decoder, so it runs
offline on any clip without a GPU. No dependencies beyond the standard library.

Coordinate convention: functions take (rgb, w, h) where rgb is a w*h*3 bytes object.
"""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Box:
    """Normalised bounding box. (0,0) is top-left, (1,1) is bottom-right."""
    x0: float
    y0: float
    x1: float
    y1: float

    def clamp(self) -> "Box":
        return Box(
            max(0.0, min(self.x0, 1.0)), max(0.0, min(self.y0, 1.0)),
            max(0.0, min(self.x1, 1.0)), max(0.0, min(self.y1, 1.0)),
        )

    def px(self, w: int, h: int) -> tuple[int, int, int, int]:
        b = self.clamp()
        return (int(b.x0 * w), int(b.y0 * h), int(b.x1 * w), int(b.y1 * h))

    @staticmethod
    def full() -> "Box":
        return Box(0.0, 0.0, 1.0, 1.0)


def luma(rgb: bytes, w: int, h: int, box: Box | None = None) -> bytes:
    """Return one luma byte per pixel inside box, row-major."""
    box = box or Box.full()
    x0, y0, x1, y1 = box.px(w, h)
    out = bytearray((x1 - x0) * (y1 - y0))
    o = 0
    for y in range(y0, y1):
        base = y * w * 3
        for x in range(x0, x1):
            i = base + x * 3
            # integer Rec.601 weights
            out[o] = (rgb[i] * 77 + rgb[i + 1] * 150 + rgb[i + 2] * 29) >> 8
            o += 1
    return bytes(out)


def thumbnail(rgb: bytes, w: int, h: int, tw: int = 32, th: int = 32,
              box: Box | None = None) -> bytes:
    """Coarse luma signature of size tw*th, used for cheap frame comparison."""
    box = box or Box.full()
    x0, y0, x1, y1 = box.px(w, h)
    bw = max(1, (x1 - x0))
    bh = max(1, (y1 - y0))
    out = bytearray(tw * th)
    k = 0
    for by in range(th):
        yy = y0 + (by * bh) // th
        base = yy * w * 3
        for bx in range(tw):
            xx = x0 + (bx * bw) // tw
            i = base + xx * 3
            out[k] = (rgb[i] * 77 + rgb[i + 1] * 150 + rgb[i + 2] * 29) >> 8
            k += 1
    return bytes(out)


def signature_diff(a: bytes, b: bytes) -> float:
    """Mean absolute difference between two signatures on the 0-255 scale."""
    if len(a) != len(b):
        raise ValueError("signature size mismatch")
    return sum(abs(x - y) for x, y in zip(a, b)) / float(len(a))


def frame_deltas(lumas: list[bytes]) -> list[float]:
    """Mean absolute luma delta between consecutive frames (0-255 scale)."""
    if len(lumas) < 2:
        return []
    n = len(lumas[0])
    return [
        sum(abs(a - b) for a, b in zip(lumas[i - 1], lumas[i])) / float(n)
        for i in range(1, len(lumas))
    ]


def duplicate_mask(deltas: list[float], threshold: float = 0.5) -> list[bool]:
    """True where a frame is a duplicate of the one before it."""
    return [d < threshold for d in deltas]


def duplicate_rate(deltas: list[float], threshold: float = 0.5) -> float:
    if not deltas:
        return 0.0
    return sum(1 for d in deltas if d < threshold) / float(len(deltas))


def motion_energy(deltas: list[float]) -> float:
    """Mean frame-to-frame delta. Low = drifting, high = weighted motion."""
    return sum(deltas) / float(len(deltas)) if deltas else 0.0


def motion_variance(deltas: list[float]) -> float:
    """Spread of motion. Near-zero means a constant drift (float-y, unweighted)."""
    if len(deltas) < 2:
        return 0.0
    m = motion_energy(deltas)
    return sum((d - m) ** 2 for d in deltas) / float(len(deltas))


def accel_peaks(deltas: list[float], floor: float = 1.0) -> int:
    """Count frames where motion sharply rises then falls — gesture accents.

    A performance with weight has accents: the hand lands, the head settles.
    A drifting performance has none.

    Two guards matter here. First, the comparison uses the neighbourhood on the same
    side of any zero the signal drops into, because generated clips alternate real
    motion with duplicate frames and a raw local-max test fires on the recovery
    instead of on the gesture. Second, frames at or below `floor` are ignored so that
    duplicate/repeat boundaries never read as accents.
    """
    peaks = 0
    for i in range(1, len(deltas) - 1):
        if deltas[i] <= floor:
            continue
        if not (deltas[i] > deltas[i - 1] and deltas[i] > deltas[i + 1]):
            continue
        local = max(deltas[i - 1], deltas[i + 1])
        if local <= floor:          # neighbour is a duplicate-frame artefact, not a lull
            continue
        if deltas[i] >= local * 1.35:
            peaks += 1
    return peaks


def motion_texture(deltas: list[float], floor: float = 1.0) -> float:
    """How much of the motion is articulation rather than a uniform drift.

    Ratio of the standard deviation of the non-artefact deltas to their mean. A clip
    that drifts at a constant rate scores near zero; a performance with gestures and
    settles scores well above it. This is the signal that separates 'alive' from
    'sliding', and it is stable against duplicate frames.
    """
    real = [d for d in deltas if d > floor]
    if len(real) < 3:
        return 0.0
    m = sum(real) / len(real)
    if m <= 0:
        return 0.0
    var = sum((d - m) ** 2 for d in real) / len(real)
    return (var ** 0.5) / m


def mouth_activity(rgb: bytes, w: int, h: int, box: Box) -> float:
    """Crude mouth-region motion proxy.

    Measures the share of the lower-centre face box that is dark or very saturated,
    which correlates with an open mouth in a talking close-up. Not a landmark
    detector; it is a cheap monotone signal for 'is this face performing'.
    """
    x0, y0, x1, y1 = box.px(w, h)
    if x1 <= x0 or y1 <= y0:
        return 0.0
    dark = 0
    total = 0
    for y in range(y0, y1):
        base = y * w * 3
        for x in range(x0, x1):
            i = base + x * 3
            r, g, b = rgb[i], rgb[i + 1], rgb[i + 2]
            mx, mn = max(r, g, b), min(r, g, b)
            if mx < 55 or (mx - mn) > 90:   # oral cavity or heavily saturated lip
                dark += 1
            total += 1
    return dark / float(total) if total else 0.0
