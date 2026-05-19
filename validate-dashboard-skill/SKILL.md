---
name: validate-dashboard
description: >
  Run a comprehensive health check on a large single-file HTML dashboard after any edit.
  Use this skill WHENEVER the user: edits a large HTML file (>5 MB), replaces a JSON block
  embedded in HTML, edits JavaScript functions near the end of an HTML file, asks to
  "validate", "QA", or "check" a dashboard, or mentions Edit-tool truncation or a broken
  dashboard. The validator catches silent file truncation, unbalanced JS braces, missing
  functions, broken JSON, and mismatched HTML structure — problems that the Edit tool can
  cause silently without throwing any error.
---

# validate-dashboard

A reusable validator for large single-file HTML dashboards. It runs 11 structural and
content checks and exits non-zero if anything is broken. Run it after EVERY significant
edit to a dashboard file, especially any edit near the end of the file.

## Why this exists

The Edit tool can silently truncate a file when it replaces a large block near EOF. A 25 MB
dashboard can lose its last 500 lines and look fine until opened in a browser. This
validator exists to catch that class of error before the user ever sees a broken page.

## Bundled script

The validator lives at `scripts/validate_dashboard.py` relative to this SKILL.md.

Run it via Bash:

```bash
python "/sessions/<session>/mnt/.claude/skills/validate-dashboard/scripts/validate_dashboard.py" \
       "/sessions/<session>/mnt/<your-folder>/dashboard.html"
```

Exit codes: **0** = all clear, **1** = errors found.

## How to use this skill

### Step 1 - Identify the dashboard file

Ask the user (or infer from context) which HTML file to validate.

### Step 2 - Run with generic checks first

For any new project, run with defaults first. It will pass the generic checks (file
structure, brace balance, script tag balance, file size) even without project-specific
config.

### Step 3 - Configure for the project

The script has a configuration block at the top (~lines 35-95). Adapt these constants:

| Constant | What it checks | How to adapt |
|---|---|---|
| `REQUIRED_JS` | Named functions the JS must contain | List each function + a unique substring |
| `FUNCTION_SENTINELS` | Unique strings near the END of key function bodies | Pick a line that only appears in each function's closing logic |
| `NEAR_EOF_RISK_SYMBOLS` | Symbols near EOF most vulnerable to truncation | `window.*` assignments or late-declared vars |
| `REQUIRED_IDS` | DOM element IDs the JS depends on | Check `getElementById(...)` calls |
| `REQUIRED_THEMES` / `EXPECTED_THEME_COUNT` | Theme IDs in embedded JSON | Match actual theme list; 0 if no theme JSON |
| `DATA_MARKERS` | Embedded JS data blocks | Match exact string as it appears in HTML |
| `THRESHOLD_CHECKS` | UI threshold values that must be consistent | Key literal strings in conditional rendering logic |
| `SIZE_MIN_MB` / `SIZE_MAX_MB` | Expected file size range | +-20% of known-good file size |

Save the adapted validator alongside the dashboard for future use.

### Step 4 - Interpret results

**Common failures:**

- `File does not end with </html>` - Edit tool truncated the file tail. Fix with Python
  write (not Edit tool), reconstructing and appending the missing tail.
- `script block #N - M unclosed braces` - A JS function was cut mid-body.
- `Missing: <functionName>` - Required function absent; likely truncated away.
- `<functionName> body appears truncated` - Function exists but closing logic is missing.
- `'<symbol>' at XX.X% - in danger zone` - Symbol in last 10% of file, vulnerable to
  every future Edit-tool call. Use Python writes for changes in that region.
- `INSIGHTS_DATA parse failed` - Embedded JSON is malformed or cut short.
- `Missing element: #<id>` - A DOM element the JS expects is absent.

**On any error:** do NOT use the Edit tool to fix it - that risks a second truncation.
Use Python to surgically append or replace the affected section.

### Step 5 - Re-run after every fix

Always re-run the validator after every fix. A clean run is the sign-off before moving on.

## Adapting to a new project from scratch

1. Grep the dashboard to discover functions, DOM IDs, and embedded data blocks.
2. Write a project-specific copy of `validate_dashboard.py` with the correct config.
3. Offer to save the adapted validator alongside the dashboard for future use.

## JS brace balance checker (core innovation)

This function counts `{` vs `}` in a JS block while correctly skipping strings and comments:

```python
def js_brace_balance(block):
    depth = 0; i = 0; n = len(block); in_str = None
    while i < n:
        c = block[i]
        if in_str and c == '\\': i += 2; continue
        if in_str:
            if c == in_str: in_str = None
            i += 1; continue
        if c in ('"', "'", '`'): in_str = c; i += 1; continue
        if c == '/' and i+1<n and block[i+1]=='/':
            while i<n and block[i]!='\n': i+=1
            continue
        if c == '/' and i+1<n and block[i+1]=='*':
            i+=2
            while i+1<n and not(block[i]=='*' and block[i+1]=='/'): i+=1
            i+=2; continue
        if c=='{': depth+=1
        elif c=='}': depth-=1
        i+=1
    return depth  # 0=balanced, >0=unclosed, <0=extra closes
```
