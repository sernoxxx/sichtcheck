#!/usr/bin/env python3
"""Tests for sichtcheck with synthetic pictures: dark cluttered reference vs. bright smooth result.
Run: python3 tests/test_sichtcheck.py"""
import json, os, subprocess, sys, tempfile
from pathlib import Path
import numpy as np
from PIL import Image

TOOL = Path(__file__).resolve().parents[1] / "sichtcheck.py"
tmp = Path(tempfile.mkdtemp(prefix="sichtcheck-test-"))
env = dict(os.environ, SICHTCHECK_HOME=str(tmp / "home"))


def run(*args, stdin=None):
    r = subprocess.run([sys.executable, str(TOOL), *map(str, args)], capture_output=True, text=True, env=env, input=stdin)
    return r.returncode, r.stdout + r.stderr


def pictures():
    rng = np.random.default_rng(1)
    ref = np.zeros((360, 640, 3), np.float32) + [0.03, 0.03, 0.08]          # dark blue room
    for _ in range(140):                                                    # clutter
        x, y, w, h = rng.integers(0, 600), rng.integers(0, 330), rng.integers(6, 40), rng.integers(6, 30)
        ref[y:y + h, x:x + w] = rng.uniform(0.05, 0.5, 3) * [0.6, 0.7, 1.0]
    ref[120:300, 220:420] = [0.75, 0.6, 0.62] + rng.normal(0, 0.06, (180, 200, 3))  # textured skin patch
    out = np.zeros((720, 1280, 3), np.float32) + [0.55, 0.55, 0.55]         # bright clean room
    out[240:600, 440:840] = [0.6, 0.5, 0.5]                                 # smooth balloon patch
    for a, n in ((ref, "ref.png"), (out, "out.png")):
        Image.fromarray((np.clip(a, 0, 1) * 255).astype(np.uint8)).save(tmp / n)


def check_dir(out):
    return Path(next(l for l in out.splitlines() if "created:" in l).split("created:")[1].strip())


pictures()
ALL = ("geometry", "material", "light", "color", "scene", "proportions")
PASS_ANSWER = json.dumps({"categories": {k: {"verdict": "pass", "differences": []} for k in ALL}, "worst_flaws": []})

# 1 bad result: hints fire, nothing can pass
code, out = run("new", "--ref", tmp / "ref.png", "--out", tmp / "out.png", "--req", "dark, messy room", "--name", "bad")
assert code == 0, out
d = check_dir(out)
assert "brighter" in out and "smoother/emptier" in out, out
assert Image.open(d / "sheet_1.png").size[0] <= 2000 and (d / "light.png").is_file()
code, out = run("verdict", d, "material", "pass", "--evidence", "sheet_1.png")
assert code != 0 and "close-up" in out, out
code, out = run("verdict", d, "light", "fail")
assert code != 0 and "difference" in out, out
code, out = run("review", d)
assert code != 0 and "close-ups" in out, out
code, out = run("crop", d, "material:skin=0.34,0.33,0.31,0.5:0.34,0.33,0.31,0.5", "light:room=0,0,0.3,1:0,0,0.3,1",
                "geometry:edge=0.3,0.3,0.2,0.3:0.3,0.3,0.2,0.3", "scene:corner=0.6,0,0.4,0.6:0.6,0,0.4,0.6")
assert code == 0 and (d / "crop_material_skin.png").is_file(), out
assert "[material] crop_material_skin" in out, out              # smooth patch flagged in the skin close-up
code, out = run("verdict", d, "material", "pass", "--evidence", "crop_material_skin.png")
assert code != 0 and "Measured hints open" in out, out
code, out = run("verdict", d, "material", "fail", "-d", "skin smooth like a balloon, no texture")
assert code == 0, out
code, out = run("status", d)
assert code == 1 and "SICHTCHECK FAILED" in out and "reviewer missing" in out and "own finding missing" in out, out
code, out = run("review", d, "--answer", "-", "--by", "test", stdin=PASS_ANSWER)
assert code != 0 and "Write the brief first" in out, out       # no verdict without a generated brief
code, out = run("review", d)
assert code == 0 and "Check ID: SC-" in out and "dark, messy room" in out, out
code, out = run("review", d, "--answer", "-", "--by", "test", stdin=PASS_ANSWER)
assert code == 0, out
code, out = run("status", d)                                    # a lenient reviewer alone cannot rescue it
assert code == 1 and "SICHTCHECK FAILED" in out and "balloon" in out, out
assert json.loads((d / "status.json").read_text())["ok"] is False

