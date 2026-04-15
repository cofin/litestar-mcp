#!/usr/bin/env bash
# Validate that the ``uvx`` snippets in the reference notes docs parse
# as a valid ``uvx`` invocation.
#
# We intentionally do not *execute* each snippet (that would hit the
# network on every CI run). Instead we:
#
#   1. Extract every ``uvx --from litestar-mcp`` command from the docs.
#   2. Confirm ``uvx`` is on PATH and that its ``--help`` works, so we
#      know the binary can be asked to parse arguments at all.
#   3. For each extracted snippet, check that it mentions the required
#      pieces (``--from litestar-mcp``, ``--with``, and a
#      ``python -m docs.examples.notes.<family>.<variant>`` tail) so we
#      catch copy-paste drift early.
#
# The goal is copy-paste correctness, not reproducing the zero-install
# demo end to end. Full end-to-end coverage is handled by the
# integration suite on real filesystems.
#
# Set ``UVX_OFFLINE=1`` to skip the ``uvx --help`` sanity check when the
# CI environment has no network access.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DOCS=(
    "${ROOT}/docs/usage/uvx_guide.rst"
    "${ROOT}/docs/usage/reference_examples.rst"
    "${ROOT}/docs/examples/notes/README.md"
    "${ROOT}/docs/examples/notes/advanced_alchemy/README.md"
    "${ROOT}/docs/examples/notes/sqlspec/README.md"
)

for path in "${DOCS[@]}"; do
    if [[ ! -f "${path}" ]]; then
        echo "missing documentation file: ${path}" >&2
        exit 1
    fi
done

if [[ "${UVX_OFFLINE:-0}" != "1" ]]; then
    if ! command -v uvx >/dev/null 2>&1; then
        echo "uvx not on PATH; set UVX_OFFLINE=1 to skip this check" >&2
        exit 1
    fi
    if ! uvx --help >/dev/null 2>&1; then
        echo "uvx --help failed; the installed uvx cannot parse arguments" >&2
        exit 1
    fi
fi

snippet_count=0
error_count=0

while IFS= read -r line; do
    snippet_count=$((snippet_count + 1))
    if ! [[ "${line}" == *"--from litestar-mcp"* ]]; then
        echo "snippet missing '--from litestar-mcp': ${line}" >&2
        error_count=$((error_count + 1))
        continue
    fi
    if ! [[ "${line}" == *"--with"* ]]; then
        echo "snippet missing '--with <extras>': ${line}" >&2
        error_count=$((error_count + 1))
        continue
    fi
    if ! [[ "${line}" == *"python -m docs.examples.notes."* ]]; then
        echo "snippet missing notes-variant entrypoint: ${line}" >&2
        error_count=$((error_count + 1))
        continue
    fi
done < <(
    # Join backslash-continued lines before grepping so we inspect the
    # full command rather than the first physical line.
    for path in "${DOCS[@]}"; do
        awk '
            /\\$/ { sub(/\\$/, ""); buf = buf $0; next }
            { print buf $0; buf = "" }
        ' "${path}"
    done | grep -E "^[[:space:]]*uvx " || true
)

if (( error_count > 0 )); then
    echo "${error_count} uvx snippet(s) failed validation (of ${snippet_count})" >&2
    exit 1
fi

# Zero snippets is explicitly OK: the reference docs now lead with single-file
# PEP 723 runs (see `make validate-pep723`). `uvx --from litestar-mcp` is kept
# as a fallback mention in the guide but is no longer the primary entrypoint.
echo "OK: ${snippet_count} uvx snippet(s) validated"
