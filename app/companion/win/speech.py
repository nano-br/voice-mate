"""Short fixed lines VoiceMate says itself ("The conversation X has an answer."), Windows only.

The engine's text to speech belongs to its claude_chat flow and has no "say this" request, so the
companion speaks these few lines with the Windows speech synthesizer (System.Speech, through a
hidden PowerShell). The text and the language reach the script through environment variables,
never through the command line, so nothing in them is ever parsed as code. One line at a time,
on a daemon thread; anything that fails is logged and dropped. Standard library only.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
from typing import Final

log = logging.getLogger(__name__)

SAY_TIMEOUT_S: Final = 30.0
TEXT_ENV: Final = "VOICEMATE_SAY_TEXT"
LANGUAGE_ENV: Final = "VOICEMATE_SAY_LANGUAGE"
# A voice of the language when one is installed, the default voice otherwise.
_SCRIPT: Final = (
    "Add-Type -AssemblyName System.Speech;"
    "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer;"
    "try { $s.SelectVoiceByHints(0, 0, 0, [System.Globalization.CultureInfo]::new($env:"
    + LANGUAGE_ENV
    + ")) } catch {};"
    "$s.Speak($env:" + TEXT_ENV + ")"
)
_CREATE_NO_WINDOW: Final = 0x08000000

_lock = threading.Lock()


def _run(text: str, language: str) -> None:
    env = dict(os.environ)
    env[TEXT_ENV] = text
    env[LANGUAGE_ENV] = language or "en-US"
    with _lock:
        try:
            subprocess.run(
                ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", _SCRIPT],
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=SAY_TIMEOUT_S,
                creationflags=_CREATE_NO_WINDOW,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            log.info("speech: could not say a line (%s)", exc)


def say(text: str, language: str) -> None:
    """Speak `text` in the background (BCP 47 `language`, e.g. "pt-BR"). No-op off Windows."""
    text = text.strip()
    if not text:
        return
    if sys.platform != "win32":
        log.info("speech: not available on this system: %s", text)
        return
    threading.Thread(target=_run, args=(text, language), name="voicemate-say", daemon=True).start()
