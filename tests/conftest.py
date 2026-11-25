from pathlib import Path

import pytest

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


@pytest.fixture
def sql_diff() -> str:
    return (EXAMPLES / "sql-injection.diff").read_text(encoding="utf-8")


@pytest.fixture
def laravel_diff() -> str:
    return (EXAMPLES / "laravel-orders.diff").read_text(encoding="utf-8")


@pytest.fixture
def clean_diff() -> str:
    return (EXAMPLES / "clean-refactor.diff").read_text(encoding="utf-8")
