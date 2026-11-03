# namo-presence — build log

Date: 2026-11-04 (device date verified)

## What was built
Repo: https://github.com/onenamocom-stack/namo-presence (public, MIT)
Local dev copy: files/presence/  ·  clean-clone verify: files/presence_verify/

The presence layer that none of the six evaluated engines (OpenMontage, Toonflow, ViMax,
ArcReel, dramaclaw, MiniMax-H3) ships. Three components:

1. `schema/character.schema.json` — presence contract: gaze, mouth, hands, head, timing,
   weight, breath + negatives + per-character gate thresholds. Versioned.
2. `presence/compile.py` — contract+shot -> H3 prompt with per-beat timeline. Deterministic,
   diffable. Rewrites negatives as physical facts; strips performance-leaking phrasing.
3. `presence/gate.py` — mechanical accept/reject on decoded frames. No GPU, no model.

Plus `pipeline/runner.py` (SeasonRunner, MockBackend, ComfyBackend) and `cli.py`.

## Verified, not assumed
- 18/18 unit tests pass from a fresh GitHub clone.
- CLI selftest 4/4 from a fresh clone.
- `plan` -> 3 shots, `run --backend mock` -> 3 rendered, 3 episodes exercised end to end.
- Gate verified on REAL footage: the 124s Krishna/Hanuman clip decoded earlier. Every take
  rejected, and the rejections match what a human reader flagged (duplicate stutter
  7-18%, uniform drift with no articulation, too-busy motion).
- Both verdict paths proven: ACCEPT fires on a calibrated articulated take; REJECT fires
  on frozen, on uniform drift, on duplicate-heavy, and on over-busy takes.

## Calibration note (important)
The first accent detector was wrong: generated clips alternate real motion with duplicate
frames, so a naive local-max test fires on the recovery from a zero, not on the gesture.
Fixed by comparing only above a floor and by adding `motion_texture` (coefficient of
variation of non-artefact deltas). Measured values: flat drift 0.00, the real footage
0.06-0.15, an articulated performance 0.32-0.62. Thresholds sit at 0.15-0.18.

## Not proven / honest gaps
- `ComfyBackend` is written against the validated H3 graphs (files/h3/fill/graphs.json,
  14 arms) but has NEVER been run against a live GPU server. Treat as unproven until it
  renders one real shot.
- No assembly/edit, no audio, no lip-sync, no publish. This is the presence layer only.
- The phone cannot run any of the six engines: no git, no node, ~1GB free RAM, and app data
  dirs are not executable. Cloud is the only path for real renders.
- RunPod has 2 CPU dry-run pods and 0 GPU pods right now; H3 weights (170GB graph set,
  94GB kept arms) live on network volume namo-weights z2yphdepkh in US-NC-1.

## Next concrete step
Land one real shot: start the ComfyBackend against a GPU pod holding the H3 arms, render
shot ep01/03 (krishna), and gate the actual output. That converts the last unproven
component into a measured one.
