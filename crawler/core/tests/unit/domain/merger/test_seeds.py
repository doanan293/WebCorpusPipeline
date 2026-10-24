from __future__ import annotations

from data_pipeline.domain.merger.seeds import parse_seeds


def test_parse_seeds_host_and_url_lines() -> None:
    seeds = parse_seeds(
        [
            "# start pages",
            "",
            "SoICT.hust.edu.vn.",
            "https://ctsv.hust.edu.vn/tin-tuc/abc#top",
        ]
    )
    assert seeds.urls == ("https://soict.hust.edu.vn/", "https://ctsv.hust.edu.vn/tin-tuc/abc")
    assert seeds.hosts == frozenset({"soict.hust.edu.vn", "ctsv.hust.edu.vn"})
    assert seeds.problems == ()


def test_parse_seeds_reports_bad_lines() -> None:
    seeds = parse_seeds(["soict.hust.edu.vn", "ftp://files.hust.edu.vn/x", "not a host"])
    assert seeds.urls == ("https://soict.hust.edu.vn/",)
    assert [problem.split(":")[0] for problem in seeds.problems] == ["line 2", "line 3"]
    assert "ftp://files.hust.edu.vn/x" in seeds.problems[0]


def test_url_with_port_needs_allowed_port() -> None:
    assert parse_seeds(["http://127.0.0.1:5000/"]).urls == ()
    allowed = parse_seeds(["http://127.0.0.1:5000/"], allowed_ports=frozenset({5000}))
    assert allowed.urls == ("http://127.0.0.1:5000/",)
    assert allowed.hosts == frozenset({"127.0.0.1"})


def test_host_with_trailing_slash_is_a_host() -> None:
    seeds = parse_seeds(["soict.hust.edu.vn/"])
    assert seeds.urls == ("https://soict.hust.edu.vn/",)
    assert seeds.problems == ()
