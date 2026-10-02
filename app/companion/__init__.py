"""VoiceMate companion: tray app that supervises the engine and owns the desktop side.

Must stay importable without the engine's dependencies (numpy, sounddevice,
torch, app.core): it ships alone on Windows. tests/test_import_boundary.py
enforces it. See docs/companion-app.md.
"""
