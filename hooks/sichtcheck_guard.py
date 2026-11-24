#!/usr/bin/env python3
"""sichtcheck guard - Claude Code hook that holds the agent to its own visual check.

It does not ask whether *some* check ran; it reads the claim itself:

  prompt  UserPromptSubmit  the user criticises the visuals -> inject the critique protocol, set a marker
  pre     PreToolUse        marker open and no protocol written -> refuse visible changes
  stop    Stop              1. criticism open, but the agent kept building                     -> block
                            2. success claim about the visuals without a passed sichtcheck      -> block
                            3. sichtcheck failed, the final message does not say so            -> block
                            4. visible files changed, neither a sichtcheck nor "visuals unchecked" -> block

A pass only counts if `sichtcheck status DIR` ran after the last visible change, status.json is ok, every sheet and
close-up was opened with Read, and a fresh agent (not a fork) received the reviewer brief.
No model call, no cost. At most MAX_BLOCKS blocks per turn, then a warning goes to the user instead.
Claims and criticism are recognised in English and German.
"""
import json
import os
import re
import shlex
import sys
import time
from datetime import datetime
from pathlib import Path

BASE = Path(os.environ.get("SICHTCHECK_HOME", "~/.local/share/sichtcheck")).expanduser()
STATE = Path(os.environ.get("SICHTCHECK_GUARD_STATE", "~/.cache/sichtcheck_guard")).expanduser()
MAX_BLOCKS = 3
MIN_EDITS = 2

EDITS = {"Edit", "Write", "NotebookEdit"}
STRONG_EXT = re.compile(
    r"\.(gdshader|shader|glsl|wgsl|hlsl|tscn|tres|material|mat|blend|vrm|glb|gltf|fbx|obj|exr|hdr|lmbake|unity|prefab|uss"
    r"|rbxlx?|png|jpe?g|webp)$", re.I)
STRONG_PATH = re.compile(
    r"/(characters?|figures?|scenes?|rooms?|shaders?|materials?|textures?|lighting|looks?|renders?|viewmodels?|weapons?"
    r"|rigs?|avatars?)/", re.I)
WEAK_EXT = re.compile(r"\.(css|scss|html|tsx|jsx|vue|svelte|svg)$", re.I)
IMAGE = re.compile(r"\.(png|jpe?g|webp|gif|bmp|exr)$", re.I)
RENDER = re.compile(r"\bblender\b.*(\s-b\b|--background|--python)|\bgodot\S*\s.*(--script|\s-s\s|--headless|--write-movie)"
                    r"|\bunity\b.*\bcapture\b", re.I | re.S)
MUTATING_MCP = re.compile(r"^mcp__blender__(execute_blender_code|import_asset|generate_)")
VIEW_MCP = re.compile(r"^mcp__blender__look$|^mcp__claude-in-chrome__computer$|^mcp__playwright__browser_take_screenshot$")

QUOTED = re.compile(r"```.*?```|`[^`\n]*`|„[^“”\"\n]*[“”\"]|\"[^\"\n]*\"|“[^”\n]*”|‚[^‘’\n]*[‘’]|»[^«\n]*«|^\s*>.*$",
                    re.S | re.M)
THING = (r"(reference\w*|mock-?ups?|original\w*|photo\w*|figma|concept art|"
         r"Referenz\w*|Vorlage\w*|Foto|Mock-?up\w*|Design-?Vorlage|Konzeptbild\w*)")
