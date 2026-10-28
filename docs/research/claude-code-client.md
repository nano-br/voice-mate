# Research: VoiceMate as a Claude Code client

Purpose: lab notes and measurements on turning VoiceMate into a third Claude Code client (next to the Claude Desktop app and the phone), with a recommended architecture. Research only: nothing here is wired into the production flow yet. Back to the [README](../../README.md).

Lab date: 2026-10-28. Versions seen: Claude Desktop 4.29.0 (its bundled CLI 2.1.309), `claude-agent-sdk` 0.2.174 (bundled CLI 2.1.310), `claude` on the Windows `PATH` 2.1.143. Prototypes live in [`tools/lab/claude_client/`](../../tools/lab/claude_client/).

## 1. Questions and short answers

| Question | Answer | Evidence |
|---|---|---|
| Does `ClaudeSDKClient` keep one process alive across turns? | Yes. The CLI child keeps the same PID for every turn until `disconnect()`. | §3, same PID on every turn |
| How much does a persistent process save? | The spawn and init cost of every message (measured 0.6 to 2.2 s without any API call). Model time is the same. | §3 |
| Do Desktop Code tab sessions use `~/.claude/projects/*.jsonl`? | Yes, the same files (`entrypoint: "claude-desktop"`). | §4 |
| Does an SDK session show up in the Desktop sidebar? | No. The sidebar reads the Desktop's own index, not the projects folder. | §5 |
| What puts a session on the phone? | Remote Control, which the Desktop turns on per running session (`bridge-session` lines in the transcript). The CLI has `--remote-control` too. | §6 |
| Can two writers share a session? | See §7. | §7 |
| Can the reader stay off the SDK/CLI? | Yes. Transcripts plus `~/.claude/sessions/<pid>.json` give messages, turn ends and busy/idle. Works from WSL through `/mnt/c`. | §4, §8 |
| Windows or WSL for the persistent core? | Windows (companion side). | §9 |

## 2. Authentication is the first blocker

The Desktop app authenticates its own sessions: it starts the CLI with `CLAUDE_CODE_SDK_HAS_HOST_AUTH_REFRESH=1` and hands tokens over the SDK control channel. Nothing is stored for other clients. On this machine:

- `claude auth status` (Windows, outside the Desktop environment) reports `loggedIn: false`; `~/.claude/.credentials.json` holds empty tokens.
- The WSL distro has its own `~/.claude` with a token that expired in July, and no `claude` on `PATH`. The engine venv in WSL does not have `claude-agent-sdk` installed.

So a VoiceMate client needs its own login: `claude auth login` once on the side that runs it, or `claude setup-token` for a long-lived token passed as `CLAUDE_CODE_OAUTH_TOKEN`.

