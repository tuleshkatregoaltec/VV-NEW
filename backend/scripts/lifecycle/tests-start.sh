#!/usr/bin/env sh

set -eu

# Fast tests do not migrate the application's database. Database integration
# fixtures provision and migrate their own isolated databases.
if [ "$#" -eq 0 ]; then
    set -- tests/api tests/unit tests/contracts tests/architecture tests/agents tests/evaluation tests/streaming tests/test_*.py
fi
exec uv run pytest -o cache_dir=/tmp/vitevue-pytest-cache --ignore=tests/integration "$@"