CLAIMS = [
    re.compile(r"\b(1\s?:\s?1|1\s?zu\s?1|one[- ]to[- ]one|eins zu eins)\b", re.I),
    re.compile(r"\b(identical|indistinguishable|pixel-?perfect|spot[- ]on|identisch|deckungsgleich|pixelgenau|originalgetreu)\b",
               re.I),
    re.compile(r"\b(looks?|looking)\b[^.!?\n]{0,25}\b(good|great|right|correct|perfect|realistic|clean|professional|polished)\b",
               re.I),
    re.compile(r"\bsieht\b[^.!?\n]{0,45}\b(gut|super|sauber|richtig|stimmig|realistisch|echt|professionell|top|perfekt|toll"
               r"|klasse|hochwertig|natürlich)\b[^.!?\n]{0,12}\baus\b", re.I),
    re.compile(r"\b(match(es)?|like|same as|identical to|faithful to|true to|wie|entspricht|gleicht|trifft|matcht"
               r"|passt (jetzt |nun )?zu[rm]?)\b[^.!?\n]{0,40}\b" + THING, re.I),
    re.compile(r"\b(is|are|it's|ist|sind|wäre)\b\s+(now |jetzt |nun |damit |somit |also )?"
               r"(completely |fully |all |komplett |ganz |vollständig )?(done|finished|complete|fertig|erledigt|abgeschlossen)\b",
               re.I),
    re.compile(r"\A\s*(done|finished|fertig|erledigt)\b\s*[.!:]", re.I),
]
PROCESS_NOUN = re.compile(r"\b(bake|render\w*|build|export|download|upload|commit|push|test\w*|run|script|install\w*"
                          r"|recording|import|deploy\w*|merge|search|research|scan|measurement"
                          r"|lauf|skript|aufnahme|suche|recherche|messung)\b", re.I)
NEG_BEFORE = re.compile(r"\b(not|never|no|nothing|unlike|without|un(checked|verified|clear|proven)|neither|instead|\w+n['’]t"
                        r"|nicht|noch nicht|kein\w*|nie|niemals|ohne|un(geprüft|klar|sicher|bewiesen|belegt)|weder|statt)\b",
                        re.I)
NEG_AFTER = re.compile(r"\A\W*(it|that|this)?\s*(is|are|was|ist|sind|war|wäre|wird)?\s*(it|that|es|das|er|sie)?\s*"
                       r"(still |yet |aber |noch |längst |bei weitem )*(not|nicht|kein)\b", re.I)
FORMAT = re.compile(r"\b(format\w*|aspect|ratio|square|scal\w*|zoom|cover|pixel|seitenverh\w*|quadrat\w*|maßstab|skalier\w*)\b"
                    r"|\b(16:9|9:16|4:5|4:3)\b", re.I)
UNCHECKED = re.compile(r"\b(visuals unchecked|unchecked|not verified|unverified|ungeprüft|nicht geprüft|nicht prüfbar)\b", re.I)
FAILED = re.compile(r"sichtcheck failed|\bnot passed\b|nicht bestanden", re.I)

NEG_WORDS = (r"ugly|bad|terrible|awful|horrible|cheap|broken|garbage|trash|crap|generic|ai[- ]?slop|plastic\w*|balloon\w*"
             r"|low[- ]?poly|blocky|jagged|sterile|too (bright|dark|clean|smooth|empty|flat|plain)|nothing like|not even close"
             r"|way off|(doesn't|does not|don't|do not) (look|match)|you lied|lying|did you (even )?(look|check)"
             r"|kacke|schei(ß|ss)e|schlecht|billig|kaputt|katastroph\w*|hässlich|schrecklich|grottig|unprofessionell"
             r"|generisch|plastik\w*|luftbal\w*|ballon\w*|kantig|eckig|blockig|steril"
             r"|zu (hell|dunkel|clean|glatt|sauber|leer|flach)|stimmt (gar |überhaupt )?nicht|passt (gar |überhaupt )?nicht"
             r"|riesen ?unterschied|nicht (so |ansatzweise |annähernd )?wie|überhaupt nicht|belügst|lügst|gelogen"
             r"|hast du (dir )?(das|es)[^.?!]{0,20}angeschaut")
VIS_WORDS = (r"looks?|visuals?|image\w*|picture\w*|render\w*|screenshot\w*|reference\w*|characters?|figures?|skin|room"
             r"|light\w*|exposure|texture\w*|material\w*|models?|mesh|scene|design|layout|colou?rs?|shadows?|graphics|ui"
             r"|hair|body|face|animation\w*"
             r"|sieht|aussehen|aussieht|optik|bild\w*|referenz\w*|figur\w*|haut|raum|zimmer|licht|belichtung|textur\w*"
             r"|modell\w*|szene|charakter\w*|farbe\w*|schatten|grafik|haare|körper|gesicht")
