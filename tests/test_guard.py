#!/usr/bin/env python3
"""Tests for hooks/sichtcheck_guard.py with synthetic transcripts.
Run: python3 tests/test_guard.py"""
import json, os, subprocess, sys, tempfile, time
from datetime import datetime, timezone
from pathlib import Path
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "hooks" / "sichtcheck_guard.py"
TOOL = ROOT / "sichtcheck.py"
tmp = Path(tempfile.mkdtemp(prefix="sichtcheck-guard-test-"))
env = dict(os.environ, SICHTCHECK_HOME=str(tmp / "home"), SICHTCHECK_GUARD_STATE=str(tmp / "state"))
sys.path.insert(0, str(HOOK.parent))
os.environ.update(SICHTCHECK_HOME=env["SICHTCHECK_HOME"], SICHTCHECK_GUARD_STATE=env["SICHTCHECK_GUARD_STATE"])
import sichtcheck_guard as g

# --- wording
def has(t): return bool(g.claims(t))
assert has("The character looks good now.")
assert has("The room is now 1:1 with your picture.")
assert has("The button matches the mockup.")
assert has("The scene is done.")
assert has("Done. Everything is built.")
assert has("The skin looks realistic now.")
assert has("Die Figur sieht jetzt gut aus.")                    # German claims are recognised too
assert has("Der Raum ist jetzt 1:1 wie dein Bild.")
assert not has("It is not 1:1 yet.")
assert not has("1:1 it is not.")
assert not has("You wrote that I claimed \"looks good\" and \"1:1\".")
assert not has("Export in 16:9, 9:16 and 1:1.")
assert not has("The bake is done.")
assert not has("The skin is brighter than in the reference.")
assert not has("It looks nothing like the reference yet.")
assert not has("SICHTCHECK FAILED: skin looks like plastic, room too bright.")
assert not has("The skin doesn't look realistic yet.")
assert not has("Es ist noch nicht 1:1.")
assert has("The skin looks good now and has no more gloss.")
K = lambda t: bool(g.KRITIK.search(t))
assert K("the skin looks like cheap plastic") and K("bro the room is way too bright and too clean")
assert K("Did you even look at it? The character is completely broken")
assert K("die haut sieht aus wie billiges plastik")
assert not K("make the skin brighter and the hands bigger") and not K("the voice sounds cheap")

# --- transcripts
def iso(t): return datetime.fromtimestamp(t, timezone.utc).isoformat().replace("+00:00", "Z")
def transcript(tools, t0=None):
    t0 = t0 or time.time()
    p = tmp / f"t{time.time_ns()}.jsonl"
    lines = [{"type": "user", "message": {"content": "make the skin like in the picture"}, "timestamp": iso(t0)}]
    for k, (name, inp) in enumerate(tools):
        blk = {"type": "text", "text": inp} if name == "TEXT" else {"type": "tool_use", "name": name, "input": inp}
        lines.append({"type": "assistant", "timestamp": iso(t0 + 1 + k), "message": {"content": [blk]}})
    p.write_text("\n".join(json.dumps(l) for l in lines) + "\n")
    return p
def hook(mode, ev):
    r = subprocess.run([sys.executable, str(HOOK), mode], input=json.dumps(ev), capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout) if r.stdout.strip() else {}
def stop(tools, final, sid="s", active=False, t0=None):
    return hook("stop", {"session_id": sid, "transcript_path": str(transcript(tools, t0)), "last_assistant_message": final,
                         "stop_hook_active": active})
def tool(*a, stdin=None):
    r = subprocess.run([sys.executable, str(TOOL), *map(str, a)], capture_output=True, text=True, env=env, input=stdin)
    return r.returncode, r.stdout + r.stderr

E1 = ("Edit", {"file_path": "/p/characters/skin.gdshader"}); E2 = ("Edit", {"file_path": "/p/scenes/bedroom.tscn"})
r = stop([E1, E2], "Skin is brighter now, room darker.")
assert r.get("decision") == "block" and "no visual check" in r["reason"], r
assert stop([E1, E2], "Skin made brighter. Visuals unchecked.") == {}
assert stop([("Edit", {"file_path": "/p/src/api.py"})], "API fixed, tests green.") == {}
r = stop([("Read", {"file_path": "/p/r1.png"})], "The character looks good now.")
assert r.get("decision") == "block" and "without evidence" in r["reason"] and "No `sichtcheck status`" in r["reason"], r
r = stop([("Edit", {"file_path": "/p/web/app.css"})], "The layout is now 1:1 with the mockup.")
assert r.get("decision") == "block", r
assert stop([("Edit", {"file_path": "/p/web/app.css"})], "Spacing adjusted.") == {}

# a real check directory: identical pictures -> can pass
Image.radial_gradient("L").convert("RGB").resize((1200, 800)).save(tmp / "a.png")
t0 = time.time()
code, out = tool("new", "--ref", tmp / "a.png", "--out", tmp / "a.png", "--req", "like the picture", "--name", "t")
d = Path(next(l for l in out.splitlines() if "created:" in l).split("created:")[1].strip())
tool("crop", d, "material:m=0.2,0.2,0.5,0.5:0.2,0.2,0.5,0.5", "light:l=0,0,0.5,1:0,0,0.5,1",
     "geometry:g=0.2,0.2,0.6,0.7:0.2,0.2,0.6,0.7", "scene:u=0.5,0,0.5,1:0.5,0,0.5,1")
