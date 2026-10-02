"""Msgids of the user-facing messages of `scripts/windows/voicemate-hotkeys.ps1`.

The Windows hotkeys script runs under PowerShell, outside this package, so
`pybabel extract` (which only scans `app/`, see `babel.cfg`) would never see
its strings. Listing them here with the `N_()` marker (one of Babel's default
extraction keywords) puts them in `voicemate.pot` and in the `en`, `pt_BR`, `es`,
`ru` and `zh_CN` catalogs next to the daemon's messages, where they are translated like
any other msgid. At runtime the script reads the translations straight from
the `.po` files; the app never imports this module.

The msgids use .NET composite format placeholders (`{0}`, `{1}`), which the
script fills in with PowerShell's `-f` operator. `MESSAGES` must match the
literals the script passes to its `Get-LocalizedText` function exactly:
`tests/test_windows_script_i18n.py` fails when the two drift apart. After
changing either side, run `make i18n-extract i18n-update`, translate the new
entries in every catalog but `en`, then `make i18n-compile`.
"""

from __future__ import annotations


def N_(message: str) -> str:
    """Mark `message` for extraction without translating it (gettext's no-op marker)."""
    return message


MESSAGES: tuple[str, ...] = (
    N_("[VoiceMate] Windows audio device check unavailable: {0}"),
    N_("[VoiceMate] Connected to the daemon at {0}."),
    N_("[VoiceMate] Lost contact with the daemon at {0} (stopped or restarting?). Retrying every {1} s."),
    N_("connection refused"),
    N_("connection closed before the daemon answered"),
    N_("nothing is listening on {0}"),
    N_("[VoiceMate] Registered with the daemon as consumer {0}."),
    N_("[VoiceMate] Daemon restarted (instance {0} -> {1}): reading its results from the start."),
    N_("[VoiceMate] Registered with the daemon as consumer {0} (assigned on the trigger)."),
    N_("[VoiceMate] > recording... (op {0})"),
    N_("[VoiceMate] # transcribing... (op {0})"),
    N_("[VoiceMate] ~ restarted (op {0})"),
    N_("[VoiceMate] trigger '{0}' received."),
    N_("daemon offline"),
    N_("{0}: daemon offline at {1} ({2}). Start it in WSL with `make run`, then press the hotkey again."),
    N_("daemon busy"),
    N_(
        "{0}: the daemon did not answer within {1} s (busy). It is running, so it may still act on this key press: check the WSL console before pressing again."
    ),
    N_("daemon loading"),
    N_("{0}: the daemon is still loading the model, try again in a few seconds."),
    N_("daemon error"),
    N_("{0}: daemon error ({1}). See the WSL console for details."),
    N_("trigger rejected"),
    N_("{0}: the daemon rejected the trigger ({1})."),
    N_("trigger failed"),
    N_("{0}: unexpected error talking to the daemon at {1}: {2}"),
    N_("The daemon reported an error ({0})."),
    N_("[VoiceMate] {0} (seq {1}, op {2}, error '{3}'; clipboard untouched)"),
    N_("error"),
    N_("microphone unavailable"),
    N_("No microphone is connected to Windows. Connect one and press the hotkey again."),
    N_(
        "Windows has an active microphone, but WSL cannot open it: the WSLg audio bridge is stuck. Run `wsl --shutdown`, then start the daemon again."
    ),
    N_(
        "Could not query the Windows audio devices. Check that a microphone is connected; if it is, run `wsl --shutdown` and start the daemon again."
    ),
    N_(
        "[VoiceMate] Transcription could not be copied to the clipboard (seq {0}, op {1}) after {2} tries. Full text below, copy it manually:"
    ),
    N_("----- VoiceMate transcription (seq {0}) -----"),
    N_("----- end -----"),
    N_("Transcription could not be copied to the clipboard"),
    N_('"{0}" (the full text is in the VoiceMate console window)'),
    N_('[VoiceMate] clipboard confirmed (seq {0}, op {1}, retry {2}): "{3}"'),
    N_('[VoiceMate] clipboard confirmed (seq {0}, op {1}): "{2}"'),
    N_('[VoiceMate] could not confirm the clipboard (seq {0}): "{1}"; retrying.'),
    N_(
        "WSL audio is stuck: the daemon cannot reach the WSLg PulseAudio server, so recording will fail. Run `wsl --shutdown`, then start the daemon again."
    ),
    N_("Windows has no microphone connected either: connect one first."),
    N_("WSL audio is down"),
    N_("[VoiceMate] WSL audio is working again."),
    N_("[VoiceMate] Unexpected error in the main loop (continuing): {0}"),
    N_("[VoiceMate] Hotkeys active: {0} (clipboard) / {1} (Claude) -> {2}"),
    N_("(unregistered, retrying every {0} s)"),
    N_("[VoiceMate] Consumer {0} | scope={1}. Clipboard set by Windows. Leave this window open."),
    N_(
        "[VoiceMate] Daemon not reachable at {0} yet: start it in WSL (`make run`). The hotkeys stay active and this window connects as soon as it is up."
    ),
    N_("[VoiceMate] Windows has no active microphone: recording will fail until you connect one."),
    N_("{0} is already registered by another app."),
)
