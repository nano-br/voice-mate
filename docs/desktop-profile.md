# Claude Desktop profile

VoiceMate drives the Claude Desktop through Windows UI Automation (see `docs/research/claude-code-client.md`, sections 5 and 8). Everything the driver needs to know about the Desktop's UI lives in a **profile**: a small JSON file. When a Desktop update changes its UI, the fix is a new profile published in this repository, not a new VoiceMate release.

The code is `app/companion/win/desktop_profile.py` (format, validation, sources, update) and `app/companion/win/claude_desktop.py` (the driver that interprets it).

## Where the profile comes from

The first source that applies wins:

1. **Override**: a file the person imported (Settings, import). It lives in the profile cache folder and survives restarts. Clearing it goes back to the next source.
2. **Cached remote profile**: the last valid profile downloaded from the published URL, used only when its `profile_version` is higher than the packaged one.
3. **Packaged profile**: `app/companion/win/profiles/claude-desktop.json`, shipped inside the app.

The published copy is `profiles/claude-desktop.json` at the repository root. The two files must stay identical (a test checks it). The default URL is:

`https://raw.githubusercontent.com/nano-br/voice-mate/main/profiles/claude-desktop.json`

The update check is an HTTPS GET with `If-None-Match` (ETag), a 5 second timeout and no redirect to plain HTTP. It adopts a profile only when all of these hold: it is valid, its `schema` is supported, its `min_voicemate` (if any) is not above the running VoiceMate, and its `profile_version` is higher than the active packaged or cached one. A network error, a bad file or a refused profile is logged and the current profile stays.

## Format (schema 1)

One JSON object. Unknown keys, wrong types, out of range numbers, duplicate keys, NaN and files over 64 KiB are refused, and the reason names the field.

| Key | Meaning |
| --- | --- |
| `schema` | Format version. Only `1` is read. |
| `profile_version` | Integer, grows on every publish. Decides which profile is newer. |
| `min_voicemate` | Optional `"x.y.z"`. Older VoiceMate versions skip the profile. |
| `app` | Documentation only: `name`, `tested_versions`, `notes`. |
| `window` | Matcher for the top-level window. |
| `sidebar` | `container` (locator), `row` (step), `label` (matcher, a direct child of a row; rows without it are navigation items), `label_text` (step; the first text is the exact title), `status` (matcher), `status_text` (step; texts joined with a space). |
| `conversation` | `title_format` (contains `{title}` once; the open conversation is a document with exactly this name), `document` (locator), `visible_text` (step), `max_visible_lines`. |
| `prompt` | `editor` (locator), `text` (step; the editor exposes no value, its text lives in Text children), `focus` (matcher applied to the focused control: the focus confirmation rule). |
| `keys` | `clear_draft`, `paste`, `submit` (lists of key names, each sent as its own press, with the guards checked before each one), `interval_s` (between the events of one key, such as Ctrl and A), `wait_s` (after each key). |
| `paste` | `verify_max_chars` (a longer paste may become an attachment chip and is not verified), `verify_head_chars`, `unverified_wait_s`. |
| `timeouts` | `lock_wait_s`, `open_wait_s`, `foreground_wait_s`, `focus_wait_s`, `focus_prompt_wait_s`, `paste_wait_s`, `submit_wait_s`, `poll_s`, all in seconds. |

### Matcher

Conditions that must all hold; absent ones are not checked; at least one is required.

`control_type` (a UI Automation name such as `GroupControl`), `class_equals`, `class_prefix`, `class_contains`, `automation_id`, `name_equals`, `name_prefix`, `name_suffix`. Schema 1 has no regular expressions (Python's `re` has no timeout); a profile with `class_regex` or `name_regex` is refused as an unknown key.

The window's process is checked before any matcher runs: the profile's window matcher only ever sees windows of the Claude Desktop process, never a browser tab whose title a web page chooses. The `window` and `focus` matchers need a class, id or name condition, not only a control type.

### Step and locator

A **step** is `{"match": matcher, "max_depth": n}`: the first control, depth first, in the subtree of the root (the root is depth 0, `n` from 1 to 90) that matches.

A **locator** is a step, or `{"path": [step, ...]}` with 1 to 4 steps, each searched inside the previous result:

```json
{"path": [
  {"match": {"control_type": "PaneControl", "class_contains": "main"}, "max_depth": 6},
  {"match": {"control_type": "EditControl", "class_contains": "ProseMirror"}, "max_depth": 20}
]}
```

### Keys

Each role takes 1 to 4 names, and only these names exist: `ctrl+a`, `delete`, `ctrl+v`, `enter`, `escape`. Each role accepts a subset: `clear_draft` may use `ctrl+a`, `delete`, `escape`; `paste` only `ctrl+v`; `submit` only `enter`. The order of the flash itself (clear, copy, paste, verify, submit) is in code.

## What a profile cannot do

A profile is data and widens nothing. These guards are in code and no field changes them:

- The window must be a top-level window owned by the Claude Desktop process (`claude.exe`, or a program of the Claude MSIX package under `WindowsApps\Claude_...`). A browser tab titled "Claude" never matches. The foreground check repeats the process test.
- No key is sent unless the Claude window owns the foreground, the right conversation is open and the keyboard focus passes the `prompt.focus` rule. Each check is repeated right before each key: every key is its own press, so a click elsewhere between Ctrl+A and Delete stops the flash before Delete. The clipboard sequence number is also checked before the paste and before Enter: if another program copied something meanwhile, the flash stops, and the previous clipboard is restored only while the clipboard still holds VoiceMate's text, so a copy made during the flash is never overwritten.
- Only the five keys above can reach the keyboard: the backend refuses any other text, whatever calls it.
- There is no field that runs a command or opens a URL or a path. The only network access is the update check, to the one configured HTTPS URL.

## Writing a profile for a new Desktop version

1. Export the active profile (Settings, export) and edit a copy.
2. Inspect the Desktop with a UI Automation viewer (Accessibility Insights, or `tools/lab/claude_client`) and adjust the matchers.
3. Raise `profile_version`, list the Desktop version in `app.tested_versions`.
4. Import it as an override and try a dictation to a conversation.
5. Copy it to both `app/companion/win/profiles/claude-desktop.json` and `profiles/claude-desktop.json` and publish: running installs pick it up on their next check.
