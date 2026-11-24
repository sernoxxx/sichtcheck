# Install sichtcheck — instructions for Claude Code

> **Human:** give your Claude Code agent the link to this repo and say
> *"Install this and set it up in my Claude Code."* Everything below is for the agent.

---

You are installing **sichtcheck**, a Claude Code skill plus a hook. Before the agent may claim that something visual
matches a reference or looks good, it must compare result and reference side by side, open close-ups, judge every
category and let a fresh reviewer agent try to fail it. The hook blocks claims that skip this.

Do these steps in order. Talk to the user in their language. Ask only where it says so.

## 1. Check the requirements

```bash
python3 --version     # 3.9 or newer
git --version
python3 -c "import PIL, numpy; print('ok')"
```

If Pillow or NumPy is missing:

```bash
python3 -m pip install --user pillow numpy || python3 -m pip install --user --break-system-packages pillow numpy
```

## 2. Install the skill and the command

```bash
git clone https://github.com/sernoxxx/sichtcheck.git ~/.claude/skills/sichtcheck
mkdir -p ~/.local/bin
ln -sf ~/.claude/skills/sichtcheck/sichtcheck.py ~/.local/bin/sichtcheck
chmod +x ~/.claude/skills/sichtcheck/sichtcheck.py
sichtcheck --help | head -3
```

If `sichtcheck` is not found, `~/.local/bin` is not on the PATH: tell the user and use
`python3 ~/.claude/skills/sichtcheck/sichtcheck.py` instead until they fix it.

## 3. Run the tests

```bash
python3 ~/.claude/skills/sichtcheck/tests/test_sichtcheck.py
python3 ~/.claude/skills/sichtcheck/tests/test_guard.py
```

Both must print `OK`. If not, stop and show the user the output.

## 4. Add the hook

Merge these three entries into `~/.claude/settings.json` under `"hooks"`. Read the file first and **add** to the
existing arrays — never replace other hooks. Create the file with `{"hooks": {}}` if it does not exist.

```json
{
  "hooks": {
    "UserPromptSubmit": [
      { "hooks": [ { "type": "command", "command": "python3 \"$HOME/.claude/skills/sichtcheck/hooks/sichtcheck_guard.py\" prompt", "timeout": 5 } ] }
    ],
    "PreToolUse": [
      { "matcher": "Edit|Write|NotebookEdit|mcp__blender__.*",
        "hooks": [ { "type": "command", "command": "python3 \"$HOME/.claude/skills/sichtcheck/hooks/sichtcheck_guard.py\" pre", "timeout": 5 } ] }
    ],
    "Stop": [
      { "hooks": [ { "type": "command", "command": "python3 \"$HOME/.claude/skills/sichtcheck/hooks/sichtcheck_guard.py\" stop", "timeout": 15 } ] }
    ]
  }
}
```

Validate afterwards: `python3 -c "import json,os; json.load(open(os.path.expanduser('~/.claude/settings.json')))"`.

## 5. Add the rule

Append this block to `~/.claude/CLAUDE.md` (create it if missing; skip if a "sichtcheck" section already exists):

```markdown
## Visual checks: sichtcheck

Before any statement about how something looks (render, screenshot, 3D figure, room, material, light, UI, image),
load the skill **sichtcheck** and run it: result next to the reference, close-ups of the critical spots, a verdict
per category, a fresh reviewer agent. "1:1", "identical", "looks good", "like the reference", "done" only with
`SICHTCHECK PASSED`. Otherwise say literally "sichtcheck failed" plus what differs; without a check say literally
"visuals unchecked".
```

## 6. Tell the user

In two or three sentences: what was installed, that the next visual claim will go through sichtcheck, and that
Claude Code must be restarted (or `/hooks` reopened) for the hook to load. Mention that check folders are kept in
`~/.local/share/sichtcheck/`.

## Uninstall

Remove the three hook entries from `~/.claude/settings.json`, the "Visual checks: sichtcheck" block from
`~/.claude/CLAUDE.md`, then `rm ~/.local/bin/sichtcheck` and `rm -r ~/.claude/skills/sichtcheck`.
