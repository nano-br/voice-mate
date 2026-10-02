"""What the views (tray, status window, settings window) may ask of the app shell."""

from __future__ import annotations

from typing import Protocol

from app.companion.contract import CompanionController, CompanionSettings, RecentItem
from app.companion.ui.bridge import ControllerBridge


class Shell(Protocol):
    """Implemented by `CompanionUi` (ui/app.py); the views never talk to each other directly."""

    @property
    def controller(self) -> CompanionController: ...

    @property
    def bridge(self) -> ControllerBridge: ...

    @property
    def has_tray(self) -> bool: ...

    @property
    def quitting(self) -> bool:
        """Quit has started: no window may pop up any more."""

    def settings(self) -> CompanionSettings:
        """`controller.settings()`, read live every time: the UI never keeps a copy."""

    def settings_changed(self) -> None:
        """Settings were applied: re-render what depends on them."""

    def show_status(self) -> None: ...

    def show_settings(self) -> None: ...

    def confirm_wsl_restart(self) -> None:
        """Answer the controller's pending question (policy "ask")."""

    def confirm_restart_wsl(self) -> None:
        """A manual "Restart WSL" (menu, jump list): confirm first, it stops every distro."""

    def set_muted(self, muted: bool) -> None: ...

    def copy_result(self, item: RecentItem) -> None: ...

    def confirm_clear_pending(self) -> None:
        """Ask before emptying the Not copied list: its texts cannot be copied afterwards."""

    def quit_app(self) -> None: ...
