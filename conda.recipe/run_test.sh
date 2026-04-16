#!/bin/bash
set -e

# Find the installed package location
PKG_DIR=$(python -c "import pybind11_mkdoc; import os; print(os.path.dirname(os.path.dirname(pybind11_mkdoc.__file__)))")

# Run tests from the source tree if available, otherwise skip
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Check common locations for tests
if [ -d "$SRC_DIR/tests" ]; then
    TEST_DIR="$SRC_DIR/tests"
elif [ -d "$RECIPE_DIR/../tests" ]; then
    TEST_DIR="$RECIPE_DIR/../tests"
elif [ -d "tests" ]; then
    TEST_DIR="tests"
else
    echo "No test directory found, skipping pytest"
    exit 0
fi

pytest -v "$TEST_DIR" --ignore="$TEST_DIR/long_parameter_test.py"
