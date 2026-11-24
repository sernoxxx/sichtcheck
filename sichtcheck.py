#!/usr/bin/env python3
"""sichtcheck - a visual check for still images (renders, screenshots, UI) against a reference and the request.

Principles:
- A result does NOT match until proven otherwise (every category starts "open").
- Numbers are evidence, not a verdict: a measured hint can block a pass, never justify one.
- "Passed" only when your own findings AND a fresh reviewer pass every category.

  sichtcheck new --out RESULT [--out ...] --ref REFERENCE --req "request, verbatim" [--name N] [--profile 3d|ui]
  sichtcheck crop DIR CAT:NAME=REFBOX:OUTBOX[:N] ...     close-ups (box = x,y,w,h as fractions 0..1; "-" = no reference crop)
  sichtcheck verdict DIR CAT pass|fail|na [-d "difference"]... [--evidence FILE] [--reason T]
  sichtcheck review DIR                                  write the brief for the fresh reviewer
  sichtcheck review DIR --answer FILE|- --by "..."       record the reviewer's answer
  sichtcheck status DIR                                  result; exit 0 only when PASSED
  sichtcheck critique --text "..." --why "..." --research "..." --old "..." --new "..."   protocol after criticism
  sichtcheck list                                        recent checks
"""
import argparse
import hashlib
import json
import os
import re
import secrets
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

BASE = Path(os.environ.get("SICHTCHECK_HOME", "~/.local/share/sichtcheck")).expanduser()
PANEL = 990  # max edge per half; sheets stay <= 2000 px wide so viewing them does not downscale anything
SMALL_REF = 600  # reference long edge below this cannot prove fine detail
SMALL_CROP = 220  # native px of an output crop below this is a blurry zoom, not a close-up

# id: (label, what to look at, needs its own close-up crop before it can pass)
PROFILES = {
    "3d": {
        "geometry": ("Geometry", "silhouette, facets/edges, cracks, holes, interpenetration, joints", True),
        "material": ("Skin/materials", "gloss, roughness, pores/texture, translucency, seams, plastic or balloon look", True),
        "light": ("Light", "brightness, contrast, light sources and direction, shadows, haze/glow", True),
        "color": ("Color", "hue, saturation, white balance, mood", False),
        "scene": ("Scene/clutter", "props, clutter, wear, background density, arrangement", True),
        "proportions": ("Proportions", "sizes, pose, camera/framing, perspective", False),
    },
    "ui": {
        "layout": ("Layout", "grid, spacing, alignment, size ratios, wrapping", True),
        "typography": ("Typography", "typeface, sizes, weights, line height, legibility", True),
        "color": ("Color", "hues, contrast, gradients, transparency", False),
        "components": ("Component detail", "radii, shadows, borders, icons, states", True),
        "content": ("Content/completeness", "every element present, texts, images, nothing cut off", False),
        "impression": ("Overall impression", "hierarchy, density, matches the style of the mockup", False),
    },
}
# where a measured hint lands when it comes from the whole frame: kind -> category per profile
HINT_TARGET = {"3d": {"light": "light", "color": "color", "detail": "scene"},
               "ui": {"light": "color", "color": "color", "detail": "impression"}}
VERDICTS = ("pass", "fail", "na")
REVIEWER_VERDICTS = VERDICTS + ("cannot_judge",)
ZONES = [(0.08, "deep black", (30, 30, 120)), (0.25, "dark", (0, 120, 140)), (0.55, "mid", (140, 140, 140)),
         (0.80, "bright", (240, 170, 60)), (9.0, "highlight", (255, 255, 170))]


def die(msg, code=2):
    print("sichtcheck: " + msg, file=sys.stderr)
    sys.exit(code)


