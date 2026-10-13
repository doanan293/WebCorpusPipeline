import json
from pathlib import Path

import pytest

from data_pipeline.domains.probe import (
    ProbeResult,
)
from data_pipeline.domains.report import (
    load_candidate_hostnames,
    render_active_domains,
    render_report,
)


def test_candidate_hostnames_are_normalized_unique_and_hust_scoped(tmp_path: Path) -> None:
    source = tmp_path / "domains.txt"
    source.write_text(
        "B.HUST.EDU.VN\na.hust.edu.vn\nb.hust.edu.vn\noutside.test\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="outside HUST scope"):
        load_candidate_hostnames(source)

    source.write_text(
        "B.HUST.EDU.VN\na.hust.edu.vn\nb.hust.edu.vn\n",
        encoding="utf-8",
    )
    assert load_candidate_hostnames(source) == (
        "a.hust.edu.vn",
        "b.hust.edu.vn",
    )


def test_active_domain_output_is_plain_sorted_hostname_list() -> None:
    output = render_active_domains(
        ["b.hust.edu.vn", "a.hust.edu.vn", "b.hust.edu.vn"],
    )

    assert output == "a.hust.edu.vn\nb.hust.edu.vn\n"


def test_report_is_stable_jsonl_sorted_by_hostname() -> None:
    results = [
        ProbeResult("b.hust.edu.vn", False, "dns_error", error="not found"),
        ProbeResult(
            "a.hust.edu.vn",
            True,
            "public_response",
            scheme="https",
            status=200,
            final_url="https://a.hust.edu.vn/",
            title="A",
        ),
    ]

    lines = render_report(results).splitlines()

    assert [json.loads(line)["hostname"] for line in lines] == [
        "a.hust.edu.vn",
        "b.hust.edu.vn",
    ]
    assert json.loads(lines[0])["active"] is True
    assert json.loads(lines[1])["reason"] == "dns_error"
