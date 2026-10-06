#!/usr/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
cd ../ # script is in misc/, go to repo root

# ---------------------------------------------------------------------------
# 1. Read current version from pybind11_mkdoc/__init__.py (single source of truth)
# ---------------------------------------------------------------------------
INIT_FILE=pybind11_mkdoc/__init__.py
CURRENT_VERSION=$(grep -m1 '^__version__' "$INIT_FILE" | sed 's/.*"\(.*\)".*/\1/')
echo "Current version: $CURRENT_VERSION"

# ---------------------------------------------------------------------------
# 2. Prompt for new version
# ---------------------------------------------------------------------------
read -rp "New version (leave empty to keep $CURRENT_VERSION): " NEW_VERSION
NEW_VERSION="${NEW_VERSION:-$CURRENT_VERSION}"

if [[ "$NEW_VERSION" != "$CURRENT_VERSION" ]]; then
    # Update pybind11_mkdoc/__init__.py (flit reads the version from here)
    sed -i "s/^__version__ = \".*\"/__version__ = \"$NEW_VERSION\"/" "$INIT_FILE"
    echo "Updated $INIT_FILE -> $NEW_VERSION"
fi

# Sync version into recipe.yaml (always, in case it drifted)
sed -i "s/version: \".*\"/version: \"$NEW_VERSION\"/" recipe/recipe.yaml
echo "Synced recipe/recipe.yaml -> $NEW_VERSION"

# ---------------------------------------------------------------------------
# 3. Build conda package
# ---------------------------------------------------------------------------
echo ""
echo "Building conda package..."
pixi run -e build rattler-build build \
    --recipe=recipe/recipe.yaml \
    --channel https://conda.anaconda.org/themachinethatgoesping \
    --channel conda-forge \
    --output-dir output

# ---------------------------------------------------------------------------
# 4. Find the built package
# ---------------------------------------------------------------------------
CONDA_FILE=$(find output/noarch -name "pybind11_mkdoc-${NEW_VERSION}-*.conda" -type f -printf '%T@ %p\n' 2>/dev/null \
    | sort -n | tail -1 | cut -d' ' -f2-)

if [[ -z "${CONDA_FILE:-}" ]]; then
    # Fallback: newest .conda in any output subdirectory
    CONDA_FILE=$(find output -name "pybind11_mkdoc-*.conda" -type f -printf '%T@ %p\n' 2>/dev/null \
        | sort -n | tail -1 | cut -d' ' -f2-)
fi

if [[ -z "${CONDA_FILE:-}" ]]; then
    echo "ERROR: No .conda file found in output/"
    exit 1
fi

# ---------------------------------------------------------------------------
# 5. Confirm before uploading
# ---------------------------------------------------------------------------
echo ""
echo "Package to upload:"
echo "  $CONDA_FILE"
echo ""
read -rp "Upload to anaconda.org/themachinethatgoesping? [y/N] " CONFIRM
if [[ "${CONFIRM,,}" != "y" && "${CONFIRM,,}" != "yes" ]]; then
    echo "Aborted."
    exit 0
fi

# ---------------------------------------------------------------------------
# 6. Upload
# ---------------------------------------------------------------------------
pixi run -e build rattler-build upload anaconda -o themachinethatgoesping "$CONDA_FILE"
echo ""
echo "Done! Published pybind11_mkdoc $NEW_VERSION"