KRITIK = re.compile(rf"\b({NEG_WORDS})\b[^.!?\n]{{0,140}}\b({VIS_WORDS})\b|\b({VIS_WORDS})\b[^.!?\n]{{0,140}}\b({NEG_WORDS})\b",
                    re.I)

NOT_USER = re.compile(r"\A\s*<(agent-message|task-notification|system-reminder|local-command)|\[Subagent hand-back\]"
                      r"|SYSTEM NOTIFICATION - NOT USER INPUT")

PROTOCOL = """VISUAL CHECK - the user says the result looks bad. Required before you change anything visible:
1. STOP. Do not try the same path (same tool, same knobs) again.
2. Work out why your check let this through: what did you actually look at, what did you base "matches" on?
3. Research how others solve exactly this problem (web, docs, skills) - at least one concrete source.
4. Change the approach and record it:
   sichtcheck critique --text "<the user's words>" --why "<why it was missed>" --research "<source: insight>" --old "<old path>" --new "<new path>" [--limit "<what the current tools cannot do>"]
5. If it cannot be reached with the current tools: say so instead of building on.
Not criticism of the visuals: sichtcheck critique --none "<reason>".
Until the protocol exists, visible changes are refused."""


def load_state(sid):
    try:
        return json.loads((STATE / f"{re.sub(r'[^\w-]', '_', sid or 'x')}.json").read_text())
    except Exception:
        return {}


def save_state(sid, st):
    STATE.mkdir(parents=True, exist_ok=True)
    (STATE / f"{re.sub(r'[^\w-]', '_', sid or 'x')}.json").write_text(json.dumps(st))


def critique_open(st):
    ts = st.get("critique")
    if not ts:
        return False
    d = BASE / "critique"
    return not (d.is_dir() and any(p.stat().st_mtime >= ts for p in d.glob("*.md")))


def kind(tool):
    """-> 'strong' (visible 3D/scene change), 'weak' (web UI file), 'view' (looked at pictures), 'render' or None."""
    name, inp = tool.get("name", ""), tool.get("input") or {}
    if name in EDITS:
        f = inp.get("file_path", "")
        if STRONG_EXT.search(f) or STRONG_PATH.search(f):
            return "strong"
        return "weak" if WEAK_EXT.search(f) else None
    if MUTATING_MCP.search(name):
        return "strong"
    if VIEW_MCP.search(name) or (name == "Read" and IMAGE.search(inp.get("file_path", ""))):
        return "view"
    if name == "Bash" and "sichtcheck" not in inp.get("command", "") and RENDER.search(inp.get("command", "")):
        return "render"
    return None


def _scan(lines):
    """(tool calls since the last real user message, whether such a message was seen)."""
    tools, seen = [], False
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        content = (entry.get("message") or {}).get("content")
        if entry.get("type") == "user" and not entry.get("isMeta"):
            if not (isinstance(content, list) and any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content)):
                tools, seen = [], True
        elif entry.get("type") == "assistant" and isinstance(content, list):
            tools += [b for b in content if isinstance(b, dict) and b.get("type") == "tool_use"]
    return tools, seen


def turn_tools(transcript, window=4_000_000):
    """Tool calls since the last real user message, in order. Reads only the end of the transcript."""
    with open(transcript, "rb") as fh:
        size = fh.seek(0, 2)
        while True:
            start = max(0, size - window)
            fh.seek(start)
            tools, seen = _scan(fh.read().decode("utf-8", "ignore").splitlines())
            if seen or start == 0:
                return tools
            window *= 2


def status_dirs(tool):
    """Check directories named by `sichtcheck status DIR` in a Bash command."""
    if tool.get("name") != "Bash":
        return []
    cmd = (tool.get("input") or {}).get("command", "")
    if "sichtcheck" not in cmd or "status" not in cmd:
        return []
    out = []
    try:
        parts = shlex.split(cmd, comments=False)
    except ValueError:
        parts = cmd.split()
    for i, p in enumerate(parts[:-1]):
        if p == "status" and "sichtcheck" in " ".join(parts[max(0, i - 3):i]):
            out.append(parts[i + 1].rstrip(";&|"))
    return out or ["?"]