**Pitfall found in the lab.** A CLI started from inside a Desktop session inherits the Desktop host variables (`CLAUDECODE`, `CLAUDE_CODE_ENTRYPOINT`, `CLAUDE_CODE_SDK_HAS_HOST_AUTH_REFRESH`, `CLAUDE_CODE_MESSAGING_*`, ...). The first lab run did exactly that and the child rewrote `~/.claude/.credentials.json` with empty tokens (the file's mtime matches the first lab session to the second). The prototypes now drop those variables (`scrub_host_env()` in `persistent_client.py`). Production code that may be started from a Claude session (tests, dev runs) must do the same.

## 3. Latency: persistent process vs one process per message

Strategies measured by [`bench_latency.py`](../../tools/lab/claude_client/bench_latency.py), model `claude-haiku-5-5`, no tools, one short prompt per turn:

- **sdk-persistent**: one `ClaudeSDKClient`, N turns over the same process (what `app/features/claude/runtime.py` already does).
- **sdk-query**: `claude_agent_sdk.query()` per message with `resume`, a new process each time.
- **cli-resume**: `claude -p --resume <id>` per message.
- **cli-stream**: one `claude -p --input-format stream-json --output-format stream-json` process fed turn by turn on stdin.

Process overhead only (runs without a valid login return a local error, so this is spawn + init, no API time):

| Strategy | First turn | Next turns | PIDs over 4 turns |
|---|---|---|---|
| sdk-persistent | 0.63 to 2.15 s (connect) | 0.04 to 0.06 s | 1 |
| cli-stream | 0.98 s (spawn) | 0.02 s | 1 |

<!-- MEASUREMENTS-WITH-LOGIN -->
Full measurements with a logged-in CLI (time to first token and to end of turn): pending the login in §2.

## 4. Transcript format (`~/.claude/projects/<slug>/<session-id>.jsonl`)

**Folder slug.** The working directory with every non-alphanumeric character replaced by `-` (`C:\Users\allit\workspace\voice-mate` gives `C--Users-allit-workspace-voice-mate`). Paths longer than 200 characters are cut and get a base36 hash suffix (a port of the CLI hash is in `session_watch.py`). The drive letter keeps the case the client saw: both `C--Users-...` and `c--Users-...` exist side by side, so look the folder up case-insensitively. `CLAUDE_CONFIG_DIR` moves the whole `~/.claude`.

**One JSON object per line, append-only.** Line types seen across 767 local transcripts (1.5 GB):

| `type` | What it is | Reader use |
|---|---|---|
| `user` | Prompt typed by the person, or a `tool_result` block list | New prompt when `content` is text and `isMeta` is not set |
| `assistant` | One content block of an API reply (`text`, `thinking`, `tool_use`). Lines of the same reply share `message.id`. | Assistant text; `message.stop_reason == "end_turn"` closes the turn |
| `system` | `stop_hook_summary` (after a turn), `api_error`, `compact_boundary`, `informational` | Turn end, errors, compaction |
| `queue-operation` | `enqueue`/`dequeue`/`remove` of a prompt typed while the session was busy | Input from another client (Desktop, phone) |
| `custom-title`, `ai-title`, `agent-name` | Session title, rewritten every turn | Display name |
| `bridge-session` | Remote Control binding (`bridgeSessionId`) | Session is on the phone |
| `attachment`, `api-request*`, `file-history-*`, `last-prompt`, `mode`, `cost-state`, `pr-link`, `frame-link`, `atis-latch` | Client internals | Ignore |

Common fields on message lines: `uuid`, `parentUuid` (the conversation is a tree, resumes and forks branch it), `sessionId`, `timestamp` (UTC), `cwd`, `gitBranch`, `version`, `entrypoint` (`claude-desktop`, `sdk-py`, `sdk-cli`, `cli`), `isSidechain` (subagent traffic).

**Turn end.** Three signals, from cheapest to most reliable:

1. An `assistant` line with `stop_reason: "end_turn"` (9,256 of 92,427 assistant lines; `tool_use` means the turn continues).
2. A `system`/`stop_hook_summary` line, written after the turn when hooks are configured.
3. The live registry `~/.claude/sessions/<pid>.json` (one file per running client) switching `status` from `busy` to `idle`. It also carries `sessionId`, `cwd`, `name`, `entrypoint`, `bridgeSessionId` and the Desktop messaging pipe.

**Compaction.** A `system`/`compact_boundary` line with `parentUuid: null` and `logicalParentUuid`, followed by a `user` line with `isCompactSummary: true`. A reader that shows history should restart from the boundary.

**Latest assistant reply.** Walk back from the end to the last `assistant` line with `stop_reason: "end_turn"`, then join the `text` blocks of the lines that share its `message.id`.

## 5. Where the Desktop keeps its sessions

The Desktop is an MSIX app, but its user data moved out of the package container: `%LOCALAPPDATA%\Packages\Claude_pzs8sxrjxfjjc\LocalCache\Roaming\Claude` holds only a `.user-data-left` marker, and the live data is in `%LOCALAPPDATA%\Claude-Data\claude_pzs8sxrjxfjjc\`.

- `claude-code-sessions\<account>\<org>\local_<uuid>.json`: one file per sidebar entry. It maps the Desktop id (`local_...`) to `cliSessionId` (the `.jsonl` name) and stores title, cwd, worktree, branch, model, effort, permission mode, archive flag and `bridgeSessionIds`.
- `claude-code\<version>\`: the CLI builds the Desktop runs.
- The transcript itself is the normal `~/.claude/projects/...jsonl`.

An SDK session has no `local_*.json`, so it never shows in the sidebar (checked: the four lab sessions are missing from the Desktop's session list). Writing that index file by hand would be an undocumented, fragile hack. The Desktop opens a known session from a deep link: `claude://claude.ai/epitaxy/local_<uuid>`.

## 6. Phone and Remote Control

A session reaches the phone (and claude.ai/code) through Remote Control. The Desktop enables it per running session and the transcript records a `bridge-session` line with the `bridgeSessionId`; the session stays on the phone while its process runs. The CLI can do the same for a session it owns: `claude --remote-control [name]` (interactive mode). The SDK has no documented Remote Control switch.

## 7. Resume, fork and two writers

<!-- RESUME-RESULTS -->
Pending the login in §2. Options available in the SDK: `resume=<id>`, `continue_conversation=True`, `fork_session=True` (with resume: new session id, history copied), `session_id=<uuid>` (choose the id of a new session) and `resume_session_at=<message uuid>`. The CLI help documents one guard for background sessions: `claude --bg --resume <id>` "starts a copy and says so when the session is already running".

## 8. Disk reader prototype

[`session_watch.py`](../../tools/lab/claude_client/session_watch.py), standard library only:

```bash
python tools/lab/claude_client/session_watch.py list
python tools/lab/claude_client/session_watch.py follow <session-id>
```

It keeps a byte offset per file, holds back an unfinished last line until its newline arrives, maps lines to events (`user`, `assistant_text`, `tool_use`, `turn_end`, `compact`, `queued`, `error`, `title`) and polls `~/.claude/sessions/*.json` for busy/idle. Polling every 0.25 s was chosen over file-system events because it works the same on NTFS, on `/mnt/c` from WSL (no inotify over 9p) and on `\\wsl.localhost` from Windows. From WSL with `CLAUDE_CONFIG_DIR=/mnt/c/Users/<user>/.claude`, one `stat` costs about 1.8 ms and following a session works unchanged; listing all 767 transcripts takes 1.7 s, so the list should be cached.

## 9. Windows or WSL for the core

- The Desktop, its sessions, `~/.claude` and the projects the person works on are on Windows. A core in WSL would need its own login, would see Windows projects only as `/mnt/c/...` (a different slug, `-mnt-c-...`, so a different session folder) and would cross the 9p bridge for every read.
- The engine in WSL only needs text in and text out for voice chat; it already talks HTTP to the companion.

Recommendation: the persistent Claude core runs on the Windows companion side, with the Windows `claude` login; the WSL engine asks the companion over the existing local HTTP channel. If it ever has to run in WSL, set `CLAUDE_CONFIG_DIR` to the Windows `~/.claude` only for reading, never for a second writer.

## 10. Recommended architecture

<!-- ARCHITECTURE -->
Pending the measurements in §3 and §7.

## 11. Risks

- **Undocumented formats.** The `.jsonl` line types, `~/.claude/sessions/<pid>.json` and the Desktop index are internal. New line types appear often (this lab saw 22). Parse defensively: ignore unknown types, never fail on a missing field, pin the tested CLI version in a test fixture.
- **Auth coupling.** The Desktop's host auth is not reusable. VoiceMate needs its own login and must scrub the Desktop variables when started from a Claude session.
- **Version drift.** Three CLI versions coexist on this machine (2.1.143 on `PATH`, 2.1.309 in the Desktop, 2.1.310 in the SDK). Use the SDK's bundled CLI and record its version.
- **Clock skew.** WSL and Windows clocks differed by about 20 s during the lab; compare transcript `timestamp` values, not file mtimes across the boundary.
