#!/usr/bin/env bash
# Lan dau: lap chi muc toan bo tu MongoDB va tinh PageRank (lau, chi mot lan: co moc .ready).
# Sau do: dong bo moi 10 giay o nen, API o tien trinh chinh.
set -euo pipefail

INDEX=${INDEX:-/data/index}
MONGO_URI=${MONGO_URI:-mongodb://mongo:27017}
JAVA_OPTS=${JAVA_OPTS:--Xmx3g}
run() { java $JAVA_OPTS -jar lucene-search.jar --mongo-uri "$MONGO_URI" --index "$INDEX" "$@"; }

if [[ ! -e "$INDEX/.ready" ]]; then
  echo "bach khoa" > /tmp/queries.txt   # --mode batch can mot file truy van; chi de lap chi muc roi thoat
  run --source mongo --mode batch --queries /tmp/queries.txt --out /tmp/results.txt
  run --mode pagerank
  touch "$INDEX/.ready"
fi

# Khoi dong lai khi loi (vd MongoDB tam mat ket noi), API van chay.
( while true; do run --mode sync --every 10 || true; sleep 10; done ) &
exec java $JAVA_OPTS -jar lucene-search.jar --mongo-uri "$MONGO_URI" --index "$INDEX" \
     --mode web --skip-index --port 8080
