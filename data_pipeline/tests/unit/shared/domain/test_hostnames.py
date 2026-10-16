from pathlib import Path

import pytest

from data_pipeline.shared.infrastructure.files import load_hostnames


@pytest.mark.parametrize("line", ["-bad.test", "a.test:8080", "a test", "a.test/path"])
def test_load_hostnames_rejects_invalid_lines(tmp_path: Path, line: str) -> None:
    source = tmp_path / "domains.txt"
    source.write_text(f"ok.test\n{line}\n", encoding="utf-8")

    with pytest.raises(ValueError, match=r"domains.txt:2"):
        load_hostnames(source)