def resolve(d):
    p = Path(d).expanduser()
    if (p / "status.json").is_file():
        return p
    if BASE.is_dir():  # shell variable or relative path: newest status of the last hours
        recent = sorted((x for x in BASE.glob("*/status.json") if time.time() - x.stat().st_mtime < 6 * 3600),
                        key=lambda x: x.stat().st_mtime)
        if recent:
            return recent[-1].parent
    return None


def tools_since(transcript, t0, window=6_000_000):
    """All tool_use blocks of the main chain with a timestamp >= t0 (the check may span several turns)."""
    with open(transcript, "rb") as fh:
        size = fh.seek(0, 2)
        while True:
            start = max(0, size - window)
            fh.seek(start)
            tools, older = [], False
            for line in fh.read().decode("utf-8", "ignore").splitlines():
                try:
                    e = json.loads(line)
                    ts = datetime.fromisoformat(e["timestamp"].replace("Z", "+00:00")).timestamp()
                except (ValueError, KeyError, TypeError, AttributeError):
                    continue
                if ts < t0:
                    older = True
                    continue
                c = (e.get("message") or {}).get("content")
                if e.get("type") == "assistant" and not e.get("isSidechain") and isinstance(c, list):
                    tools += [b for b in c if isinstance(b, dict) and b.get("type") == "tool_use"]
            if older or start == 0:
                return tools
            window *= 2


def reported(transcript, rx, window=4_000_000):
    """Did an assistant message of this turn, written after the last `sichtcheck status`, already say it (rx)?
    Other Stop hooks can make the turn end again with a one-word message; the report must not be demanded twice."""
    with open(transcript, "rb") as fh:
        size = fh.seek(0, 2)
        while True:
            start = max(0, size - window)
            fh.seek(start)
            hit, seen = False, False
            for line in fh.read().decode("utf-8", "ignore").splitlines():
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                c = (e.get("message") or {}).get("content")
                if e.get("type") == "user" and not e.get("isMeta"):
                    if not (isinstance(c, list) and any(isinstance(b, dict) and b.get("type") == "tool_result" for b in c)):
                        hit, seen = False, True
                elif e.get("type") == "assistant" and not e.get("isSidechain") and isinstance(c, list):
                    for b in c:
                        if not isinstance(b, dict):
                            continue
                        if b.get("type") == "tool_use" and (status_dirs(b) or kind(b) in ("strong", "render")):
                            hit = False
                        elif b.get("type") == "text" and rx.search(b.get("text", "")):
                            hit = True
            if seen or start == 0:
                return hit
            window *= 2


def proof(d, transcript):
    """-> (passed, reason it does not count)"""
    try:
        st = json.loads((d / "status.json").read_text())
        ck = json.loads((d / "check.json").read_text())
    except Exception:
        return False, "status.json or check.json missing"
    if not st.get("ok"):
        return False, st.get("head", "failed")
    since = tools_since(transcript, ck.get("created", 0) - 5)
    opened = {str(Path((t.get("input") or {}).get("file_path", "")).expanduser()) for t in since if t.get("name") == "Read"}
    missing = [b for b in st.get("images", []) if str(d / b) not in opened]
    if missing:
        return False, "not looked at (Read missing): " + ", ".join(missing)
    cid, brief = ck.get("id", "?"), str(d / "reviewer_brief.md")
    if any(t.get("name") in EDITS and Path((t.get("input") or {}).get("file_path", "")).name == "reviewer_answer.json"
           for t in since):
        return False, "the reviewer answer was written by the builder instead of the reviewer"
    for t in since:
        inp = t.get("input") or {}
        text = json.dumps(inp, ensure_ascii=False)
        if cid not in text and brief not in text:
            continue
        if t.get("name") in EDITS:
            continue
        if t.get("name") in ("Agent", "Task") and inp.get("subagent_type") != "fork":
            return True, ""
        if t.get("name") == "Bash" and re.search(r"\bclaude\b.*\s-p\b|\bcodex\b", text):
            return True, ""
    return False, f"no fresh agent received the reviewer brief {cid} (an Agent call without fork is missing)"


