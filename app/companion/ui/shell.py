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

    def settings(self) -> CompanionSettings:
        """The applied settings (cached; refreshed after every successful apply)."""

    def settings_applied(self, settings: CompanionSettings) -> None: ...

    def show_status(self) -> None: ...

    def show_settings(self) -> None: ...

    def confirm_wsl_restart(self) -> None: ...

    def set_muted(self, muted: bool) -> None: ...

    def copy_result(self, item: RecentItem) -> None: ...

    def quit_app(self) -> None: ...
