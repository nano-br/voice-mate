"""Fixtures for the Qt UI tests: offscreen QApplication, English texts, a FakeController.

PySide6 is optional (the engine's Linux venv does not have it): every test module
starts with `pytest.importorskip("PySide6")`, and this file imports Qt lazily.
"""

from __future__ import annotations

import gettext
import os
import time
from collections.abc import Callable, Iterator
from typing import TYPE_CHECKING

import pytest

import app.i18n as i18n_module

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

if TYPE_CHECKING:
    from PySide6.QtWidgets import QApplication

    from app.companion.ui.app import CompanionUi
    from app.companion.ui.demo_controller import FakeController


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    widgets = pytest.importorskip("PySide6.QtWidgets")
    app: QApplication = widgets.QApplication.instance() or widgets.QApplication([])
    app.setQuitOnLastWindowClosed(False)
    return app


@pytest.fixture(autouse=True)
def english_texts() -> Iterator[None]:
    """Assertions use the English msgids: no catalog loaded."""
    original = (i18n_module._translation, i18n_module._active_language)
    i18n_module._translation = gettext.NullTranslations()
    i18n_module._active_language = "en"
    yield
    i18n_module._translation, i18n_module._active_language = original


@pytest.fixture
def process_events(qapp: QApplication) -> Callable[..., bool]:
    """process_events(predicate=None, timeout=2.0): run the Qt loop until `predicate()` holds."""

    def run(predicate: Callable[[], bool] | None = None, timeout: float = 2.0) -> bool:
        # Without a predicate: just let queued signals and short timers run.
        condition = predicate or (lambda: False)
        deadline = time.monotonic() + (timeout if predicate else 0.05)
        while True:
            qapp.processEvents()
            if condition():
                return True
            if time.monotonic() > deadline:
                return predicate is None
            time.sleep(0.005)

    return run


@pytest.fixture
def fake() -> FakeController:
    from app.companion.ui.demo_controller import FakeController

    return FakeController()


@pytest.fixture
def exits() -> list[int]:
    return []


@pytest.fixture
def relaunches() -> list[int]:
    """One entry per new instance the UI asked for (a restart); never a real process."""
    return []


# A deterministic `catalog_for`: "auto" reads as an English Windows, every catalog exists.
TEST_CATALOGS: dict[str, str] = {
    "auto": "en",
    "pt-BR": "pt_BR",
    "en": "en",
    "es": "es",
    "ru": "ru",
    "zh-CN": "zh_CN",
}


@pytest.fixture
def ui(
    qapp: QApplication,
    fake: FakeController,
    exits: list[int],
    relaunches: list[int],
    process_events: Callable[..., bool],
) -> Iterator[CompanionUi]:
    """A CompanionUi with a tray (not shown), attached and started, rendered. Its UI runs
    in English; a restart only records itself in `relaunches`."""
    from app.companion.ui.app import CompanionUi

    companion = CompanionUi(
        fake,
        tray_available=True,
        exit_app=lambda: exits.append(0),
        relaunch=lambda: relaunches.append(0),
        language_catalog=TEST_CATALOGS.__getitem__,
        running_catalog="en",
    )
    companion.bridge.attach()
    fake.start()
    process_events()
    yield companion
    companion.bridge.detach()
    if companion.settings_dialog is not None:
        companion.settings_dialog.hide()
    for attribute in ("_wsl_box", "_restart_wsl_box", "_language_box", "_clear_pending_box"):
        box = getattr(companion, attribute)
        if box is not None:
            setattr(companion, attribute, None)
            box.close()
            box.deleteLater()
    companion.status_window.hide()
    if companion.tray is not None:
        companion.tray.hide()
    companion.deleteLater()
    process_events()
