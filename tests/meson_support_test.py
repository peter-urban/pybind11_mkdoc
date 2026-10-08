import json
import os
from pathlib import Path

from pybind11_mkdoc.meson_support import (
    configure_libclang_env,
    include_args_from_compile_commands,
)


def _write_compile_commands(
    build_dir: Path, entries: list[dict[str, str]]
) -> Path:
    path = build_dir / "compile_commands.json"
    path.write_text(json.dumps(entries), encoding="utf-8")
    return path


def test_include_args_extracts_and_resolves(tmp_path: Path) -> None:
    build = tmp_path / "build"
    src = tmp_path / "src"
    mod = src / "mod"
    third = tmp_path / "third"
    for directory in (build, mod, third):
        directory.mkdir(parents=True)

    _write_compile_commands(
        build,
        [
            {
                "directory": str(build),
                "file": "../src/mod/a.cpp",
                "command": (
                    "c++ -I../src -isystem../third -DFOO=1 "
                    "-std=c++20 -O2 -Wall -c ../src/mod/a.cpp"
                ),
            }
        ],
    )

    flags = include_args_from_compile_commands(str(build), target_filter=str(mod))

    assert "-I" + os.path.normpath(str(src)) in flags
    # -isystem must be a single joined token, never split into two arguments.
    assert "-isystem" + os.path.normpath(str(third)) in flags
    assert "-isystem" not in flags
    assert "-DFOO=1" in flags
    assert "-std=c++20" in flags
    # Non parse-relevant flags are dropped.
    assert "-O2" not in flags
    assert "-Wall" not in flags


def test_include_args_dedupes_and_keeps_first_std(tmp_path: Path) -> None:
    build = tmp_path / "build"
    src = tmp_path / "src"
    src.mkdir(parents=True)
    build.mkdir()

    _write_compile_commands(
        build,
        [
            {
                "directory": str(build),
                "file": "../src/a.cpp",
                "command": "c++ -I../src -std=c++20 -c ../src/a.cpp",
            },
            {
                "directory": str(build),
                "file": "../src/b.cpp",
                "command": "c++ -I../src -std=c++26 -c ../src/b.cpp",
            },
        ],
    )

    flags = include_args_from_compile_commands(str(build))

    assert flags.count("-I" + os.path.normpath(str(src))) == 1
    assert "-std=c++20" in flags
    assert "-std=c++26" not in flags


def test_include_args_walks_up_for_header_only_module(tmp_path: Path) -> None:
    # A header-only module has no compiled sources under its own directory; the
    # flags should come from a sibling (the binding sources under src/).
    build = tmp_path / "build"
    src = tmp_path / "src"
    header_only = src / "headers"
    bindings = src / "bindings"
    inc = tmp_path / "inc"
    for directory in (build, header_only, bindings, inc):
        directory.mkdir(parents=True)

    _write_compile_commands(
        build,
        [
            {
                "directory": str(build),
                "file": "../src/bindings/m.cpp",
                "command": "c++ -I../inc -std=c++23 -c ../src/bindings/m.cpp",
            }
        ],
    )

    flags = include_args_from_compile_commands(
        str(build), target_filter=str(header_only)
    )

    assert "-I" + os.path.normpath(str(inc)) in flags
    assert "-std=c++23" in flags


def test_configure_libclang_env(tmp_path: Path) -> None:
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib" / "libclang.so").write_text("", encoding="utf-8")
    resource = tmp_path / "lib" / "clang" / "23" / "include"
    resource.mkdir(parents=True)

    env_before = dict(os.environ)
    try:
        os.environ.pop("LIBCLANG_PATH", None)
        os.environ.pop("CLANG_INCLUDE_DIR", None)
        configure_libclang_env(str(tmp_path))
        assert os.environ["LIBCLANG_PATH"] == str(tmp_path / "lib" / "libclang.so")
        assert os.environ["CLANG_INCLUDE_DIR"] == str(resource)

        # Without override an existing value wins.
        os.environ["LIBCLANG_PATH"] = "/custom/libclang.so"
        configure_libclang_env(str(tmp_path))
        assert os.environ["LIBCLANG_PATH"] == "/custom/libclang.so"

        # With override the environment prefix wins again.
        configure_libclang_env(str(tmp_path), override=True)
        assert os.environ["LIBCLANG_PATH"] == str(tmp_path / "lib" / "libclang.so")
    finally:
        os.environ.clear()
        os.environ.update(env_before)