def claims(text):
    """Success statements about the visuals in the final message, quotes and negated sentences left out."""
    text = QUOTED.sub(" ", text or "")
    hits = []
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", text):
        for n, rx in enumerate(CLAIMS):
            for m in rx.finditer(sentence):
                if NEG_BEFORE.search(sentence[max(0, m.start() - 48):m.end()]) or NEG_AFTER.search(sentence[m.end():m.end() + 40]):
                    continue
                if n >= 5 and PROCESS_NOUN.search(sentence):
                    continue
                if n == 0 and FORMAT.search(sentence):
                    continue
                hits.append(m.group(0).strip())
    return hits


def block(reason):
    print(json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False))


def on_prompt(ev):
    prompt = ev.get("prompt", "")
    # only the user's own words count: subagent reports and task notifications also arrive as prompts
    if NOT_USER.search(prompt) or not KRITIK.search(prompt):
        return
    sid = ev.get("session_id", "")
    st = load_state(sid)
    st["critique"] = time.time()
    save_state(sid, st)
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": PROTOCOL}},
                     ensure_ascii=False))


def on_pre(ev):
    st = load_state(ev.get("session_id", ""))
    if not st.get("critique"):
        return
    if kind({"name": ev.get("tool_name", ""), "input": ev.get("tool_input") or {}}) != "strong" or not critique_open(st):
        return
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                             "permissionDecisionReason": PROTOCOL}}, ensure_ascii=False))


def on_stop(ev):
    sid, path = ev.get("session_id", ""), ev.get("transcript_path")
    if not path or not Path(path).is_file():
        return
    st = load_state(sid)
    if not ev.get("stop_hook_active"):
        st["blocks"] = 0
    tools = turn_tools(Path(path))
    final = ev.get("last_assistant_message") or ""
    kinds = [kind(t) for t in tools]
    visible = [i for i, k in enumerate(kinds) if k in ("strong", "render")]
    active = any(kinds)
    checks = [(i, d) for i, t in enumerate(tools) for d in status_dirs(t)]
    after = [d for i, d in checks if not visible or i > visible[-1]]

    reason = None
    if critique_open(st) and visible:
        reason = "The user's criticism of the visuals is still open, but you kept building.\n" + PROTOCOL
    else:
        ok, why = False, ""
        for d in after:
            checked = resolve(d)
            ok, why = proof(checked, path) if checked else (False, f"check directory {d} not found")
        found = claims(final) if active else []
        if found and not ok:
            reason = ("Claim about the visuals without evidence: " + ", ".join(f"»{f}«" for f in dict.fromkeys(found)) + ". "
                      + (f"The sichtcheck does not count: {why}. " if after else "No `sichtcheck status` ran after the last visible change. ")
                      + "Either run the sichtcheck skill in full (reference side by side, open the close-ups, every "
                        "category, fresh reviewer) and report the result - or drop the claim and say exactly what still "
                        "differs or is unchecked. Words like 1:1, identical, looks good, done only with SICHTCHECK PASSED.")
        elif after and not ok and not FAILED.search(final) and not reported(path, FAILED):
            reason = (f"The sichtcheck did not pass ({why}), but the final message does not say so. Write literally "
                      "»sichtcheck failed« and list per category what still differs, plus the limits from `sichtcheck status`.")
        elif len([i for i, k in enumerate(kinds) if k == "strong" and not any(j > i for j, _ in checks)]) >= MIN_EDITS \
                and not UNCHECKED.search(final) and not reported(path, UNCHECKED):
            reason = ("Visible files changed, but no visual check afterwards. Run the sichtcheck skill - or write literally "
                      "»visuals unchecked« in the final message and make no statement about the quality.")
    if not reason:
        save_state(sid, st)
        return
    if st.get("blocks", 0) >= MAX_BLOCKS:
        save_state(sid, st)
        print(json.dumps({"systemMessage": "sichtcheck guard: this answer claims something about the visuals without a "
                                           "passed visual check - do not rely on it."}, ensure_ascii=False))
        return
    st["blocks"] = st.get("blocks", 0) + 1
    save_state(sid, st)
    block(reason)


if __name__ == "__main__":
    try:
        event = json.load(sys.stdin)
    except ValueError:
        sys.exit(0)
    {"prompt": on_prompt, "pre": on_pre, "stop": on_stop}.get(sys.argv[1] if len(sys.argv) > 1 else "stop", on_stop)(event)