def font(size=20):
    for name in ("DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "NotoSans-Regular.ttf",
                 "/System/Library/Fonts/Helvetica.ttc", "C:/Windows/Fonts/arial.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def load(path):
    p = Path(path).expanduser()
    if not p.is_file():
        die(f"image missing: {p}")
    img = Image.open(p)
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        bg = Image.new("RGBA", img.size, (128, 128, 128, 255))
        img = Image.alpha_composite(bg, img)
    return img.convert("RGB")


def sha(path):
    return hashlib.sha256(Path(path).expanduser().read_bytes()).hexdigest()[:16]


def box(text, size):
    """'x,y,w,h' (fractions 0..1, or pixels if any value > 1) -> pixel box; '-' -> None; 'full' -> whole image."""
    if text in ("-", ""):
        return None
    w, h = size
    if text == "full":
        return (0, 0, w, h)
    try:
        x, y, bw, bh = (float(v) for v in text.split(","))
    except ValueError:
        die(f"box '{text}' is not x,y,w,h")
    if max(x, y, bw, bh) <= 1.0:
        x, y, bw, bh = x * w, y * h, bw * w, bh * h
    l, t, r, b = max(0, round(x)), max(0, round(y)), min(w, round(x + bw)), min(h, round(y + bh))
    if r - l < 4 or b - t < 4:
        die(f"box '{text}' lies outside the image ({w}x{h})")
    return (l, t, r, b)


def luma(img):
    a = np.asarray(img, dtype=np.float32) / 255.0
    return a[..., 0] * 0.2126 + a[..., 1] * 0.7152 + a[..., 2] * 0.0722


def detail_measure(img, width):
    """Mean luma gradient at a shared width, so a small reference is never compared against a sharp large render."""
    w, h = img.size
    g = img.convert("L").resize((width, max(8, round(h * width / w))), Image.LANCZOS)
    a = np.asarray(g, dtype=np.float32) / 255.0
    return float(np.abs(np.diff(a, axis=1)).mean() + np.abs(np.diff(a, axis=0)).mean())


def metrics(img, width):
    a = np.asarray(img, dtype=np.float32) / 255.0
    lu = luma(img)
    mx, mn = a.max(axis=2), a.min(axis=2)
    sat = np.where(mx > 1e-4, (mx - mn) / np.maximum(mx, 1e-4), 0.0)
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    d = np.maximum(mx - mn, 1e-4)
    hue = np.where(mx == r, ((g - b) / d) % 6, np.where(mx == g, (b - r) / d + 2, (r - g) / d + 4)) * 60.0
    colorful = (sat > 0.2) & (mx > 0.12)
    tone = None
    if colorful.mean() >= 0.10:
        rad = np.deg2rad(hue[colorful])
        tone = float(np.rad2deg(np.arctan2(np.sin(rad).mean(), np.cos(rad).mean())) % 360)
    return {
        "bright": float(lu.mean()), "p05": float(np.percentile(lu, 5)), "p50": float(np.percentile(lu, 50)),
        "p95": float(np.percentile(lu, 95)), "contrast": float(lu.std()),
        "black": float((lu < 0.08).mean()), "highlight": float((lu > 0.80).mean()),
        "sat": float(sat.mean()), "hue": tone, "warm": float(r.mean() - b.mean()),
        "detail": detail_measure(img, width),
        "zones": [float(((lu >= (ZONES[i - 1][0] if i else -1)) & (lu < z[0])).mean()) for i, z in enumerate(ZONES)],
    }


def pct(v):
    return f"{v * 100:.0f} %"


def hints(ref, out):
    """Measured differences between two regions as (kind, text). Hints only ever block a pass."""
    width = max(16, min(ref.size[0], out.size[0], 192))
    a, b = metrics(ref, width), metrics(out, width)
    h = []
    d = b["bright"] - a["bright"]
    if abs(d) >= 0.06:
        h.append(("light", f"result {'brighter' if d > 0 else 'darker'}: mean brightness {pct(b['bright'])} vs {pct(a['bright'])}"))
    d = b["black"] - a["black"]
    if abs(d) >= 0.12:
        h.append(("light", f"{'more' if d > 0 else 'less'} true black: {pct(b['black'])} of the area vs {pct(a['black'])}"))
    d = b["highlight"] - a["highlight"]
    if abs(d) >= 0.08:
        h.append(("light", f"{'more' if d > 0 else 'fewer'} highlights: {pct(b['highlight'])} vs {pct(a['highlight'])}"))
    if a["contrast"] > 0.02:
        q = b["contrast"] / a["contrast"]
        if q < 0.75 or q > 1.33:
            h.append(("light", f"contrast {'flatter' if q < 1 else 'harsher'}: {b['contrast']:.2f} vs {a['contrast']:.2f}"))
    d = b["sat"] - a["sat"]
    if abs(d) >= 0.08:
        h.append(("color", f"saturation {'higher' if d > 0 else 'lower'}: {pct(b['sat'])} vs {pct(a['sat'])}"))
    if a["hue"] is not None and b["hue"] is not None:
        d = abs(a["hue"] - b["hue"])
        d = min(d, 360 - d)
        if d >= 25:
            h.append(("color", f"hue shifted by {d:.0f}° (result {b['hue']:.0f}°, reference {a['hue']:.0f}°)"))
    d = b["warm"] - a["warm"]
    if abs(d) >= 0.06:
        h.append(("color", f"mood {'warmer' if d > 0 else 'cooler'} (red minus blue {b['warm']:+.2f} vs {a['warm']:+.2f})"))
    if a["detail"] > 0.004:
        q = b["detail"] / a["detail"]
        if q < 0.70:
            h.append(("detail", f"result smoother/emptier: detail {b['detail']:.3f} vs {a['detail']:.3f} at the same resolution ({width} px)"))
        elif q > 1.45:
            h.append(("detail", f"result busier/more detailed: detail {b['detail']:.3f} vs {a['detail']:.3f} at the same resolution ({width} px)"))
    return h, a, b


def fit(img, bw, bh):
    w, h = img.size
    s = min(bw / w, bh / h)
    return img.resize((max(1, round(w * s)), max(1, round(h * s))), Image.LANCZOS), s


def sheet(parts, path, height=PANEL):
    """parts: [(image or None, label)] -> one sheet, panels side by side with a label strip, <= 2000 px wide."""
    parts = [(i, t) for i, t in parts if i is not None]
    bw = PANEL if len(parts) > 1 else 2 * PANEL
    target_h = min([height] + [round(bw * i.size[1] / i.size[0]) for i, _ in parts])
    scaled = []
    for img, text in parts:
        k, s = fit(img, bw, target_h)
        extra = f"  {img.size[0]}x{img.size[1]} px" + (f", {s:.1f}x enlarged" if s > 1.15 else "")
        scaled.append((k, text + extra))
    head, gap = 30, 10
    width = sum(k.size[0] for k, _ in scaled) + gap * (len(scaled) - 1)
    canvas = Image.new("RGB", (width, head + max(k.size[1] for k, _ in scaled)), (18, 18, 18))
    draw, x = ImageDraw.Draw(canvas), 0
    for k, text in scaled:
        draw.text((x + 6, 4), text, fill=(255, 255, 255), font=font(18))
        canvas.paste(k, (x, head))
        x += k.size[0] + gap
    canvas.save(path)
    return canvas.size


def zone_image(img):
    lu = luma(img)
    out = np.zeros(lu.shape + (3,), dtype=np.uint8)
    low = -1.0
    for limit, _, color in ZONES:
        out[(lu >= low) & (lu < limit)] = color
        low = limit
    return Image.fromarray(out)


def check_dir(text):
    d = Path(text).expanduser()
    if not (d / "check.json").is_file():
        die(f"no check in {d} (check.json missing)")
    return d


def read_check(d):
    return json.loads((d / "check.json").read_text())


def write_check(d, c):
    (d / "check.json").write_text(json.dumps(c, ensure_ascii=False, indent=1))


def categories(c):
    return PROFILES[c["profile"]]


def store_hints(c, where, items, cat=None):
    target = HINT_TARGET[c["profile"]]
    c["hints"] = [h for h in c["hints"] if h["where"] != where]
    for kind, text in items:
        c["hints"].append({"cat": cat if (cat and kind == "detail") else target[kind], "where": where, "text": text})


def cmd_new(a):
    if not a.ref and not a.no_ref:
        die("--ref missing. If there really is no reference: --no-ref \"reason\" (then '1:1' or 'like the reference' can never be proven).")
    name = re.sub(r"[^\w-]+", "-", a.name or Path(a.out[0]).stem).strip("-")[:40] or "check"
    d = Path(a.dir).expanduser() if a.dir else BASE / f"{time.strftime('%Y-%m-%d_%H%M%S')}-{name}"
    d.mkdir(parents=True, exist_ok=True)
    ref = load(a.ref) if a.ref else None
    c = {"id": "SC-" + secrets.token_hex(4), "name": name, "profile": a.profile, "created": time.time(),
         "req": a.req or [], "no_ref": a.no_ref,
         "ref": {"path": str(Path(a.ref).expanduser().resolve()), "size": list(ref.size), "sha": sha(a.ref)} if ref else None,
         "outs": [], "sheets": [], "crops": [], "hints": [], "limits": [], "verdict": {}, "reviewer": None}
    for n, o in enumerate(a.out, 1):
        img = load(o)
        c["outs"].append({"path": str(Path(o).expanduser().resolve()), "size": list(img.size), "sha": sha(o)})
        file = f"sheet_{n}.png"
        sheet([(ref, "REFERENCE"), (img, f"RESULT {n}")], d / file)
        c["sheets"].append(file)
        if ref is not None:
            items, ma, mb = hints(ref, img)
            store_hints(c, f"whole_{n}", items)
            if n == 1:
                sheet([(zone_image(ref), "REFERENCE brightness zones"), (zone_image(img), "RESULT brightness zones")], d / "light.png")
                c["sheets"].append("light.png")
                c["zones"] = {"ref": ma["zones"], "out": mb["zones"]}
        if max(img.size) < 1000:
            c["limits"].append(f"Result {n} is only {img.size[0]}x{img.size[1]} px: render bigger or closer for close-ups.")
    if ref is not None and max(ref.size) < SMALL_REF:
        c["limits"].append(
            f"Reference is only {ref.size[0]}x{ref.size[1]} px: fine detail (pores, fabric, small objects) is not in the "
            "reference. 'Like the reference' cannot be proven for it; also judge close-up quality against the request, "
            "or get a bigger reference.")
    if not ref:
        c["limits"].append(f"No reference ({a.no_ref}): claims like '1:1' or 'like the mockup' cannot be proven.")
    if not c["req"]:
        c["limits"].append("No verbatim request recorded (--req): the reviewer does not know the bar.")
    write_check(d, c)
    print(f"Check {c['id']} created: {d}")
    show_next(d, c)


def show_next(d, c):
    print("\nLOOK AT THESE NOW (open every image on its own):")
    for s in c["sheets"]:
        print(f"  {d / s}")
    for cr in c["crops"]:
        print(f"  {d / cr['file']}")
    if c.get("zones"):
        print("\nBrightness zones (share of the area)   reference   result")
        for (_, zname, _), r, o in zip(ZONES, c["zones"]["ref"], c["zones"]["out"]):
            print(f"  {zname:<12} {pct(r):>25} {pct(o):>8}")
    if c["hints"]:
        print("\nMEASURED HINTS (block 'pass' until fixed or explained with a reason):")
        for h in c["hints"]:
            print(f"  [{h['cat']}] {h['where']}: {h['text']}")
    if c["limits"]:
        print("\nLIMITS:")
        for g in c["limits"]:
            print(f"  - {g}")
    missing = [k for k, (_, _, needs) in categories(c).items() if needs and not any(cr["cat"] == k for cr in c["crops"])]
    if missing:
        print(f"\nClose-ups still missing for: {', '.join(missing)}  ->  sichtcheck crop {d} CAT:NAME=REFBOX:OUTBOX ...")


def cmd_crop(a):
    d = check_dir(a.dir)
    c = read_check(d)
    ref = load(c["ref"]["path"]) if c["ref"] else None
    for spec in a.spec:
        m = re.fullmatch(r"(\w+):([\w-]+)=([^:]+):([^:]+)(?::(\d+))?", spec)
        if not m:
            die(f"'{spec}' is not CAT:NAME=REFBOX:OUTBOX[:N]")
        cat, name, rb, ob, n = m.group(1), m.group(2), m.group(3), m.group(4), int(m.group(5) or 1)
        if cat not in categories(c):
            die(f"category '{cat}' does not exist. Allowed: {', '.join(categories(c))}")
        if n > len(c["outs"]):
            die(f"result {n} does not exist")
        out = load(c["outs"][n - 1]["path"])
        obox, rbox = box(ob, out.size), (box(rb, ref.size) if ref is not None else None)
        if obox is None:
            die("OUTBOX must not be empty")
        oc, rc = out.crop(obox), (ref.crop(rbox) if rbox else None)
        file = f"crop_{cat}_{name}.png"
        sheet([(rc, f"REFERENCE {name}"), (oc, f"RESULT {n} {name} [{cat}]")], d / file, height=900)
        entry = {"cat": cat, "name": name, "file": file, "out": n, "refbox": rb, "outbox": ob,
                 "px": [list(rc.size) if rc else None, list(oc.size)], "warnings": []}
        if min(oc.size) < SMALL_CROP:
            entry["warnings"].append(f"only {oc.size[0]}x{oc.size[1]} px of source: a blurry zoom, not a close-up - render closer")
        if rc is None and ref is not None:
            entry["warnings"].append("no reference crop: compared against the request only")
        c["crops"] = [x for x in c["crops"] if x["file"] != file] + [entry]
        if rc is not None:
            store_hints(c, f"crop_{cat}_{name}", hints(rc, oc)[0], cat)
        for w in entry["warnings"]:
            print(f"  ! {file}: {w}")
    write_check(d, c)
    show_next(d, c)


def cmd_verdict(a):
    d = check_dir(a.dir)
    c = read_check(d)
    if a.cat not in categories(c):
        die(f"category '{a.cat}' does not exist. Allowed: {', '.join(categories(c))}")
    label, _, needs_crop = categories(c)[a.cat]
    open_hints = [h for h in c["hints"] if h["cat"] == a.cat]
    if a.verdict == "fail" and not a.diff:
        die("fail needs at least one -d \"difference\" (where, what, which direction).")
    if a.verdict == "na" and not a.reason:
        die("na needs --reason.")
    if a.verdict == "pass":
        crops = [x for x in c["crops"] if x["cat"] == a.cat]
        if needs_crop and not crops:
            die(f"'{label}' cannot pass without a close-up: first sichtcheck crop {d} {a.cat}:NAME=REFBOX:OUTBOX")
        if needs_crop and all(x["warnings"] and "blurry zoom" in " ".join(x["warnings"]) for x in crops):
            die(f"'{label}': every close-up is a blurry zoom. Render closer/bigger and check again.")
        if not a.evidence:
            die("pass needs --evidence FILE (the sheet or close-up where you saw it).")
        for e in a.evidence:
            if not (d / e).is_file():
                die(f"evidence {e} is not in {d}")
        if a.diff:
            die("pass with differences does not exist: fix them, or use fail.")
        if open_hints and not a.hint_ok:
            die("Measured hints open:\n  " + "\n  ".join(h["text"] for h in open_hints)
                + "\nFix them, or explain with --hint-ok \"why the number misleads here\".")
    c["verdict"][a.cat] = {"verdict": a.verdict, "diffs": a.diff or [], "evidence": a.evidence or [],
                           "reason": a.reason, "hint_ok": a.hint_ok, "time": time.time()}
    write_check(d, c)
    print(f"[{a.cat}] {a.verdict}" + (f" ({len(a.diff)} differences)" if a.diff else ""))


def brief(d, c):
    cats = "\n".join(f'- "{k}": {v[0]} - {v[1]}' for k, v in categories(c).items())
    images = "\n".join(f"- {d / s}" for s in c["sheets"]) + "".join(
        f"\n- {d / x['file']}  (close-up, category {x['cat']}" + ("; " + "; ".join(x["warnings"]) if x["warnings"] else "") + ")"
        for x in c["crops"])
    req = "\n".join(f"> {r}" for r in c["req"]) or "> (none recorded)"
    limits = "\n".join(f"- {g}" for g in c["limits"]) or "- none"
    schema = json.dumps({"categories": {k: {"verdict": "pass | fail | cannot_judge | na",
                                            "differences": ["where, what, which direction"]} for k in categories(c)},
                         "worst_flaws": ["the three most visible flaws, worst first"]}, indent=1)
    return f"""You are an independent visual reviewer. Check ID: {c['id']}

You have not seen how this result was made and you will not be told. Do not read code, logs or any file other than
the images listed below. Your job is to find flaws. Assume the result does NOT match until the images prove it does.
Give no credit for effort or for "better than before" - only the picture counts.

What the client asked for (verbatim):
{req}

Open every image with the Read tool, one by one. Left half = REFERENCE (if there is one), right half = RESULT.
{images}

Known limits of this material:
{limits}

Judge each category on its own:
{cats}

Rules:
- "pass" only if you looked at the close-up for that category and see no difference a layperson would notice.
  In doubt: "fail".
- List every difference you can see: where in the picture, what differs, in which direction (brighter, smoother, emptier ...).
- Also report defects that are wrong regardless of the reference: cracks, holes, clipping, faceted silhouettes,
  plastic or balloon-like skin, flat even lighting, sterile empty rooms, stretched textures, visible seams.
- Cannot judge a category from these images (region not shown, resolution too low)? Use "cannot_judge" and say why.
  Never pass a category by default.
- Describe only what is needed for the visual comparison, in neutral technical terms.

Write exactly one JSON object with the Write tool to {d / 'reviewer_answer.json'} and reply only "written".
(If you cannot write files, reply with the JSON object and nothing else.) Differences in plain language:
{schema}
"""


def cmd_review(a):
    d = check_dir(a.dir)
    c = read_check(d)
    if not a.answer:
        missing = [k for k, (_, _, needs) in categories(c).items() if needs and not any(x["cat"] == k for x in c["crops"])]
        if missing:
            die(f"Create close-ups for {', '.join(missing)} first - the reviewer must see the same critical spots.")
        text = brief(d, c)
        (d / "reviewer_brief.md").write_text(text)
        c["brief_time"] = time.time()
        write_check(d, c)
        print(text)
        print(f"--- Brief saved to {d / 'reviewer_brief.md'}. Hand it unchanged to a fresh agent (no fork, nothing about "
              f"the work). It writes {d / 'reviewer_answer.json'}; do not touch that file yourself, then: "
              f"sichtcheck review {d} --answer {d / 'reviewer_answer.json'} --by \"...\"", file=sys.stderr)
        return
    if not c.get("brief_time"):
        die("Write the brief first (sichtcheck review DIR) and hand it to a fresh reviewer.")
    if not a.by:
        die("--by missing: which agent/model reviewed it?")
    raw = sys.stdin.read() if a.answer == "-" else Path(a.answer).expanduser().read_text()
    m = re.search(r"\{.*\}", raw, re.S)
    try:
        ans = json.loads(m.group(0)) if m else None
    except ValueError:
        ans = None
    if not ans or not isinstance(ans.get("categories"), dict):
        die("Answer has no JSON with 'categories'. Ask the reviewer for the required format.")
    clean = {}
    for k in categories(c):
        e = ans["categories"].get(k)
        if not isinstance(e, dict) or e.get("verdict") not in REVIEWER_VERDICTS:
            die(f"Answer incomplete: category '{k}' is missing or has no valid verdict.")
        if e["verdict"] in ("fail", "cannot_judge") and not e.get("differences"):
            die(f"Answer incomplete: '{k}' is {e['verdict']} without a reason.")
        clean[k] = {"verdict": e["verdict"], "differences": [str(u) for u in e.get("differences") or []]}
    c["reviewer"] = {"by": a.by, "time": time.time(), "categories": clean,
                     "flaws": [str(x) for x in ans.get("worst_flaws") or []]}
    write_check(d, c)
    print(f"Reviewer verdict recorded ({a.by}): " + ", ".join(f"{k}={v['verdict']}" for k, v in clean.items()))


def evaluate(d, c):
    """-> (ok, rows, open points, blockers)"""
    blockers = []
    for n, o in enumerate(c["outs"], 1):
        p = Path(o["path"])
        if not p.is_file() or sha(p) != o["sha"]:
            blockers.append(f"Result {n} changed since the check or is missing: check again (sichtcheck new).")
    rv = c.get("reviewer")
    if not rv:
        blockers.append("Fresh reviewer missing: no 'passed' without one.")
    elif rv["time"] < max([u.get("time", 0) for u in c["verdict"].values()] + [0]) - 86400:
        blockers.append("Reviewer verdict is more than a day older than your own findings: have it reviewed again.")
    rows, open_points = [], []
    for k, (label, _, needs) in categories(c).items():
        own = c["verdict"].get(k, {"verdict": "open", "diffs": []})
        other = (rv or {}).get("categories", {}).get(k, {"verdict": "open", "differences": []})
        good = own["verdict"] == "pass" and other["verdict"] == "pass"
        skip = own["verdict"] == "na" and other["verdict"] in ("na", "pass")
        if own["verdict"] == "pass" and needs and not any(x["cat"] == k for x in c["crops"]):
            good = False
        rows.append((k, label, own["verdict"], other["verdict"], "passed" if good else "n/a" if skip else "FAILED"))
        if not (good or skip):
            points = list(own.get("diffs", [])) + [f"{u} (reviewer)" for u in other.get("differences", [])]
            if own["verdict"] == "open":
                points.append("own finding missing")
            if other["verdict"] == "open":
                points.append("reviewer verdict missing")
            measured = [h for h in c["hints"] if h["cat"] == k and not own.get("hint_ok")]
            points += [f"measured hint ({h['where']}): {h['text']}" for h in measured[:3]]
            if len(measured) > 3:
                points.append(f"... and {len(measured) - 3} more measured hints (check.json)")
            open_points.append((label, points))
    ok = not blockers and not open_points
    return ok, rows, open_points, blockers


def cmd_status(a):
    d = check_dir(a.dir)
    c = read_check(d)
    ok, rows, open_points, blockers = evaluate(d, c)
    print(f"{'Category':<24}{'own finding':<18}{'fresh reviewer':<18}result")
    for _, label, e, f, r in rows:
        print(f"{label:<24}{e:<18}{f:<18}{r}")
    n_ok = sum(1 for z in rows if z[4] != "FAILED")
    if ok:
        head = f"SICHTCHECK PASSED ({c['id']}): all {len(rows)} categories, own findings and fresh reviewer."
    else:
        head = f"SICHTCHECK FAILED ({c['id']}): {len(rows) - n_ok} of {len(rows)} categories open."
    print("\n" + head)
    for b in blockers:
        print(f"  ! {b}")
    for label, points in open_points:
        print(f"  {label}:")
        for p in points:
            print(f"    - {p}")
    if c["limits"]:
        print("Limits (always report them):")
        for g in c["limits"]:
            print(f"  - {g}")
    if not ok:
        print("\nAllowed now: say exactly what still differs (list above). Not: '1:1', 'identical', 'looks good', 'done'.")
    (d / "status.json").write_text(json.dumps({
        "ok": ok, "id": c["id"], "time": time.time(), "head": head, "blockers": blockers,
        "categories": {z[0]: {"own": z[2], "reviewer": z[3], "result": z[4]} for z in rows},
        "open": [{"category": l, "points": p} for l, p in open_points], "limits": c["limits"],
        "images": c["sheets"] + [x["file"] for x in c["crops"]],
        "reviewer_by": (c.get("reviewer") or {}).get("by")}, ensure_ascii=False, indent=1))
    sys.exit(0 if ok else 1)


def cmd_critique(a):
    target = BASE / "critique"
    target.mkdir(parents=True, exist_ok=True)
    if a.none:
        (target / f"{time.strftime('%Y-%m-%d_%H%M%S')}-none.md").write_text(f"No criticism of the visuals: {a.none}\n")
        print("Noted: no criticism of the visuals.")
        return
    missing = [n for n in ("text", "why", "research", "old", "new") if not getattr(a, n)]
    if missing:
        die("Critique protocol incomplete, missing: " + ", ".join("--" + f for f in missing))
    if a.old.strip().lower() == a.new.strip().lower():
        die("--old and --new are the same: the approach has to change, not just the knob.")
    file = target / f"{time.strftime('%Y-%m-%d_%H%M%S')}.md"
    file.write_text(
        f"# Critique protocol {time.strftime('%Y-%m-%d %H:%M')}\n\n## The criticism (verbatim)\n{a.text}\n\n"
        f"## Why my check let it through\n{a.why}\n\n## How others solve it (research)\n"
        + "\n".join(f"- {r}" for r in a.research)
        + f"\n\n## Previous approach (do not repeat)\n{a.old}\n\n## New approach\n{a.new}\n"
        + (f"\n## Not reachable with the current tools\n{a.limit}\n" if a.limit else ""))
    print(f"Critique protocol: {file}")


def cmd_list(a):
    if not BASE.is_dir():
        return
    for d in sorted((p for p in BASE.iterdir() if (p / "check.json").is_file()), reverse=True)[:a.n]:
        st = d / "status.json"
        head = json.loads(st.read_text())["head"] if st.is_file() else "no status yet"
        print(f"{d.name}: {head}")


def main():
    p = argparse.ArgumentParser(prog="sichtcheck", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("new")
    s.add_argument("--out", action="append", required=True)
    s.add_argument("--ref")
    s.add_argument("--no-ref", dest="no_ref")
    s.add_argument("--req", action="append")
    s.add_argument("--name")
    s.add_argument("--profile", choices=list(PROFILES), default="3d")
    s.add_argument("--dir")
    s.set_defaults(f=cmd_new)
    s = sub.add_parser("crop")
    s.add_argument("dir")
    s.add_argument("spec", nargs="+")
    s.set_defaults(f=cmd_crop)
    s = sub.add_parser("verdict")
    s.add_argument("dir")
    s.add_argument("cat")
    s.add_argument("verdict", choices=VERDICTS)
    s.add_argument("-d", "--diff", action="append")
    s.add_argument("--evidence", action="append")
    s.add_argument("--reason")
    s.add_argument("--hint-ok", dest="hint_ok")
    s.set_defaults(f=cmd_verdict)
    s = sub.add_parser("review")
    s.add_argument("dir")
    s.add_argument("--answer")
    s.add_argument("--by")
    s.set_defaults(f=cmd_review)
    s = sub.add_parser("status")
    s.add_argument("dir")
    s.set_defaults(f=cmd_status)
    s = sub.add_parser("critique")
    for n in ("text", "why", "old", "new", "limit", "none"):
        s.add_argument("--" + n)
    s.add_argument("--research", action="append")
    s.set_defaults(f=cmd_critique)
    s = sub.add_parser("list")
    s.add_argument("-n", type=int, default=10)
    s.set_defaults(f=cmd_list)
    a = p.parse_args()
    a.f(a)


if __name__ == "__main__":
    main()
