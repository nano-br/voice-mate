"""Windows-only helpers used by main.py: AUMID, single-instance mutex, jump list."""

from __future__ import annotations

import sys
import uuid

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows-only modules")


def test_named_mutex_admits_one_holder() -> None:
    from app.companion.win.single_instance import NamedMutex

    name = f"Local\\VoiceMate.Companion.Test-{uuid.uuid4().hex}"
    first, second = NamedMutex(name), NamedMutex(name)
    assert first.acquire()
    assert first.acquire()  # idempotent for the holder
    assert not second.acquire()
    assert not second.held
    first.release()
    assert second.acquire()
    second.release()


def test_jump_list_tasks_start_this_app_with_a_command() -> None:
    from app.companion.win.jumplist import current_launch_target

    target = current_launch_target()
    assert target.arguments_for("settings").endswith("--command settings")
    if not getattr(sys, "frozen", False):
        assert target.arguments == ("-m", "app.companion.main")
        assert target.executable.lower().endswith(("pythonw.exe", "python.exe"))


def test_jump_list_can_be_written_and_cleared() -> None:
    from app.companion.win.jumplist import JumpTask, clear_jump_list, set_jump_list_tasks

    app_id = f"VoiceMate.Companion.Test.{uuid.uuid4().hex[:8]}"
    assert set_jump_list_tasks([JumpTask("Settings", "settings"), JumpTask("Quit VoiceMate", "quit")], app_id=app_id)
    assert clear_jump_list(app_id)
