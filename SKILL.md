---
name: sichtcheck
description: "Required before ANY statement about how something looks (render, screenshot, 3D figure, room, material, light, UI, image): look at the result next to the reference, open close-ups of the critical spots, judge every category, have a fresh reviewer try to fail it. Never say '1:1', 'identical', 'looks good', 'matches the reference' or 'done' without a passed sichtcheck. Also load it when the user says something looks bad (critique protocol). Triggers: reference image, like in the picture, 1:1, recreate, realistic, looks, look and feel, render, screenshot, sign-off, visual check."
---

# sichtcheck: visual check for still images

Why this exists: agents keep reporting visuals as matching when they clearly do not. The patterns behind it:
checking only what was just changed; taking a number (brightness, green tests) as the verdict; looking at whole,
downscaled images instead of close-ups; grading their own work. This tool turns each of those around.

## Hard rules

1. **Assume it does not match.** Every category starts open and counts as failed.
2. **No statement without looking.** Result and reference side by side, plus close-ups of the critical spots, every
   image opened on its own with Read. Code, settings and green tests say nothing about the visuals.
3. **Numbers are evidence, not a verdict.** A measured hint can block a pass, never justify one.
4. **Judge per category** and write down every difference (where, what, in which direction).
5. **Fresh reviewer.** An agent that does not know the work looks for flaws. Its no beats your yes.
6. **Wording.** "1:1", "identical", "looks good", "like the reference", "done" only with `SICHTCHECK PASSED`.
   Otherwise literally "sichtcheck failed" plus the list of what differs. Without a check, literally
   "visuals unchecked" and no statement about quality.
7. **Out of reach means: say it.** If the current tools cannot get there (mesh too coarse, reference too small,
   engine cannot do it), report that instead of turning more knobs.

The hook `hooks/sichtcheck_guard.py` enforces this: it blocks success claims without a passed status, checks that
every sheet was opened and that a fresh agent got the brief, and locks visible changes after the user's criticism
until the protocol is written.

## Steps

```bash
# 1 Create: current result (freshly rendered, several views allowed) + reference + the user's words
sichtcheck new --ref REF.png --out front.png --out side.png --req "the request, verbatim" --name shortname
#   --profile ui for interfaces; --no-ref "reason" if there really is no reference
# 2 Open the sheets (Read): sheet_N.png (left reference, right result), light.png (brightness zones)
# 3 Close-ups: box = x,y,w,h as fractions of the image; reference first, then result; :N = which result; "-" = no reference crop
sichtcheck crop DIR material:back=0.30,0.20,0.25,0.30:0.35,0.25,0.25,0.30 light:tv=0.4,0,0.3,0.4:0.4,0,0.3,0.4 \
                    geometry:shoulder=-:0.2,0.1,0.2,0.2:2 scene:shelf=0.7,0.1,0.3,0.6:0.7,0.1,0.3,0.6
# 4 Open every close-up (Read), then judge each category
sichtcheck verdict DIR material fail -d "back: smooth without pores, reference shows a soft gloss gradient"
sichtcheck verdict DIR color pass --evidence sheet_1.png
# 5 Fresh reviewer: write the brief, hand it unchanged to a new agent; it writes its own answer
sichtcheck review DIR                       # writes DIR/reviewer_brief.md
sichtcheck review DIR --answer DIR/reviewer_answer.json --by "subagent, model X"
# 6 Result (exit 0 only when PASSED); the head line and the open points go into the final message
sichtcheck status DIR
```

Check folders live in `~/.local/share/sichtcheck/` (override with `SICHTCHECK_HOME`); `sichtcheck list` shows the
latest.

### Categories

| Profile `3d` | what to look at | close-up required |
|---|---|---|
| `geometry` | silhouette, facets/edges, cracks, holes, interpenetration, joints | yes |
| `material` | skin/materials: gloss, roughness, pores/texture, translucency, seams, plastic or balloon look | yes |
| `light` | brightness, contrast, light sources and direction, shadows, haze/glow | yes |
| `color` | hue, saturation, white balance, mood | no |
| `scene` | props, clutter, wear, background density, arrangement | yes |
| `proportions` | sizes, pose, camera/framing, perspective | no |

Profile `ui`: `layout`, `typography`, `color`, `components`, `content`, `impression`.

### Critical spots (always as a close-up)

- **Character:** shoulders, elbows, knees, hip crease, hands, face, hairline; plus a patch of smooth skin in grazing
  light. From several views (front, back, side, first person), never just one.
- **Room:** brightest and darkest zone, every visible wall/corner, the floor, the spot with the most clutter.
- **Material:** where a highlight sits, and where two materials meet.
- **UI:** smallest text, tightest spacing, every card/button once at full size.

A crop with less than 220 px of source image is a blurry zoom and does not count: render closer or bigger.

### Fresh reviewer

- Use the Agent tool with the content of `reviewer_brief.md` as the prompt, unchanged. No `fork`, no hints about
  what was changed or what you believe.
- No reviewer available: the status stays "failed - reviewer missing". That is then the result.
- The reviewer writes `DIR/reviewer_answer.json` itself. Never write or edit that file yourself (the hook treats that
  as invalid). If it refuses or returns no JSON: take a new reviewer, do not fill it in yourself.

## When the user says it looks bad

The hook injects the protocol and locks visible changes until it exists:

1. Stop. Do not repeat the same path.
2. Why did my check let this through? (What did I actually look at, what did I base "matches" on?)
3. Research how others solve exactly this: at least one concrete source.
4. Change the approach and record it:
   `sichtcheck critique --text "…" --why "…" --research "source: insight" --old "…" --new "…" [--limit "…"]`
5. Then rebuild and check again from step 1. Not criticism of the visuals: `sichtcheck critique --none "reason"`.

## Limits (say them out loud)

- The model sees images at roughly 2000 px on the long edge at most; anything bigger is downscaled. Hence the close-ups.
- A small reference (under 600 px) holds no fine detail. "Like the reference" cannot be proven for pores, fabric
  or small objects then; get a bigger reference or judge against the request.
- The measured hints compare coarse image statistics and assume a similar framing.
- A still image shows no motion and not the picture inside a VR headset. That stays "unchecked" until the user has
  seen it there.
- The reviewer is the same model with a fresh context: independent of your work, not of your blind spots. The
  user's judgement ranks above both.

Tests: `python3 tests/test_sichtcheck.py` and `python3 tests/test_guard.py` (from the skill folder).
