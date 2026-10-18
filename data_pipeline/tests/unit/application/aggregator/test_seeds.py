from __future__ import annotations

from data_pipeline.application.aggregator.seeds import seed_payload


def test_seed_payload() -> None:
    assert seed_payload(["hust.edu.vn", "soict.hust.edu.vn"]) == {
        "urls": ["https://hust.edu.vn/", "https://soict.hust.edu.vn/"]
    }
