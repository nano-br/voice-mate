"""The CT2-ROCm build on a fresh machine: the ROCm clang, the ROCm 7 source fixes, a stale CMake
cache and the python wrapper install (app/setup/ct2_rocm.py). No compiler runs here."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from app.setup import ct2_rocm

_UTILS = """#pragma once
#include <thrust/execution_policy.h>
namespace ctranslate2 {}
"""
_PRIMITIVES = """#define cublasGemmEx hipblasGemmEx_v2
#define cublasGemmStridedBatchedEx hipblasGemmStridedBatchedEx_v2
"""


def _source(root: Path) -> Path:
    cuda = root / "src" / "cuda"
    cuda.mkdir(parents=True)
    (cuda / "utils.h").write_text(_UTILS, encoding="utf-8")
    (cuda / "primitives.cu").write_text(_PRIMITIVES, encoding="utf-8")
    return root


def test_rocm7_gets_the_thrust_headers_and_the_plain_hipblas_names_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = _source(tmp_path)
    monkeypatch.setattr(ct2_rocm, "_hipblas_major", lambda: 3)
    ct2_rocm._patch_for_rocm7(src)
    ct2_rocm._patch_for_rocm7(src)  # `make configure` again: nothing doubles
    utils = (src / "src" / "cuda" / "utils.h").read_text(encoding="utf-8")
    assert utils.count("#include <thrust/iterator/counting_iterator.h>") == 1
    assert utils.count("#include <thrust/reduce.h>") == 1
    primitives = (src / "src" / "cuda" / "primitives.cu").read_text(encoding="utf-8")
    assert "_v2" not in primitives
    assert "#define cublasGemmEx hipblasGemmEx\n" in primitives


def test_rocm6_keeps_the_v2_hipblas_names(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    src = _source(tmp_path)
    monkeypatch.setattr(ct2_rocm, "_hipblas_major", lambda: 2)
    ct2_rocm._patch_for_rocm7(src)
    assert (src / "src" / "cuda" / "primitives.cu").read_text(encoding="utf-8") == _PRIMITIVES


def test_the_hipblas_version_comes_from_its_header(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    header = tmp_path / "include" / "hipblas" / "hipblas-version.h"
    header.parent.mkdir(parents=True)
    header.write_text("#define hipblasVersionMajor 3\n#define hipblasVersionMinor 2\n", encoding="utf-8")

    def hipconfig(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, 0, stdout=f"{tmp_path}\n")

    monkeypatch.setattr(ct2_rocm.subprocess, "run", hipconfig)
    assert ct2_rocm._hipblas_major() == 3


def test_the_rocm_clang_builds_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # With gcc as CXX, CMake stops at "language hip not recognized".
    llvm = tmp_path / "llvm" / "bin"
    llvm.mkdir(parents=True)
    (llvm / "clang++").write_text("", encoding="utf-8")
    answers = {"-l": f"{llvm}\n", "-R": "/opt/rocm-7.2.0\n"}

    def hipconfig(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, 0, stdout=answers[cmd[1]])

    monkeypatch.setattr(ct2_rocm.subprocess, "run", hipconfig)
    assert ct2_rocm._rocm_compilers() == {
        "CXX": str(llvm / "clang++"),
        "HIPCXX": str(llvm / "clang"),
        "HIP_PATH": "/opt/rocm-7.2.0",
    }


def test_without_hipconfig_nothing_is_forced(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError(cmd[0])

    monkeypatch.setattr(ct2_rocm.subprocess, "run", missing)
    assert ct2_rocm._rocm_compilers() == {}


def test_a_build_dir_configured_with_another_compiler_starts_over(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    build = tmp_path / "build"
    build.mkdir()
    (build / "CMakeCache.txt").write_text("CMAKE_CXX_COMPILER:FILEPATH=/usr/bin/c++\n", encoding="utf-8")
    monkeypatch.setattr(ct2_rocm, "_BUILD_DIR", build)
    ct2_rocm._drop_stale_cache("/opt/rocm/lib/llvm/bin/clang++")
    assert not build.exists()


def test_a_build_dir_with_the_same_compiler_is_kept(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    build = tmp_path / "build"
    build.mkdir()
    cxx = "/opt/rocm/lib/llvm/bin/clang++"
    (build / "CMakeCache.txt").write_text(f"CMAKE_CXX_COMPILER:FILEPATH={cxx}\n", encoding="utf-8")
    monkeypatch.setattr(ct2_rocm, "_BUILD_DIR", build)
    ct2_rocm._drop_stale_cache(cxx)
    assert build.exists()  # an incremental rebuild


def test_the_lib_finds_the_rocm_openmp_runtime_and_the_wrapper_keeps_the_locked_numpy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = _source(tmp_path / "src")
    commands: list[tuple[list[str], dict[str, str] | None]] = []

    def run(cmd: list[str], desc: str, env: dict[str, str] | None = None) -> bool:
        commands.append((cmd, env))
        return True

    monkeypatch.setattr(ct2_rocm, "_SRC_DIR", src)
    monkeypatch.setattr(ct2_rocm, "_BUILD_DIR", src / "build")
    monkeypatch.setattr(ct2_rocm, "_run", run)
    monkeypatch.setattr(ct2_rocm, "_hipblas_major", lambda: 3)
    monkeypatch.setattr(ct2_rocm, "_rocm_compilers", lambda: {"CXX": "/opt/rocm/lib/llvm/bin/clang++"})
    monkeypatch.delenv("CXX", raising=False)
    assert ct2_rocm._build("gfx1201")
    configure = commands[0][0]
    rpath = next(arg for arg in configure if arg.startswith("-DCMAKE_INSTALL_RPATH="))
    assert rpath.endswith(";/opt/rocm/lib/llvm/lib")  # libomp.so lives there
    assert "-DCMAKE_HIP_ARCHITECTURES=gfx1201" in configure
    assert "-DWITH_CUDNN=ON" in configure  # MIOpen: without it Whisper's Conv1D fails on the GPU
    assert commands[0][1] is not None and commands[0][1]["CXX"] == "/opt/rocm/lib/llvm/bin/clang++"

    commands.clear()
    assert ct2_rocm._pip_install_wrapper()
    requirements, wrapper = (cmd for cmd, _env in commands)
    assert requirements[-2:] == ["-r", str(src / "python" / "install_requirements.txt")]  # pybind11 first
    assert "--no-build-isolation" in wrapper
    assert "--no-deps" in wrapper  # a forced reinstall would upgrade numpy past numba's range


def test_a_rocm_without_miopen_is_named_before_the_build(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ct2_rocm.shutil, "which", lambda exe: f"/usr/bin/{exe}")
    monkeypatch.setattr(ct2_rocm, "_rocm_root", lambda: tmp_path)
    assert ct2_rocm._missing_tools() == ["MIOpen (miopen-hip-dev)"]
    header = tmp_path / "include" / "miopen" / "miopen.h"
    header.parent.mkdir(parents=True)
    header.write_text("", encoding="utf-8")
    assert ct2_rocm._missing_tools() == []
