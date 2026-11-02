#!/usr/bin/env python3
"""Nap tap van ban JSON (danh sach {url, title, content, olinks}) vao MongoDB theo schema cua crawler.

    python3 lucene/scripts/import_json_corpus.py lucene/data/thay/all_extracted_results.json --db thay

Ghi vao database --db (mac dinh "thay", XOA du lieu cu cua database do):
  html  : url, title, content, intro (van ban lien ket tro toi trang, tinh nhu crawler build_intro), images = []
  graph : moi olink mot canh (src, dest, intro); intro = etext (cau chua lien ket), khong co thi atext
  other, picture : rong (de lucene / DataApi chay binh thuong)
Can MongoDB cua docker-compose.yaml o goc repo dang chay; nap bang mongoimport trong container mongo.
"""
import argparse
import collections
import datetime
import json
import os
import re
import subprocess
import sys
import tempfile

MAX_INTRO_TEXTS = 20
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def norm(s):
    return re.sub(r"\s+", " ", (s or "").strip())


def link_text(link):
    return norm(link.get("etext")) or norm(link.get("atext"))


def build(records, now):
    pages, edges = {}, []
    for r in records:
        url = (r.get("url") or "").strip()
        content = r.get("content") or ""
        if not url or not content.strip() or url in pages:
            continue
        pages[url] = {"url": url, "title": norm(r.get("title")), "content": content, "intro": "",
                      "images": [], "updated_at": now}
        seen = set()
        for link in r.get("olinks") or []:
            if not isinstance(link, dict):
                continue
            dest = (link.get("dest") or "").split("#", 1)[0].strip()
            if not dest.startswith(("http://", "https://")):
                continue
            key = (dest, link_text(link))
            if key in seen:
                continue
            seen.add(key)
            edges.append({"src": url, "dest": dest, "intro": key[1]})

    counts = collections.defaultdict(collections.Counter)
    for e in edges:
        if e["dest"] in pages and e["intro"]:
            counts[e["dest"]][e["intro"]] += 1
    for url, c in counts.items():
        top = sorted(c.items(), key=lambda kv: (-kv[1], kv[0]))[:MAX_INTRO_TEXTS]
        pages[url]["intro"] = "\n".join(t for t, _ in top)
    return list(pages.values()), edges


def compose(*args, stdin=None):
    subprocess.run(["docker", "compose", *args], cwd=ROOT, check=True, stdin=stdin)


def mongoimport(db, collection, rows, tmp):
    path = os.path.join(tmp, collection + ".jsonl")
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    with open(path, "rb") as f:
        compose("exec", "-T", "mongo", "mongoimport", "--quiet", "--db", db, "--collection", collection,
                "--drop", stdin=f)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("json", help="tep JSON: danh sach {url, title, content, olinks}")
    ap.add_argument("--db", default="thay", help="database MongoDB dich (bi xoa truoc khi nap)")
    a = ap.parse_args()
    if a.db == "information_retrieval":
        sys.exit("khong ghi de database cua crawler; chon --db khac")

    with open(a.json, encoding="utf-8") as f:
        records = json.load(f)
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    pages, edges = build(records, now)
    urls = {p["url"] for p in pages}
    inside = sum(1 for e in edges if e["dest"] in urls)
    print(f"{len(records)} ban ghi -> {len(pages)} trang, {len(edges)} lien ket ({inside} tro vao tap van ban)")

    compose("exec", "-T", "mongo", "mongosh", "--quiet", a.db, "--eval", "db.dropDatabase()")
    with tempfile.TemporaryDirectory() as tmp:
        mongoimport(a.db, "html", pages, tmp)
        mongoimport(a.db, "graph", edges, tmp)
    compose("exec", "-T", "mongo", "mongosh", "--quiet", a.db, "--eval", """
        for (const c of ['html', 'other', 'picture']) db.getCollection(c).createIndex({url: 1}, {unique: true});
        db.graph.createIndex({src: 1}); db.graph.createIndex({dest: 1});
        print('html', db.html.countDocuments(), 'graph', db.graph.countDocuments());
    """)


if __name__ == "__main__":
    main()
