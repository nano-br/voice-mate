"""Parsers puros do doctor (sem subprocess real)."""

from __future__ import annotations

from app.setup.doctor import _parse_vulkan_devices

# Saída real do `vulkaninfo --summary` dentro do WSL2 (só llvmpipe = CPU).
_WSL2_SUMMARY = """
Devices:
========
GPU0:
\tapiVersion         = 1.4.318
\tdriverVersion      = 25.2.8
\tdeviceName         = llvmpipe (LLVM 20.1.2, 256 bits)
\tdriverID           = DRIVER_ID_MESA_LLVMPIPE
\tdeviceType         = PHYSICAL_DEVICE_TYPE_CPU
"""

_NATIVE_SUMMARY = """
Devices:
========
GPU0:
\tdeviceName         = AMD Radeon RX 9070 XT (RADV GFX1201)
\tdeviceType         = PHYSICAL_DEVICE_TYPE_DISCRETE_GPU
"""


def test_parse_vulkan_devices_wsl2_llvmpipe() -> None:
    devices = _parse_vulkan_devices(_WSL2_SUMMARY)
    assert devices == [("llvmpipe (LLVM 20.1.2, 256 bits)", "PHYSICAL_DEVICE_TYPE_CPU")]


def test_parse_vulkan_devices_native_gpu() -> None:
    devices = _parse_vulkan_devices(_NATIVE_SUMMARY)
    assert devices == [("AMD Radeon RX 9070 XT (RADV GFX1201)", "PHYSICAL_DEVICE_TYPE_DISCRETE_GPU")]


def test_parse_vulkan_devices_empty() -> None:
    assert _parse_vulkan_devices("no devices here") == []
