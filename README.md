# sichtcheck

Your coding agent says the render *"looks good now"*. You open it — it doesn't. Again.

**sichtcheck** ("visual check") is a Claude Code skill plus a hook that stops that. Before the agent may say
*1:1*, *identical*, *looks good*, *matches the reference* or *done* about anything visual, it has to:

1. put the result **next to the reference** on one sheet,
2. open **close-ups** of the spots where things usually go wrong (skin, joints, light, clutter, small text),
3. judge **every category** on its own and write down each difference,
4. hand the images to a **fresh reviewer agent** that has not seen the work and is told to fail it.

Only when both the agent and the reviewer pass every category does `sichtcheck status` print
`SICHTCHECK PASSED`. Otherwise the agent has to say *"sichtcheck failed"* and list what still differs.

## Why

Agents grade visuals badly in a few predictable ways: they check only what they just changed, they treat a number
(brightness, green tests) as a verdict, they look at a whole downscaled image instead of the detail, and they grade
their own work. sichtcheck turns each of these around, and the hook makes it stick:

| The agent… | sichtcheck guard |
| --- | --- |
| claims "looks good" without a passed check | blocks the reply and asks for the check or an honest "visuals unchecked" |
| ran the check, it failed, and the reply glosses over it | blocks until the reply says "sichtcheck failed" and lists the gaps |
| changed visible files and said nothing | blocks until it checks or writes "visuals unchecked" |
| fills in the reviewer's answer itself, or skips opening a close-up | the pass does not count |
| gets told "this looks cheap" and keeps turning the same knobs | locks visible edits until a critique protocol is written: why it was missed, research, a different approach |

The hook reads the claim itself (English and German), costs nothing and calls no model.

## What a check looks like

```
$ sichtcheck new --ref mockup.png --out render.png --req "dark, cluttered room, warm TV glow"
Check SC-f19a5d8d created: ~/.local/share/sichtcheck/2026-11-24_184237-render

MEASURED HINTS (block 'pass' until fixed or explained with a reason):
  [light] whole_1: result brighter: mean brightness 50 % vs 11 %
  [light] whole_1: less true black: 0 % of the area vs 55 %
  [color] whole_1: saturation lower: 4 % vs 64 %
  [scene] whole_1: result smoother/emptier: detail 0.000 vs 0.054 at the same resolution (192 px)

$ sichtcheck status ~/.local/share/sichtcheck/2026-11-24_184237-render
Category                own finding       fresh reviewer    result
Geometry                open              open              FAILED
Skin/materials          fail              open              FAILED
...
SICHTCHECK FAILED (SC-f19a5d8d): 6 of 6 categories open.
  ! Fresh reviewer missing: no 'passed' without one.
  Skin/materials:
    - wall smooth and empty, reference full of props
```

Profiles: `3d` (geometry, material, light, color, scene, proportions) and `ui` (layout, typography, color,
components, content, impression).

## Install

Give Claude Code this repo's link and say:

```
Install this and set it up in my Claude Code: https://github.com/sernoxxx/sichtcheck
```

Claude follows [`INSTALL.md`](INSTALL.md): installs Pillow and NumPy, clones the skill to
`~/.claude/skills/sichtcheck`, puts the `sichtcheck` command on your PATH, adds the hook to
`~/.claude/settings.json`, adds one rule to `~/.claude/CLAUDE.md` and runs the tests.

Requirements: Python 3.9+, Pillow, NumPy. Works with any renderer or engine that can write a PNG.

## Manual use

```bash
sichtcheck new --ref REF.png --out OUT.png --req "the request, verbatim"
sichtcheck crop DIR material:skin=0.3,0.2,0.3,0.4:0.3,0.2,0.3,0.4 light:window=0,0,0.4,0.5:0,0,0.4,0.5
sichtcheck verdict DIR material fail -d "skin smooth like a balloon, reference shows pores"
sichtcheck review DIR            # brief for a fresh reviewer agent
sichtcheck review DIR --answer DIR/reviewer_answer.json --by "reviewer agent"
sichtcheck status DIR            # exit 0 only when passed
```

Full workflow: [`SKILL.md`](SKILL.md).

## Limits

- Still images only. Motion, jitter and flicker need a video tool.
- The measured hints are coarse image statistics; they can block a pass, never grant one.
- The reviewer is the same model with fresh context — independent of the work, not of the model's blind spots.
  Your own eyes still rank highest.

## License

MIT