ST = ("Bash", {"command": f"sichtcheck status {d}"})
code, out = tool("status", d)
assert code == 1
r = stop([E1, E2, ST], "Skin adjusted, room darker.", t0=t0)
assert r.get("decision") == "block" and "did not pass" in r["reason"], r
assert stop([E1, E2, ST], "Sichtcheck failed: reviewer missing, skin open.", t0=t0) == {}
SAID = ("TEXT", "Sichtcheck failed: skin and room open.")
assert stop([E1, E2, ST, SAID], "DONE", t0=t0) == {}                    # already reported; a later one-word stop is fine
r = stop([E1, E2, SAID, ST], "DONE", t0=t0)                            # said before the check ran: does not count
assert r.get("decision") == "block", r
assert stop([E1, E2, ("TEXT", "Skin made brighter. Visuals unchecked.")], "DONE") == {}
r = stop([E1, E2, ST], "The character looks good now. Sichtcheck failed.", t0=t0)
assert r.get("decision") == "block" and "without evidence" in r["reason"], r   # a claim next to a failed check is still a claim
for k, b in (("material", "crop_material_m.png"), ("light", "crop_light_l.png"), ("geometry", "crop_geometry_g.png"),
             ("scene", "crop_scene_u.png"), ("color", "sheet_1.png"), ("proportions", "sheet_1.png")):
    assert tool("verdict", d, k, "pass", "--evidence", b)[0] == 0
tool("review", d)
CATS = ("geometry", "material", "light", "color", "scene", "proportions")
ans = json.dumps({"categories": {k: {"verdict": "pass", "differences": []} for k in CATS}})
assert tool("review", d, "--answer", "-", "--by", "test", stdin=ans)[0] == 0
assert tool("status", d)[0] == 0
cid = json.loads((d / "check.json").read_text())["id"]
images = json.loads((d / "status.json").read_text())["images"]
READS = [("Read", {"file_path": str(d / b)}) for b in images]
AG = ("Agent", {"subagent_type": "general-purpose", "prompt": f"You are an independent visual reviewer. Check ID: {cid}"})
FORK = ("Agent", {"subagent_type": "fork", "prompt": f"Check ID: {cid}"})
CLAIM = "SICHTCHECK PASSED: the character looks good now."
r = stop([E1, E2, ST], CLAIM, t0=t0)
assert r.get("decision") == "block" and "not looked at" in r["reason"], r
r = stop([E1, E2] + READS + [ST], CLAIM, t0=t0)
assert r.get("decision") == "block" and "no fresh agent" in r["reason"], r
r = stop([E1, E2] + READS + [FORK, ST], CLAIM, t0=t0)
assert r.get("decision") == "block" and "no fresh agent" in r["reason"], r
assert stop([E1, E2] + READS + [AG, ST], CLAIM, t0=t0) == {}
SELF = ("Write", {"file_path": str(d / "reviewer_answer.json")})
r = stop([E1, E2] + READS + [AG, SELF, ST], CLAIM, t0=t0)
assert r.get("decision") == "block" and "written by the builder" in r["reason"], r
r = stop(READS + [AG, ST, E1, E2], CLAIM, t0=t0)                         # edits after the check invalidate it
assert r.get("decision") == "block", r

# block limit, then a warning for the user instead of an endless loop
for n in range(g.MAX_BLOCKS):
    r = stop([("Read", {"file_path": "/p/r1.png"})], "Looks good.", sid="loop", active=n > 0)
    assert r.get("decision") == "block", (n, r)
r = stop([("Read", {"file_path": "/p/r1.png"})], "Looks good.", sid="loop", active=True)
assert "systemMessage" in r and "decision" not in r, r

# criticism: marker, refusal of visible edits, release after the protocol
assert hook("prompt", {"session_id": "k", "prompt": "make the skin brighter"}) == {}
assert hook("prompt", {"session_id": "k", "prompt": '<agent-message from="x">[Subagent hand-back] The skin looks like cheap plastic</agent-message>'}) == {}
assert hook("pre", {"session_id": "k", "tool_name": "Edit", "tool_input": {"file_path": "/p/characters/skin.gdshader"}}) == {}
r = hook("prompt", {"session_id": "k", "prompt": "bro the skin looks like a cheap balloon and the room is too bright"})
assert "STOP" in r["hookSpecificOutput"]["additionalContext"], r
r = hook("pre", {"session_id": "k", "tool_name": "Edit", "tool_input": {"file_path": "/p/characters/skin.gdshader"}})
assert r["hookSpecificOutput"]["permissionDecision"] == "deny", r
assert hook("pre", {"session_id": "k", "tool_name": "Edit", "tool_input": {"file_path": "/p/notes.md"}}) == {}
assert hook("pre", {"session_id": "other", "tool_name": "Edit", "tool_input": {"file_path": "/p/characters/skin.gdshader"}}) == {}
r = stop([("Bash", {"command": "godot --headless --script lab/shot.gd"})], "Rendered again. Visuals unchecked.", sid="k")
assert r.get("decision") == "block" and "criticism" in r["reason"], r
assert tool("critique", "--text", "balloon", "--why", "only measured brightness", "--research", "Godot SSS: detail normal map",
            "--old", "gloss slider", "--new", "subdivide mesh + pore normal map")[0] == 0
assert hook("pre", {"session_id": "k", "tool_name": "Edit", "tool_input": {"file_path": "/p/characters/skin.gdshader"}}) == {}
print("OK: all guard tests passed")
