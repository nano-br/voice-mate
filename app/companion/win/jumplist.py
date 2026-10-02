"""Taskbar jump list tasks (right-click on the taskbar button or the pin).

Each task starts the companion again with `--command <x>`; that second process
finds the single-instance mutex taken and forwards the command to the running
one. The list is bound to the process AUMID, so `aumid.set_app_user_model_id`
must run first (`main.py` does it before creating the QApplication).
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.companion.contract import APP_USER_MODEL_ID, CompanionCommand

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class JumpTask:
    title: str  # localized
    command: CompanionCommand
    description: str = ""  # localized tooltip


@dataclass(frozen=True)
class LaunchTarget:
    """How to start this companion again: the frozen exe, or the dev interpreter."""

    executable: str
    arguments: tuple[str, ...]  # placed before "--command <x>"
    working_dir: str
    icon: str

    def arguments_for(self, command: CompanionCommand) -> str:
        return " ".join([*self.arguments, "--command", command])


def current_launch_target() -> LaunchTarget:
    """The frozen VoiceMate.exe, or in development `pythonw -m app.companion.main` from the repo."""
    executable = Path(sys.executable)
    if getattr(sys, "frozen", False):
        return LaunchTarget(str(executable), (), str(executable.parent), str(executable))
    # pythonw: no console window flashes when a task is clicked.
    windowed = executable.with_name("pythonw.exe")
    interpreter = windowed if windowed.is_file() else executable
    repo_root = Path(__file__).resolve().parents[3]
    return LaunchTarget(str(interpreter), ("-m", "app.companion.main"), str(repo_root), str(interpreter))


def set_jump_list_tasks(
    tasks: list[JumpTask], target: LaunchTarget | None = None, app_id: str = APP_USER_MODEL_ID
) -> bool:
    """Replace the jump list tasks of `app_id`; False (logged) on any COM failure.

    A failure is never fatal: the jump list is a convenience, the tray has every action.
    """
    if sys.platform != "win32":
        return False
    launch = target or current_launch_target()
    try:
        import pythoncom
        from win32com.propsys import propsys, pscon
        from win32com.shell import shell

        pythoncom.CoInitialize()
        destinations: Any = pythoncom.CoCreateInstance(
            shell.CLSID_DestinationList, None, pythoncom.CLSCTX_INPROC_SERVER, shell.IID_ICustomDestinationList
        )
        destinations.SetAppID(app_id)
        destinations.BeginList()
        collection: Any = pythoncom.CoCreateInstance(
            shell.CLSID_EnumerableObjectCollection, None, pythoncom.CLSCTX_INPROC_SERVER, shell.IID_IObjectCollection
        )
        for task in tasks:
            link: Any = pythoncom.CoCreateInstance(
                shell.CLSID_ShellLink, None, pythoncom.CLSCTX_INPROC_SERVER, shell.IID_IShellLink
            )
            link.SetPath(launch.executable)
            link.SetArguments(launch.arguments_for(task.command))
            link.SetWorkingDirectory(launch.working_dir)
            link.SetIconLocation(launch.icon, 0)
            if task.description:
                link.SetDescription(task.description)
            properties: Any = link.QueryInterface(propsys.IID_IPropertyStore)
            properties.SetValue(pscon.PKEY_Title, propsys.PROPVARIANTType(task.title, pythoncom.VT_LPWSTR))
            properties.Commit()
            collection.AddObject(link)
        destinations.AddUserTasks(collection)
        destinations.CommitList()
    except Exception:  # COM errors come in many shapes; the jump list is optional
        log.warning("could not update the taskbar jump list", exc_info=True)
        return False
    return True


def clear_jump_list(app_id: str = APP_USER_MODEL_ID) -> bool:
    """Remove every task of `app_id` (used by tests and the uninstall path)."""
    if sys.platform != "win32":
        return False
    try:
        import pythoncom
        from win32com.shell import shell

        pythoncom.CoInitialize()
        destinations: Any = pythoncom.CoCreateInstance(
            shell.CLSID_DestinationList, None, pythoncom.CLSCTX_INPROC_SERVER, shell.IID_ICustomDestinationList
        )
        destinations.DeleteList(app_id)
    except Exception:  # see set_jump_list_tasks
        log.warning("could not clear the taskbar jump list", exc_info=True)
        return False
    return True
