import json
import sqlite3

import pytest

from ir_data.package import package_corpus, verify_corpus


def test_package_and_verify(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "articles.jsonl").write_text(
        json.dumps({"url": "https://example.test/a", "links": [], "assets": []}) + "\n"
    )
    (source / "urls.txt").write_text("https://example.test/a\n")
    (source / "README.json").write_text("{}\n")
    output = package_corpus(source, tmp_path / "work", tmp_path / "corpus")
    report = verify_corpus(output)
    assert report["valid"]
    assert (output / "links.jsonl").exists() and (output / "manifest.json").exists()


def test_package_refuses_unresolved_enrichment(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "articles.jsonl").write_text("{}\n")
    work = tmp_path / "work"
    work.mkdir()
    conn = sqlite3.connect(work / "state.sqlite3")
    conn.executescript(
        """
        CREATE TABLE inventory (url TEXT PRIMARY KEY, status TEXT, final_path TEXT);
        CREATE TABLE parse_results (url TEXT PRIMARY KEY, status TEXT);
        INSERT INTO inventory VALUES ('https://example.test/file.pdf', 'pending', NULL);
        """
    )
    conn.commit()
    conn.close()
    with pytest.raises(ValueError, match="downloads are pending"):
        package_corpus(source, work, tmp_path / "corpus")
