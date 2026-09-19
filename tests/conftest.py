"""Shared fixtures.

The warehouse fixture is session-scoped and read-only: building it once and
querying it many times is the difference between a test suite people run and
one they skip.
"""

from __future__ import annotations

import duckdb
import pytest

from workplace_platform.config import WAREHOUSE_PATH, load_config


@pytest.fixture(scope="session")
def cfg():
    return load_config(profile="demo")


@pytest.fixture(scope="session")
def con():
    if not WAREHOUSE_PATH.exists():
        pytest.skip("no warehouse; run `make pipeline` first")
    connection = duckdb.connect(str(WAREHOUSE_PATH), read_only=True)
    yield connection
    connection.close()


@pytest.fixture(scope="session")
def registry():
    from workplace_platform.semantic.registry import load_registry

    return load_registry()
