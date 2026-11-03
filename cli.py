#!/usr/bin/env python3
"""namo-presence CLI.

  presence plan    season.json --chars chars.json [--out DIR]
  presence run     season.json --chars chars.json [--backend mock|comfy] [--server URL]
  presence gate    clip.json --char krishna          # gate one clip's frames
  presence compile season.json --chars chars.json     # print prompts only, no renders
  presence selftest                                    # prove the install works

Everything is offline except `run --backend comfy`, which needs a ComfyUI server.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from presence import evaluate, review_batch, compile_shot, Box  # noqa: E402
from pipeline.runner import SeasonRunner, MockBackend, ComfyBackend  # noqa: E402


def load_chars(path):
    """Accept a single contract, a list of contracts, a {id: contract} map, or a
    wrapper {"characters": ...}. A single contract is the common case when a studio
    starts with one character, so it must not be silently read as a map of characters.
    """
    raw = json.load(open(path))
    if isinstance(raw, dict) and "characters" in raw:
        raw = raw["characters"]
    if isinstance(raw, list):
        return {c["id"]: c for c in raw}
    if isinstance(raw, dict) and "id" in raw and "presence" in raw:
        return {raw["id"]: raw}          # a single contract file
    return raw


def cmd_plan(a):
    season = json.load(open(a.season))
    chars = load_chars(a.chars)
    runner = SeasonRunner(chars, MockBackend(a.out), style_lock=a.style or "",
                          out_root=a.out)
    jobs = runner.plan(season)
    print(f"{len(jobs)} shots planned")
    for j in jobs[:a.limit]:
        print(f"  {j.episode}/{j.shot_id}  {j.character}@{j.presence_version}  seed={j.seed}")
    return 0


def cmd_compile(a):
    season = json.load(open(a.season))
    chars = load_chars(a.chars)
    runner = SeasonRunner(chars, MockBackend(a.out), style_lock=a.style or "",
                          out_root=a.out)
    jobs = runner.plan(season)
    for j in jobs[:a.limit]:
        print("=" * 70)
        print(f"{j.episode}/{j.shot_id}  {j.character}@{j.presence_version}")
        print("=" * 70)
        print(j.prompt)
        print()
    return 0


def cmd_run(a):
    season = json.load(open(a.season))
    chars = load_chars(a.chars)
    if a.backend == "comfy":
        graphs = json.load(open(a.graphs))
        backend = ComfyBackend(a.server, graphs, arm=a.arm)
    else:
        backend = MockBackend(a.out)
    probe = backend.probe()
    print(f"backend: {json.dumps(probe)}")
    if not probe.get("ok"):
        print("backend not available; aborting before spending compute", file=sys.stderr)
        return 2

    loader = None
    if a.frames:
        def loader(path):
            d = json.load(open(path))
            return [(f["w"], f["h"], bytes.fromhex(f["rgb"])) for f in d["frames"]]

    runner = SeasonRunner(chars, backend, style_lock=a.style or "", out_root=a.out)
    run = runner.run(season, frame_loader=loader, max_shots=a.max_shots)
    print(json.dumps(run.summary(), indent=1))
    return 0


def cmd_gate(a):
    d = json.load(open(a.clip))
    frames = [(f["w"], f["h"], bytes.fromhex(f["rgb"])) for f in d["frames"]]
    chars = load_chars(a.chars)
    contract = chars[a.char]
    r = evaluate(frames, contract, box=Box(*[float(x) for x in a.box.split(",")]) if a.box else None)
    print(r.summary())
    print(json.dumps(r.metrics, indent=1))
    return 0 if r.accepted else 1


def cmd_selftest(a):
    W, H = 64, 114

    def perf(i):
        shift = [0, 1, 2, 3, 4, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19][i]
        buf = bytearray(W * H * 3)
        for y in range(H):
            for x in range(W):
                v = 95 + ((x + shift) % 55)
                k = (y * W + x) * 3
                buf[k] = buf[k + 1] = buf[k + 2] = v
        return bytes(buf)

    contract = {"id": "selftest", "presence_version": "0.0.1",
                "presence": {"gaze": "x"}, "negatives": [],
                "gates": {"stillness_max": 3.2, "duplicate_max": 0.04, "texture_min": 0.18}}
    good = evaluate([(W, H, perf(i)) for i in range(16)], contract)
    frozen = evaluate([(W, H, bytes([100, 100, 100]) * (W * H))] * 8, contract)
    checks = [
        ("accepts an articulated take", good.accepted),
        ("rejects a frozen take", not frozen.accepted),
        ("gate names the contract", good.character == "selftest"),
        ("prompt carries presence",
         "eyes fixed" in compile_shot(
             {"id": "k", "presence_version": "1", "presence": {"gaze": "eyes fixed past"}},
             {"frame": "close", "sec": 4})),
    ]
    failed = 0
    for name, ok in checks:
        print(("  ok   " if ok else "  FAIL ") + name)
        failed += 0 if ok else 1
    print(f"\n{len(checks)-failed}/{len(checks)} passed")
    return 1 if failed else 0


def main():
    p = argparse.ArgumentParser(prog="presence")
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp):
        sp.add_argument("season", nargs="?", help="season manifest json")
        sp.add_argument("--chars", default="schema", help="character contracts json, or a directory")
        sp.add_argument("--out", default="out")
        sp.add_argument("--style", default="")
        sp.add_argument("--limit", type=int, default=20)

    sp = sub.add_parser("plan"); common(sp); sp.set_defaults(fn=cmd_plan)
    sp = sub.add_parser("compile"); common(sp); sp.set_defaults(fn=cmd_compile)

    sp = sub.add_parser("run"); common(sp)
    sp.add_argument("--backend", default="mock", choices=["mock", "comfy"])
    sp.add_argument("--server", default="http://127.0.0.1:8188")
    sp.add_argument("--graphs", default="")
    sp.add_argument("--arm", default="C_base_v4_turbo_6")
    sp.add_argument("--frames", action="store_true", help="gate using frame dumps")
    sp.add_argument("--max-shots", type=int, default=None)
    sp.set_defaults(fn=cmd_run)

    sp = sub.add_parser("gate")
    sp.add_argument("clip"); sp.add_argument("--char", required=True)
    sp.add_argument("--chars", default="schema")
    sp.add_argument("--box", default="")
    sp.set_defaults(fn=cmd_gate)

    sp = sub.add_parser("selftest"); sp.set_defaults(fn=cmd_selftest)

    a = p.parse_args()
    if a.cmd in ("plan", "compile", "run") and not a.season:
        p.error("a season manifest is required")
    if a.cmd in ("plan", "compile", "run") and not a.chars:
        p.add_argument  # noqa
    if a.cmd in ("plan", "compile", "run") and a.chars and os.path.isdir(a.chars):
        a.chars = os.path.join(a.chars, "krishna.json")
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
