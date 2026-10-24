from __future__ import annotations

import pytest

from data_pipeline.domain.corpus.kaggle_metadata import dataset_metadata


def test_missing_key_is_named() -> None:
    with pytest.raises(ValueError, match="Kaggle metadata missing: license_name"):
        dataset_metadata(
            {"owner": "me", "slug": "c", "title": "t", "subtitle": "s", "description": "d"}
        )
