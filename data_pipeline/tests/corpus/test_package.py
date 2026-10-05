import json

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
