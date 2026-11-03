"""Tests for the presence layer.

Run: python3 -m pytest tests/ -q    (or python3 tests/test_presence.py for a no-pytest run)
No network, no GPU, no ffmpeg: everything is synthesised frames plus the contract.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from presence import Box, evaluate, review_batch, compile_shot, compile_keyframe, scrub, diff
from presence.measure import (
    thumbnail, signature_diff, frame_deltas, duplicate_rate,
    motion_energy, motion_texture, accel_peaks, mouth_activity,
)

W, H = 64, 114


def solid(v):
    return bytes([v, v, v]) * (W * H)


def ramp(offset):
    """A frame that moves: a bright band at a varying vertical position."""
    buf = bytearray(W * H * 3)
    y0 = int(offset * (H - 8))
    for y in range(y0, min(y0 + 8, H)):
        for x in range(W):
            i = (y * W + x) * 3
            buf[i] = buf[i + 1] = buf[i + 2] = 220
    return bytes(buf)


KRISHNA = {
    "id": "krishna",
    "presence_version": "1.0.0",
    "presence": {
        "gaze": "eyes fixed past the other person",
        "mouth": "jaw closed at rest",
        "hands": "hands low and still",
        "head": "head level and heavy",
        "timing": "reacts one beat late",
        "weight": "settled before the shot",
    },
    "negatives": ["brow_raise", "open_mouth_explain", "eyeline_to_lens"],
    "gates": {"stillness_max": 3.0, "duplicate_max": 0.10,
              "expression_max": 0.5, "texture_min": 0.15},
}


def test_signature_is_stable_for_identical_frames():
    f = solid(128)
    a = thumbnail(f, W, H, 16, 16)
    b = thumbnail(f, W, H, 16, 16)
    assert a == b
    assert signature_diff(a, b) == 0.0


def test_signature_differs_when_content_moves():
    a = thumbnail(ramp(0.0), W, H, 16, 16)
    b = thumbnail(ramp(0.9), W, H, 16, 16)
    assert signature_diff(a, b) > 1.0


def test_duplicate_rate_detects_frozen_clip():
    frames = [solid(100)] * 6
    deltas = frame_deltas([thumbnail(f, W, H, 16, 16) for f in frames])
    assert duplicate_rate(deltas) == 1.0


def test_duplicate_rate_zero_on_moving_clip():
    frames = [ramp(i / 10.0) for i in range(8)]
    deltas = frame_deltas([thumbnail(f, W, H, 16, 16) for f in frames])
    assert duplicate_rate(deltas) == 0.0


def test_flat_drift_has_no_texture():
    assert motion_texture([3.5] * 20) == 0.0


def test_articulated_motion_has_texture():
    perf = [5, 2, 8, 9, 3, 4, 0.2, 6, 7, 3, 2, 9, 4, 3, 1.2, 5, 8, 4, 3]
    assert motion_texture(perf) > 0.4
    assert accel_peaks(perf) >= 2


def test_accents_ignore_duplicate_artefacts():
    # sawtooth of real motion then a zero is what generated clips look like;
    # the recovery out of a zero must not be counted as a gesture accent.
    saw = [3.3, 0.6, 3.4, 0.5, 3.3, 0.6]
    assert accel_peaks(saw) == 0


def test_gate_rejects_frozen_clip():
    r = evaluate([(W, H, solid(100))] * 6, KRISHNA)
    assert not r.accepted
    assert any("duplicate" in x for x in r.reasons)


def test_gate_rejects_uniform_drift():
    frames = [(W, H, ramp(i / 10.0)) for i in range(8)]
    r = evaluate(frames, KRISHNA)
    # moves constantly in one direction, so texture is low or duplicates high
    assert not r.accepted


def test_gate_result_carries_contract_identity():
    r = evaluate([(W, H, ramp(i / 10.0)) for i in range(8)], KRISHNA)
    assert r.character == "krishna"
    assert r.presence_version == "1.0.0"


def test_review_batch_aggregates():
    rs = [evaluate([(W, H, ramp(i / 10.0)) for i in range(8)], KRISHNA) for _ in range(3)]
    agg = review_batch(rs)
    assert agg["count"] == 3
    assert 0.0 <= agg["accept_rate"] <= 1.0


def test_compile_shot_includes_presence_and_timeline():
    shot = {"id": "04", "sec": 5.0, "frame": "medium close-up", "move": "slow drift"}
    out = compile_shot(KRISHNA, shot, "film still", "forest at night")
    assert "Timeline:" in out
    assert "eyes fixed past the other person" in out
    assert "forest at night" in out


def test_compile_shot_is_deterministic():
    shot = {"id": "04", "sec": 5.0, "frame": "medium close-up", "move": "slow drift"}
    assert compile_shot(KRISHNA, shot, "s", "l") == compile_shot(KRISHNA, shot, "s", "l")


def test_compile_shot_uses_explicit_beats_when_given():
    shot = {"id": "05", "sec": 4.0, "frame": "two-shot", "move": "static",
            "beats": [{"from": 0.0, "to": 2.0, "action": "he holds"},
                      {"from": 2.0, "to": 4.0, "action": "he breathes once"}]}
    out = compile_shot(KRISHNA, shot)
    assert "[0.0s-2.0s] he holds" in out


def test_scrub_removes_performance_language():
    assert "smiles warmly" not in scrub("Krishna smiles warmly, looking at the other")
    assert "turns to camera" not in scrub("he turns to camera and speaks")


def test_compile_keyframe_forbids_camera_awareness():
    shot = {"frame": "close-up on his eyes"}
    out = compile_keyframe(KRISHNA, shot, "film still", "hall")
    assert "not aware of the camera" in out


def test_diff_reports_contract_change():
    import copy
    changed = copy.deepcopy(KRISHNA)
    changed["presence"]["gaze"] = "eyes lowered"
    a = compile_shot(KRISHNA, {"frame": "x", "sec": 4})
    b = compile_shot(changed, {"frame": "x", "sec": 4})
    assert diff(a, b)


def test_box_clamps_to_frame():
    assert Box(-1, -1, 2, 2).clamp() == Box(0.0, 0.0, 1.0, 1.0)


if __name__ == "__main__":
    fns = [(n, v) for n, v in sorted(globals().items()) if n.startswith("test_")]
    failed = 0
    for name, fn in fns:
        try:
            fn()
            print(f"  ok   {name}")
        except Exception as e:
            failed += 1
            print(f"  FAIL {name}: {type(e).__name__}: {e}")
    print(f"\n{len(fns)-failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
