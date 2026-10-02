"""Setup-time helpers: GPU detection, persisted config and the interactive
GPU bootstrap installer (`python -m app.setup.gpu_bootstrap`).

These modules run BOTH at install time (from `make setup`) and at app start
(to read the persisted choice), so keep them dependency-light: only the Python
stdlib here, never `torch`/`voxcpm`/etc.
"""
