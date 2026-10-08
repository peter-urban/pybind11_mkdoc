"""
Helpers for driving :mod:`pybind11_mkdoc` from a Meson build.

Meson has no public API to enumerate the include directories of a
``dependency`` object, so the robust way to feed the correct compiler
include/define/standard flags to ``pybind11-mkdoc`` is to read them back out
of the ``compile_commands.json`` that Meson generates for the Ninja backend.

This module provides two small, dependency-free helpers:

* :func:`configure_libclang_env` makes libclang discovery work inside a conda
  or virtual environment by pointing ``LIBCLANG_PATH`` and ``CLANG_INCLUDE_DIR``
  at the interpreter's own prefix. This works with both this fork and the
  upstream ``pybind11_mkdoc``, which honour those variables on Linux.

* :func:`include_args_from_compile_commands` extracts the ``-I``/``-isystem``/
  ``-D``/``-std=`` flags for a target from a ``compile_commands.json``.
"""

from __future__ import annotations

import glob
import json
import os
import shlex
import sys

__all__ = [
    "configure_libclang_env",
    "include_args_from_compile_commands",
]

# Flags that are relevant for parsing headers. Everything else from the build
# (optimisation, warning, codegen and machine flags) is intentionally dropped
# because it does not influence the AST that pybind11-mkdoc walks and may upset
# libclang.
_FLAG_PREFIXES = ("-I", "-D", "-std=", "-stdlib=")


def configure_libclang_env(prefix: str | None = None, *, override: bool = False) -> None:
    """
    Point libclang at the headers shipped inside a Python environment.

    When ``pybind11_mkdoc`` runs from a conda/mamba environment (or any venv
    that bundles ``libclang`` and the Clang resource headers), libclang's
    automatic discovery may pick an incompatible system ``libclang`` or fail to
    locate ``stddef.h`` and the intrinsic headers. Setting ``LIBCLANG_PATH`` and
    ``CLANG_INCLUDE_DIR`` from the active interpreter's prefix avoids both
    problems.

    Parameters
    ----------
    prefix:
        Environment prefix to search. Defaults to :data:`sys.prefix`.
    override:
        Overwrite variables that are already set. Defaults to ``False`` so that
        an explicit user setting always wins.
    """

    prefix = prefix or sys.prefix

    if override or "LIBCLANG_PATH" not in os.environ:
        for name in ("libclang.so", "libclang.so.1", "libclang.dylib", "libclang.dll"):
            candidate = os.path.join(prefix, "lib", name)
            if os.path.isfile(candidate):
                os.environ["LIBCLANG_PATH"] = candidate
                break

    if override or "CLANG_INCLUDE_DIR" not in os.environ:
        resource_dirs = sorted(
            glob.glob(os.path.join(prefix, "lib", "clang", "*", "include")),
            key=lambda d: [int(n) for n in d.split(os.sep) if n.isdigit()] or [0],
        )
        if resource_dirs:
            os.environ["CLANG_INCLUDE_DIR"] = resource_dirs[-1]


def _iter_flags(tokens: list[str]) -> list[str]:
    """Yield the parse-relevant flags from one compile command, normalising
    ``-isystem`` (one or two tokens) into an ``-I`` argument."""
    flags: list[str] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token == "-isystem":
            if index + 1 < len(tokens):
                flags.append("-isystem")
                flags.append(tokens[index + 1])
            index += 2
            continue
        if token.startswith("-isystem"):
            flags.append("-isystem")
            flags.append(token[len("-isystem") :])
        elif token.startswith(_FLAG_PREFIXES):
            flags.append(token)
        index += 1
    return flags


def _resolve(directory: str, path: str) -> str:
    return os.path.normpath(os.path.join(directory, path))


def include_args_from_compile_commands(
    compile_commands: str,
    target_filter: str | None = None,
) -> list[str]:
    """
    Return the ``-I``/``-isystem``/``-D``/``-std=`` flags for a target.

    Include and ``-isystem`` paths are resolved to absolute paths (relative to
    each entry's ``directory``) so the result is independent of the working
    directory ``pybind11-mkdoc`` is invoked from. Duplicate flags are removed
    while preserving first-seen order so that include precedence is kept.

    Parameters
    ----------
    compile_commands:
        Path to a ``compile_commands.json`` file or to the build directory that
        contains it.
    target_filter:
        If given, only consider compile entries whose source ``file`` resolves
        underneath this directory. When no entry matches (for example a
        standalone build with a different layout) every entry is used instead.
    """

    path = compile_commands
    if os.path.isdir(path):
        path = os.path.join(path, "compile_commands.json")
    with open(path, encoding="utf-8") as handle:
        entries = json.load(handle)

    target_filter = os.path.realpath(target_filter) if target_filter else None

    def under(entry: dict[str, str], directory: str) -> bool:
        source = os.path.realpath(_resolve(entry["directory"], entry["file"]))
        return source == directory or source.startswith(directory + os.sep)

    def collect(predicate: object) -> list[str]:
        ordered: list[str] = []
        seen: set[str] = set()
        std_seen = False
        for entry in entries:
            if not predicate(entry):  # type: ignore[operator]
                continue
            directory = entry["directory"]
            tokens = (
                entry["arguments"]
                if "arguments" in entry
                else shlex.split(entry["command"])
            )
            flags = _iter_flags(tokens)
            position = 0
            while position < len(flags):
                flag = flags[position]
                if flag == "-isystem":
                    resolved = _resolve(directory, flags[position + 1])
                    key = f"-isystem\0{resolved}"
                    if key not in seen and os.path.isdir(resolved):
                        seen.add(key)
                        # Emit as a single joined token: pybind11-mkdoc treats
                        # any argument not starting with "-" as a header file,
                        # so "-isystem" and its path must not be split.
                        ordered.append("-isystem" + resolved)
                    position += 2
                    continue
                if flag.startswith("-I"):
                    resolved = _resolve(directory, flag[2:])
                    key = f"-I\0{resolved}"
                    if key not in seen and os.path.isdir(resolved):
                        seen.add(key)
                        ordered.append("-I" + resolved)
                elif flag.startswith("-std="):
                    if not std_seen:
                        std_seen = True
                        ordered.append(flag)
                elif flag not in seen:
                    seen.add(flag)
                    ordered.append(flag)
                position += 1
        return ordered

    if target_filter is None:
        return collect(lambda entry: True)

    # Prefer entries compiled from the target directory. A header-only module
    # has no sources there, so walk up (e.g. to the module's src/ directory,
    # which also contains the compiled binding sources) until something matches
    # before falling back to using every entry.
    probe = target_filter
    while True:
        matched = collect(lambda entry, d=probe: under(entry, d))
        if matched:
            return matched
        parent = os.path.dirname(probe)
        if parent == probe:
            return collect(lambda entry: True)
        probe = parent
