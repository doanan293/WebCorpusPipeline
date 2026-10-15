from data_pipeline.crawl.extract.fingerprint import change_fingerprint


def test_fingerprint_ignores_digits_months_and_counters() -> None:
    assert change_fingerprint("16 Tháng Năm, 2024\nLượt xem: 5944") == change_fingerprint(
        "16 May, 2024\nViewed: 5996"
    )


def test_fingerprint_sees_real_edits() -> None:
    assert change_fingerprint("Hạn nộp hồ sơ là thứ Hai") != change_fingerprint(
        "Hạn nộp hồ sơ là thứ Sáu"
    )
