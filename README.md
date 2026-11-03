# namo-presence

**Make screen presence a locked, testable asset instead of an accident of each render.**

A video model asked for "Krishna" returns a handsome young actor performing at camera:
brows up, mouth open, head tilted, hands explaining. Every time, at every episode. The
engine is not broken — it is faithfully reproducing its default prior, because nothing in
the pipeline owns *performance*.

This package owns it. It adds the one layer that the production engines
(OpenMontage, Toonflow, ViMax, ArcReel, dramaclaw, MiniMax-H3) all leave out:
a **character presence contract** that compiles into every prompt and is enforced by
**mechanical gates** on the generated clip.

---

## The three pieces

**1. Contract** (`schema/character.schema.json`)
Presence lives with the character, not the shot: gaze, mouth, hands, head, timing, weight,
breath, plus the negatives that must never appear. Versioned — every render records the
version it was made under, so episode 400 opens on the same person as episode 1.

**2. Compiler** (`presence/compile.py`)
Turns a contract + shot into the exact prompt text, including the per-beat timeline that
H3 graphs already accept. Deterministic: same input, same prompt, reviewable diffs.
Negatives are rewritten as *physical facts* ("brows lowered and still") because models
follow physics, not prohibitions. Performance-leaking phrasing is stripped.

**3. Gate** (`presence/gate.py`)
Scores a rendered clip and returns accept/reject with reasons. No GPU, no model — just
frame statistics that separate a performance from a drift:

| metric | what it catches |
|---|---|
| `duplicate_rate` | the ~7–12% stutter that generated video is full of |
| `motion_energy` | too busy, drifting, or dead |
| `motion_texture` | uniform drift vs. real articulation (accents and settles) |
| `mouth_variance` | the "talking host" tell |
| `accents` | gesture beats, robust against duplicate-frame artefacts |

**Accept-rate is the number that makes 200–500 episodes possible.** You cannot scale a
pipeline where a human eyeballs every take. You can scale one where the gate rejects
mechanically and the human only reviews candidates that already hold.

---

## Install

```bash
git clone <this repo> && cd namo-presence
python3 cli.py selftest        # proves the install, no network, no GPU
```

No third-party dependencies. Python 3.9+.

## Use

```bash
# see what would be rendered, and the exact prompt for each shot
python3 cli.py compile schema/season-mahabharat.json --chars schema/krishna.json

# dry run the whole season with no GPU time
python3 cli.py run schema/season-mahabharat.json --chars schema/krishna.json --backend mock

# real render against a ComfyUI server holding the H3 graphs
python3 cli.py run season.json --chars chars.json \
    --backend comfy --server http://127.0.0.1:8188 \
    --graphs h3/fill/graphs.json --arm C_base_v4_turbo_6

# gate a single clip
python3 cli.py gate clip_frames.json --char krishna --box 0.15,0.10,0.85,0.92
```

## Writing a contract

```json
{
  "id": "krishna",
  "presence_version": "1.0.0",
  "presence": {
    "gaze":   "eyes fixed just past the other person, never into the lens",
    "mouth":  "jaw closed and at rest between phrases",
    "hands":  "hands low and still, never raised to explain",
    "head":   "head level and heavy, no tilt toward camera",
    "timing": "reacts one full beat after the other person finishes",
    "weight": "settled before the shot begins, never shifts while speaking"
  },
  "negatives": ["brow_raise", "open_mouth_explain", "head_tilt", "eyeline_to_lens"],
  "gates": { "stillness_max": 3.2, "duplicate_max": 0.04, "texture_min": 0.18 }
}
```

Write it as **observable facts**, not adjectives. "Calm" and "divine" produce a smiling
host. "Eyes fixed past them, jaw closed, weight already settled" produces presence.

## The production rules this encodes

1. **Direct the still, then animate the still.** If the keyframe has a brow raised and a
   mouth open, image-to-video faithfully animates a host. A held, closed-mouth still has
   nowhere to drift to. Presence review belongs on the still, where human judgement works.
2. **Gate before retry, never retry unattended.** A rejected take is recorded and skipped.
   Cost control lives here.
3. **Hold longer than the model wants.** Generate 4s, keep the best 2s, retime to 80%,
   and cut before the drift starts.
4. **Two grades.** Hero shots (entrances, close-ups) get the strict gate and more seeds.
   Coverage gets the loose gate and reused assets. 500 episodes at hero grade is
   economically impossible; 15 hero shots per episode is a normal production.
5. **Sound carries more presence than picture.** A held frame with a low drone reads as
   presence; the same frame with a voice explaining reads as an explainer.

## Status

- Gate: calibrated against a real 124s generated clip, and it rejected every take in it,
  including the ones a human reader also rejected. Verified both accept and reject paths.
- Compiler: deterministic, produces H3-compatible per-beat timelines.
- Runner: MockBackend verified across a 12-shot / 3-episode season.
  ComfyBackend is written against the validated H3 graphs but has **not** been exercised
  against a live GPU server from this machine — treat it as unproven until it renders one
  real shot.
- Not included: assembly/edit, audio, lip-sync, publish. This package is the presence
  layer; it is designed to plug into whatever engine you already run.

## Licence

MIT.
