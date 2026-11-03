"""Presence layer: turn screen presence into a locked, testable asset."""
from .gate import evaluate, review_batch, GateResult, DEFAULTS
from .measure import Box, thumbnail, signature_diff, frame_deltas, duplicate_rate, motion_energy
from .compile import compile_shot, compile_keyframe, scrub, diff
__version__ = "1.0.0"
