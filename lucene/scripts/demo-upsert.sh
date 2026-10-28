#!/usr/bin/env bash
# Demo dong bo: upsert cac batch du lieu giu lai (data/demo/batch-NNN.{html,other}.json) vao MongoDB
# nhu crawler vua ghi - updated_at = luc chay. Sau do --mode sync dua vao chi muc.
#
#   bash scripts/demo-upsert.sh 1            # batch 1
#   bash scripts/demo-upsert.sh 1 10         # batch 1..10
#   bash scripts/demo-upsert.sh 1 100 30     # batch 1..100, cach nhau 30 giay (mo phong crawler chay lien tuc)
#
# Chay trong thu muc lucene/, MongoDB cua compose.yaml dang bat (docker compose up -d mongo).
# Upsert theo url ($merge): url chua co -> them, da co -> ghi de cac truong. Chay lai 1 batch -> chi cap nhat.
set -euo pipefail

FROM=${1:?"Cach dung: bash scripts/demo-upsert.sh <tu batch> [den batch] [so giay giua 2 batch]"}
TO=${2:-$FROM}
DELAY=${3:-0}
DB=${MONGO_DB:-information_retrieval}
DIR=data/demo

for ((b = FROM; b <= TO; b++)); do
  n=$(printf '%03d' "$b")
  for c in html other; do
    f="$DIR/batch-$n.$c.json"
    [[ -f "$f" ]] || continue
    docker compose exec -T mongo mongoimport --quiet --db "$DB" --collection demo_incoming --drop < "$f"
    docker compose exec -T mongo mongosh --quiet "$DB" --eval "
      const now = new Date().toISOString().slice(0, 19) + '+00:00';
      const before = db.$c.countDocuments();
      const total = db.demo_incoming.countDocuments();
      db.demo_incoming.aggregate([
        {\$unset: ['_id', 'batch']},
        {\$set: {updated_at: now, checked_at: now, changed_at: now}},
        {\$merge: {into: '$c', on: 'url', whenMatched: 'merge', whenNotMatched: 'insert'}}]);
      const added = db.$c.countDocuments() - before;
      db.demo_incoming.drop();
      print('[' + now + '] batch $n $c: ' + total + ' ban ghi -> them ' + added + ', cap nhat ' + (total - added));"
  done
  if ((b < TO && DELAY > 0)); then sleep "$DELAY"; fi
done