# 2 identical pictures: the full path can pass, and breaks again when the result changes
code, out = run("new", "--ref", tmp / "ref.png", "--out", tmp / "ref.png", "--req", "like the reference", "--name", "same")
d2 = check_dir(out)
assert "MEASURED HINTS" not in out, out
code, out = run("crop", d2, "material:skin=0.34,0.33,0.31,0.5:0.34,0.33,0.31,0.5")
assert "blurry zoom" in out, out                                # 198x180 px source is no close-up
code, out = run("verdict", d2, "material", "pass", "--evidence", "crop_material_skin.png")
assert code != 0 and "blurry zoom" in out, out
run("crop", d2, "material:skin=0.3,0.2,0.5,0.7:0.3,0.2,0.5,0.7", "light:room=0,0,0.5,1:0,0,0.5,1",
    "geometry:edge=0.2,0.2,0.6,0.7:0.2,0.2,0.6,0.7", "scene:corner=0.5,0,0.5,1:0.5,0,0.5,1")
for k, ev in (("material", "crop_material_skin.png"), ("light", "crop_light_room.png"), ("geometry", "crop_geometry_edge.png"),
              ("scene", "crop_scene_corner.png"), ("color", "sheet_1.png"), ("proportions", "sheet_1.png")):
    code, out = run("verdict", d2, k, "pass", "--evidence", ev)
    assert code == 0, out
code, out = run("status", d2)
assert code == 1 and "reviewer missing" in out, out
run("review", d2)
bad = json.loads(PASS_ANSWER)
bad["categories"]["light"] = {"verdict": "fail", "differences": ["TV glow missing on the left"]}
run("review", d2, "--answer", "-", "--by", "test", stdin=json.dumps(bad))
code, out = run("status", d2)
assert code == 1 and "TV glow missing on the left (reviewer)" in out, out   # reviewer veto wins over own pass
run("review", d2, "--answer", "-", "--by", "test", stdin="blabla " + PASS_ANSWER + " ok")
code, out = run("status", d2)
assert code == 0 and "SICHTCHECK PASSED" in out and "Reference is only" not in out, out
Image.new("RGB", (640, 360), (200, 200, 200)).save(tmp / "ref2.png")
c = json.loads((d2 / "check.json").read_text())
c["outs"][0]["path"] = str(tmp / "ref2.png")
(d2 / "check.json").write_text(json.dumps(c))
code, out = run("status", d2)
assert code == 1 and "changed" in out, out

# 3 small reference and missing reference are reported as limits
Image.open(tmp / "ref.png").resize((288, 162)).save(tmp / "small.png")
code, out = run("new", "--ref", tmp / "small.png", "--out", tmp / "out.png", "--name", "small")
assert "Reference is only 288x162" in out and "No verbatim request" in out, out
code, out = run("new", "--out", tmp / "out.png")
assert code != 0 and "--ref missing" in out, out
code, out = run("new", "--out", tmp / "out.png", "--no-ref", "free task", "--req", "should look good")
assert code == 0 and "cannot be proven" in out, out

# 4 critique protocol
code, out = run("critique", "--text", "looks awful", "--why", "only measured brightness")
assert code != 0 and "incomplete" in out, out
code, out = run("critique", "--text", "x", "--why", "y", "--research", "z", "--old", "Turn knobs", "--new", "turn knobs")
assert code != 0 and "the same" in out, out
code, out = run("critique", "--text", "x", "--why", "y", "--research", "z", "--old", "Turn knobs", "--new", "Rebuild the mesh")
assert code == 0 and "Critique protocol:" in out, out
print("OK: all sichtcheck tests passed")
