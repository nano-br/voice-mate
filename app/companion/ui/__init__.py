"""Qt (PySide6) user interface of the companion: tray, status window, settings window.

Renders `CompanionSnapshot`s and calls `CompanionController` commands only
(app/companion/contract.py). Everything that imports PySide6 lives here or in
`app/companion/main.py` (tests/test_import_boundary.py).
"""
