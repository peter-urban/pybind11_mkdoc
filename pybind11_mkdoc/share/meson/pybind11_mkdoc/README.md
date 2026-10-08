# Using pybind11_mkdoc from Meson

Meson has no public API to enumerate the include directories of a `dependency`
object, so the include/define/standard flags that `pybind11-mkdoc` needs are
read back out of the `compile_commands.json` that Meson generates for the Ninja
backend. Two helpers in `pybind11_mkdoc.meson_support` do the heavy lifting:

* `configure_libclang_env()` pins libclang to the interpreter's own environment
  by setting `LIBCLANG_PATH` and `CLANG_INCLUDE_DIR`. This works with both this
  fork and upstream `pybind11_mkdoc`, so a conda/venv build does not get
  confused between a system and an environment `libclang`.
* `include_args_from_compile_commands(build_root, target_filter)` returns the
  `-I`/`-isystem`/`-D`/`-std=` flags for a target. `target_filter` restricts the
  flags to the entries compiled from that directory (walking up to a parent and
  finally to every entry when a module is header-only).

## Per-header side-car files (`.docstrings/<name>.doc.hpp`)

`pybind11_mkdoc.meson_mkdoc` is a ready-made driver for projects that keep a
`<dir>/.docstrings/<name>.doc.hpp` next to every header and prepend a custom
`DOC(...)` macro template (`--doc-header`). Wire it as an on-demand
`run_target`:

```meson
pymod = import('python').find_installation(python_path, pure: false)

run_target(
  'mkdoc',
  command: [
    pymod, '-m', 'pybind11_mkdoc.meson_mkdoc',
    '--module-root', meson.current_source_dir() / '..' / 'include',
    '--doc-header',  meson.current_source_dir() / 'new_doc_header.hpp',
    '--build-root',  meson.global_build_root(),
  ],
)
```

Then regenerate on demand (the build must be configured first, so that
`compile_commands.json` exists):

```sh
meson setup builddir
meson compile -C builddir mkdoc          # or '<subproject>@@mkdoc' in a superproject
```

`meson compile` does not forward extra arguments to a `run_target`; to pass
`--regenerate` or `--renew`, invoke the driver directly:

```sh
python -m pybind11_mkdoc.meson_mkdoc \
    --module-root include --doc-header new_doc_header.hpp \
    --build-root builddir --regenerate
```

Run `python -m pybind11_mkdoc.meson_mkdoc --help` for all options.

## Combined doc header (standard `DOC(...)` output)

For the standard one-header-per-module layout (as produced by the CMake
integration), call `pybind11-mkdoc` directly with the flags from the helper.
A small wrapper script, invoked from a `custom_target`, is enough:

```python
import argparse, os, subprocess, sys
from pybind11_mkdoc.meson_support import (
    configure_libclang_env, include_args_from_compile_commands,
)

parser = argparse.ArgumentParser()
parser.add_argument("--build-root", required=True)
parser.add_argument("--target-filter")
parser.add_argument("-o", "--output", required=True)
parser.add_argument("headers", nargs="+")
args = parser.parse_args()

configure_libclang_env()
flags = include_args_from_compile_commands(args.build_root, args.target_filter)
subprocess.run(
    [sys.executable, "-m", "pybind11_mkdoc", *flags, "-o", args.output, *args.headers],
    check=True,
)
```

```meson
doc_h = custom_target(
  'my_module_doc.h',
  output: 'my_module_doc.h',
  command: [
    pymod, files('gen_docs.py'),
    '--build-root', meson.global_build_root(),
    '--target-filter', meson.current_source_dir(),
    '-o', '@OUTPUT@',
    files('include/my_header_1.hpp', 'include/my_header_2.hpp'),
  ],
)

py_module = pymod.extension_module(
  'my_module',
  sources: ['src/my_module.cpp', doc_h],
  include_directories: include_directories('.'),
)
```
