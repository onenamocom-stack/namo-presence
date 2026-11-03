"""Season runner.

Takes a season manifest (episodes -> shots), compiles presence-aware prompts, and drives
renders through a pluggable backend. Two backends ship:

  ComfyBackend  - talks to a ComfyUI server using the validated H3 graphs (real GPU work)
  MockBackend   - writes deterministic files, for pipeline tests without spending GPU time

The runner is deliberately boring: it never retries unattended (cost control), it records
the contract version used for every shot, and it refuses to write a shot that the gate
rejected. Batch accounting is the product: accept-rate per contract version.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any

from presence.compile import compile_shot
from presence.gate import evaluate, review_batch, GateResult
from presence.measure import Box


@dataclass
class ShotJob:
    episode: str
    shot_id: str
    character: str
    presence_version: str
    prompt: str
    seconds: float
    seed: int
    out_path: str


@dataclass
class ShotResult:
    job: ShotJob
    ok: bool
    clip_path: str = ""
    gate: dict[str, Any] = field(default_factory=dict)
    reason: str = ""
    seconds: float = 0.0


class Backend:
    name = "base"

    def render(self, job: ShotJob) -> str:
        """Render one shot, return the clip path."""
        raise NotImplementedError

    def probe(self) -> dict:
        raise NotImplementedError


class MockBackend(Backend):
    """Writes a small deterministic file. Used to exercise the pipeline for free."""
    name = "mock"

    def __init__(self, root: str):
        self.root = root

    def probe(self) -> dict:
        return {"backend": self.name, "ok": True}

    def render(self, job: ShotJob) -> str:
        os.makedirs(os.path.dirname(job.out_path), exist_ok=True)
        payload = {
            "prompt": job.prompt, "seed": job.seed,
            "seconds": job.seconds, "character": job.character,
            "presence_version": job.presence_version, "job": job.shot_id,
        }
        with open(job.out_path, "w") as f:
            f.write(json.dumps(payload, indent=1))
        return job.out_path


class ComfyBackend(Backend):
    """Drives a ComfyUI server with the validated H3 graph arms.

    The graphs already carry the model wiring; this class only substitutes the prompt,
    seed, size and length, so a new experiment never means a new graph.
    """
    name = "comfy"

    def __init__(self, server: str, graphs: dict, arm: str = "C_base_v4_turbo_6",
                 width: int = 480, height: int = 864, timeout: int = 900):
        self.server = server.rstrip("/")
        self.graphs = graphs
        self.arm = arm
        self.width = width
        self.height = height
        self.timeout = timeout

    def probe(self) -> dict:
        import urllib.request
        try:
            with urllib.request.urlopen(self.server + "/system_stats", timeout=10) as r:
                return {"backend": self.name, "ok": True, "stats": json.loads(r.read())}
        except Exception as e:
            return {"backend": self.name, "ok": False, "error": str(e)}

    def _workflow(self, job: ShotJob) -> dict:
        g = json.loads(json.dumps(self.graphs[self.arm]))
        graph = g.get("graph", g)
        for node in graph.values():
            ct = node.get("class_type", "")
            ins = node.get("inputs", {})
            if ct in ("MiniMaxH3ImageToVideo", "MiniMaxH3TextToVideo", "MiniMaxTextToVideo") \
                    or "ImageToVideo" in ct or "TextToVideo" in ct:
                if "prompt" in ins:
                    ins["prompt"] = job.prompt
                if "width" in ins:
                    ins["width"] = self.width
                if "height" in ins:
                    ins["height"] = self.height
            if ct == "RandomNoise" and "noise_seed" in ins:
                ins["noise_seed"] = job.seed
            if ct == "SaveVideo" and "filename_prefix" in ins:
                ins["filename_prefix"] = f"{job.episode}/{job.shot_id}_{job.character}_{job.presence_version.replace('.','')}"
        return graph

    def render(self, job: ShotJob) -> str:
        import urllib.request
        wf = self._workflow(job)
        client_id = str(uuid.uuid4())
        body = json.dumps({"prompt": wf, "client_id": client_id}).encode()
        req = urllib.request.Request(self.server + "/prompt", data=body,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as r:
            pid = json.loads(r.read())["prompt_id"]
        deadline = time.time() + self.timeout
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(f"{self.server}/history/{pid}", timeout=30) as r:
                    hist = json.loads(r.read())
                if pid in hist:
                    outs = hist[pid].get("outputs", {})
                    for node in outs.values():
                        for key in ("gifs", "videos", "images"):
                            for item in node.get(key, []):
                                return os.path.join(self.server, "view",
                                                    f"?filename={item['filename']}"
                                                    f"&subfolder={item.get('subfolder','')}"
                                                    f"&type={item.get('type','output')}")
            except Exception:
                pass
            time.sleep(5)
        raise TimeoutError(f"shot {job.shot_id} exceeded {self.timeout}s")


@dataclass
class SeasonRun:
    season: str
    started: float
    results: list[ShotResult] = field(default_factory=list)
    contract_versions: dict[str, str] = field(default_factory=dict)

    def summary(self) -> dict:
        gates = [GateResult(**r.gate) for r in self.results if r.gate]
        agg = review_batch(gates)
        rendered = sum(1 for r in self.results if r.ok)
        return {
            "season": self.season,
            "shots_planned": len(self.results),
            "shots_rendered": rendered,
            "gate": agg,
            "elapsed_s": round(time.time() - self.started, 1),
            "contract_versions": self.contract_versions,
        }


class SeasonRunner:
    def __init__(self, characters: dict[str, dict], backend: Backend,
                 style_lock: str = "", out_root: str = "."):
        self.characters = characters
        self.backend = backend
        self.style_lock = style_lock
        self.out_root = out_root

    def plan(self, season: dict) -> list[ShotJob]:
        """Compile every shot in the season into a job. No GPU time is spent here."""
        jobs: list[ShotJob] = []
        for ep in season.get("episodes", []):
            ep_id = ep.get("episode", "ep00")
            loc_text = ep.get("location_text", "")
            for shot in ep.get("shots", []):
                char_ids = shot.get("chars") or ep.get("cast") or []
                # the first listed character owns the shot's presence
                primary = self.characters.get(char_ids[0]) if char_ids else None
                if primary is None:
                    continue
                prompt = compile_shot(primary, shot, self.style_lock, loc_text)
                seed = shot.get("seed", abs(hash((ep_id, shot.get("id"), primary["id"]))) % (2**31))
                jobs.append(ShotJob(
                    episode=ep_id, shot_id=str(shot.get("id")),
                    character=primary["id"],
                    presence_version=primary.get("presence_version", "0.0.0"),
                    prompt=prompt,
                    seconds=float(shot.get("sec", 4)),
                    seed=seed,
                    out_path=os.path.join(self.out_root, ep_id,
                                          f"{shot.get('id')}_{primary['id']}.mp4"),
                ))
        return jobs

    def run(self, season: dict, frame_loader=None, box: Box | None = None,
            max_shots: int | None = None) -> SeasonRun:
        """Render, then gate. A rejected shot is recorded and skipped, never retried.

        frame_loader: callable(clip_path) -> list[(w,h,rgb)] used by the gate. If None the
        gate is skipped and the run only records renders.
        """
        run = SeasonRun(season.get("season", "unknown"), time.time())
        jobs = self.plan(season)
        if max_shots is not None:
            jobs = jobs[:max_shots]
        for job in jobs:
            run.contract_versions[job.character] = job.presence_version
            t0 = time.time()
            try:
                path = self.backend.render(job)
                res = ShotResult(job, True, path, seconds=round(time.time() - t0, 1))
                if frame_loader is not None:
                    contract = self.characters[job.character]
                    frames = frame_loader(path)
                    gr = evaluate(frames, contract, box=box)
                    res.gate = gr.as_dict()
                    res.ok = gr.accepted
                    if not gr.accepted:
                        res.reason = "; ".join(gr.reasons)
            except Exception as e:
                res = ShotResult(job, False, reason=f"render error: {e}",
                                 seconds=round(time.time() - t0, 1))
            run.results.append(res)
        return run
