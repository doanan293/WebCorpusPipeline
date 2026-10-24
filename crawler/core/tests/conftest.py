from __future__ import annotations

import os
import re
from collections.abc import Iterator
from unittest import mock

import pytest

_SECRET_NAMES = re.compile(r"LLAMAPARSE_KEY\d+|KAGGLE_.*|CRAWLER_CONTACT")


@pytest.fixture(autouse=True)
def isolated_environment() -> Iterator[None]:
    """Keep real credentials out of tests and undo environment changes made by a test.

    Code under test loads the project ``.env`` into ``os.environ``; without this, a real
    key would leak into every later test.
    """
    with mock.patch.dict(os.environ):
        for name in [name for name in os.environ if _SECRET_NAMES.fullmatch(name)]:
            del os.environ[name]
        yield
