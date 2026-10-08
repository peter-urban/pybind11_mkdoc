"""
Generate per-header docstring side-car files for a Meson C++ project.

This is a thin, Meson-friendly driver around ``pybind11-mkdoc``. For every
``*.hpp`` under a module's source tree it writes a matching
``<dir>/.docstrings/<name>.doc.hpp`` that the header ``#include``s, using the
custom ``DOC(...)`` macro preamble supplied via ``--doc-header``.

The two hard problems of running ``pybind11-mkdoc`` from inside a build are
solved by :mod:`pybind11_mkdoc.meson_support`:

* libclang discovery is pinned to the interpreter's own environment, and
* the compiler ``-I``/``-isystem``/``-D``/``-std=`` flags are read back out of
  the ``compile_commands.json`` that Meson generates, so headers that include
  other project/third-party headers parse correctly.

It is normally invoked from a Meson ``run_target`` (see the ``meson`` directory
shipped with this package) but also works standalone::

    python -m pybind11_mkdoc.meson_mkdoc \\
        --module-root src/themachinethatgoesping \\
        --doc-header  src/nanomodule/new_doc_header.hpp \\
        --build-root  builddir
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
from collections.abc import Iterable
from copy import deepcopy

from pybind11_mkdoc.meson_support import (
    configure_libclang_env,
    include_args_from_compile_commands,
)

DOCSTRINGS_DIRNAME = ".docstrings"
IGNORE_MARKER = "IGNORE_DOC:"
HASH_PREFIX = "//sourcehash:"


def get_hash(file_name: str) -> str:
    hash_sha256 = hashlib.sha256()
    with open(file_name, "rb") as handle:
        for chunk in iter(lambda: handle.read(4096), b""):
            hash_sha256.update(chunk)
    return hash_sha256.hexdigest()


def get_ignore_doc(header: str) -> list[str]:
    """Collect the ``IGNORE_DOC:`` markers declared inside a header."""
    ignore_doc: list[str] = []
    with open(header, encoding="utf-8") as handle:
        for line in handle:
            if IGNORE_MARKER in line:
                marker = line.split(IGNORE_MARKER)[-1].strip()
                if marker:
                    ignore_doc.append(marker)
    return ignore_doc


def modify_doc(doc: str, header: str, doc_header_template: str) -> str:
    """Replace pybind11-mkdoc's macro preamble with the custom template and
    drop every declaration matched by an ``IGNORE_DOC:`` marker."""
    new_doc = deepcopy(doc_header_template)

    ignore_doc = get_ignore_doc(header)
    if ignore_doc:
        print(ignore_doc)

    start = False
    ignore = False
    for line in doc.split("\n"):
        if not start:
            if "#define DOC(...)" in line:
                start = True
            continue

        for marker in ignore_doc:
            if marker in line:
                ignore = True
                break

        if not ignore:
            new_doc += line + "\n"

        if ignore and ";" in line:
            ignore = False

    return new_doc


def add_doc_line(header: str, doc_path: str) -> None:
    """Inject ``#include ".docstrings/<name>.doc.hpp"`` after ``#pragma once``."""
    include_string = (
        '#include ".docstrings/' + doc_path.split("docstrings/")[-1] + '"'
    )

    file = ""
    found_pragma = False
    with open(header, encoding="utf-8") as handle:
        for line in handle:
            if include_string in line:
                return
            file += line
            if "#pragma once" in line:
                found_pragma = True
                file += f"\n/* generated doc strings */\n{include_string}\n"

    if not found_pragma:
        print(f"WARNING: did not find #pragma once in {header}")

    with open(header, "w", encoding="utf-8") as handle:
        handle.write(file)


def find_headers(module_root: str, ignore_files: list[str]) -> list[str]:
    headers: list[str] = []
    for root, _dirs, files in os.walk(module_root):
        if "output" in root:
            continue
        for file in files:
            if file.endswith(".doc.hpp"):
                continue
            if "pch" in file:
                continue
            if file.endswith(".hpp") and file not in ignore_files:
                headers.append(os.path.join(root, file))
    headers.sort()
    return headers


def discover_build_root(start: str) -> str | None:
    """Search upwards from ``start`` for a directory that contains a
    ``compile_commands.json`` in a common Meson/CMake build directory."""
    current = os.path.abspath(start)
    while True:
        for name in ("builddir", "build", "_build", "builddir-clang", "."):
            candidate = os.path.join(current, name, "compile_commands.json")
            if os.path.isfile(candidate):
                return os.path.dirname(candidate)
        parent = os.path.dirname(current)
        if parent == current:
            return None
        current = parent


