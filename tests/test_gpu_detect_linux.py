"""Parsers puros da detecção de GPU em Linux (rocm-smi/amd-smi/lspci/rocminfo)."""

from __future__ import annotations

from app.setup.gpu_detect import (
    _parse_amd_smi_asic,
    _parse_gfx_target,
    _parse_lspci_amd,
    _parse_rocm_smi_product,
)

_ROCM_SMI_OUT = """
============================ ROCm System Management Interface ============================
====================================== Product Info ======================================
GPU[0]\t\t: Card Series: \t\tAMD Radeon RX 9070 XT
GPU[0]\t\t: Card Model: \t\t0x7550
===========================================================================================
"""

_AMD_SMI_OUT = """
GPU: 0
    ASIC:
        MARKET_NAME: AMD Radeon RX 9070 XT
        VENDOR_ID: 0x1002
"""

_LSPCI_OUT = """
00:00.0 Host bridge: Advanced Micro Devices, Inc. [AMD] Device 14d8
03:00.0 VGA compatible controller: Advanced Micro Devices, Inc. [AMD/ATI] Navi 48 [Radeon RX 9070 XT]
04:00.0 Audio device: Advanced Micro Devices, Inc. [AMD/ATI] Device ab40
"""

_ROCMINFO_OUT = """
Agent 1
  Name:                    AMD Ryzen 7 9800X3D
  Device Type:             CPU
Agent 2
  Name:                    gfx1201
  Marketing Name:          AMD Radeon RX 9070 XT
  Device Type:             GPU
"""


def test_parse_rocm_smi_product() -> None:
    assert _parse_rocm_smi_product(_ROCM_SMI_OUT) == "AMD Radeon RX 9070 XT"


def test_parse_rocm_smi_product_empty() -> None:
    assert _parse_rocm_smi_product("") is None


def test_parse_amd_smi_asic() -> None:
    assert _parse_amd_smi_asic(_AMD_SMI_OUT) == "AMD Radeon RX 9070 XT"


def test_parse_lspci_amd() -> None:
    name = _parse_lspci_amd(_LSPCI_OUT)
    assert name is not None
    assert "RX 9070 XT" in name


def test_parse_lspci_ignores_non_gpu_amd_devices() -> None:
    out = "00:00.0 Host bridge: Advanced Micro Devices, Inc. [AMD] Device 14d8\n"
    assert _parse_lspci_amd(out) is None


def test_parse_lspci_ignores_other_vendors() -> None:
    out = "01:00.0 VGA compatible controller: NVIDIA Corporation AD104 [GeForce RTX 4070]\n"
    assert _parse_lspci_amd(out) is None


def test_parse_gfx_target() -> None:
    assert _parse_gfx_target(_ROCMINFO_OUT) == "gfx1201"


def test_parse_gfx_target_absent() -> None:
    assert _parse_gfx_target("Name: AMD Ryzen CPU\n") is None