def _progress(headers: list[str]) -> Iterable[str]:
    try:
        from tqdm import tqdm  # type: ignore[import-untyped]
    except ImportError:
        return headers
    return tqdm(headers)  # type: ignore[no-any-return]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--module-root",
        required=True,
        help="Directory tree to scan for *.hpp headers.",
    )
    parser.add_argument(
        "--doc-header",
        required=True,
        help="Template prepended to every generated .doc.hpp (DOC macros).",
    )
    parser.add_argument(
        "--build-root",
        default=None,
        help="Build directory containing compile_commands.json "
        "(auto-discovered when omitted).",
    )
    parser.add_argument(
        "--target-filter",
        default=None,
        help="Only use compile_commands entries under this directory "
        "(defaults to --module-root).",
    )
    parser.add_argument(
        "--regenerate",
        action="store_true",
        help="Regenerate all docstrings, ignoring the source hash.",
    )
    parser.add_argument(
        "--renew",
        action="store_true",
        help="Delete all .docstrings folders and exit.",
    )
    parser.add_argument(
        "--only",
        default=None,
        metavar="SUBSTR",
        help="Only process headers whose path contains SUBSTR (useful for "
        "debugging a single header). Implies --regenerate for the match.",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Stream all clang diagnostics (errors and warnings) to the "
        "console instead of the log file, and report every header processed.",
    )
    args = parser.parse_args()

    module_root = os.path.abspath(args.module_root)
    doc_header_path = os.path.abspath(args.doc_header)

    with open(doc_header_path, encoding="utf-8") as handle:
        doc_header_template = handle.read()

    headers = find_headers(module_root, ignore_files=[])

    if args.only:
        headers = [h for h in headers if args.only in h]
        args.regenerate = True
        if not headers:
            print(
                f"No header matches --only {args.only!r} under {module_root}",
                file=sys.stderr,
            )
            return 1

    if args.renew:
        for header in headers:
            docstrings_dir = os.path.join(
                os.path.dirname(header), DOCSTRINGS_DIRNAME
            )
            shutil.rmtree(docstrings_dir, ignore_errors=True)
        return 0

    # libclang + include flags come from the active environment and the build.
    configure_libclang_env()
    build_root = args.build_root or discover_build_root(module_root)
    if build_root is None or not os.path.isfile(
        os.path.join(build_root, "compile_commands.json")
    ):
        print(
            "ERROR: could not find compile_commands.json. Configure the Meson "
            "build first (meson setup <builddir>) or pass --build-root.",
            file=sys.stderr,
        )
        return 1
    mkdoc_args = include_args_from_compile_commands(
        build_root, target_filter=args.target_filter or module_root
    )

    log_path = os.path.join(build_root, "mkdoc_log.log")
    failures: list[str] = []
    iterator = headers if args.verbose else _progress(headers)
    with open(log_path, "w", encoding="utf-8") as log_out:
        for header in iterator:
            filepath, filename = os.path.split(header)
            output_path = os.path.join(
                filepath,
                DOCSTRINGS_DIRNAME,
                filename.replace("." + filename.split(".")[-1], ".doc.hpp"),
            )
            os.makedirs(os.path.dirname(output_path), exist_ok=True)

            add_doc_line(header, output_path)

            hash_new = get_hash(header)
            if not args.regenerate and os.path.exists(output_path):
                hash_old = "INVALID"
                with open(output_path, encoding="utf-8") as ifi:
                    line = ifi.readline()
                    if HASH_PREFIX in line:
                        hash_old = line.split(HASH_PREFIX)[1].strip()
                if hash_new == hash_old:
                    continue

            if args.verbose:
                print(f"[mkdoc] processing {header}", file=sys.stderr)

            # The header now #includes its .doc.hpp. For a new header (or after
            # --renew) that file does not exist yet, which would make the parse
            # fail on a missing include. Write the macro template as a stub so
            # the include resolves; it is overwritten with the real content
            # below.
            if not os.path.exists(output_path):
                with open(output_path, "w", encoding="utf-8") as stub:
                    stub.write(doc_header_template)

            # One header failing (e.g. a genuine missing include) must not abort
            # the whole module: log it and carry on so the remaining headers are
            # still generated. The full clang output always goes to the log; with
            # --verbose the diagnostic lines are also echoed to the console
            # (without pybind11-mkdoc's Python traceback).
            result = subprocess.run(
                [sys.executable, "-m", "pybind11_mkdoc", *mkdoc_args, header],
                capture_output=True,
                check=False,
            )
            stderr_text = result.stderr.decode("utf8", "replace")
            if stderr_text:
                log_out.write(stderr_text)
            if args.verbose:
                for line in stderr_text.splitlines():
                    if any(
                        tag in line
                        for tag in ("error:", "warning:", "note:", "fatal")
                    ):
                        print("   " + line, file=sys.stderr)
            if result.returncode != 0:
                failures.append(header)
                log_out.write(
                    f"ERROR: pybind11-mkdoc failed for {header} "
                    f"(exit {result.returncode})\n"
                )
                if args.verbose:
                    print(f"[mkdoc] FAILED {header}", file=sys.stderr)
                continue

            docstrings = result.stdout.decode("utf8")
            new_doc = modify_doc(docstrings, header, doc_header_template)

            with open(output_path, "w", encoding="utf-8") as ofi:
                ofi.write(f"{HASH_PREFIX} {hash_new}\n\n")
                ofi.write(new_doc)
                print("updated:", output_path)

    if failures:
        print(
            f"\n{len(failures)} header(s) failed to parse "
            f"(see {log_path}):",
            file=sys.stderr,
        )
        for header in failures:
            print(f"  {header}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
