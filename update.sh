#!/bin/bash
# 手動で予測を更新する。
#
# GitHub Actions のスケジュールは混雑時に間引かれるため、
# 締切前に確実に更新したいときはこれを使う。
#
#   ./update.sh          福岡のみ
#   ./update.sh 22,12    複数場

set -e
cd "$(dirname "$0")"
STADIUMS="${1:-22}"
TODAY=$(TZ=Asia/Tokyo date +%Y-%m-%d)

echo "[$(TZ=Asia/Tokyo date +%H:%M)] 更新開始 (場: $STADIUMS)"

# 締切が近いレースのオッズを取る
python3 collect_odds.py --stadiums "$STADIUMS" --window 30 2>/dev/null | tail -1

# 予測を生成(展示タイムは前回値を引き継ぐ)
python3 predict_today.py \
  --stadiums "$STADIUMS" \
  --window 20 \
  --out "firebase/public/data/${TODAY}.json" 2>/dev/null | tail -1

# AIの読みを付ける。APIキーが無ければ黙って飛ばす。
if [ -n "$ANTHROPIC_API_KEY" ]; then
  python3 explain.py --file "firebase/public/data/${TODAY}.json" 2>/dev/null | tail -1
fi

echo "{\"latest\":\"${TODAY}\"}" > firebase/public/data/index.json

# 配信
(cd firebase && firebase deploy --only hosting 2>&1 | grep -E "Hosting URL|Error" | tail -1)

echo "[$(TZ=Asia/Tokyo date +%H:%M)] 完了 https://chakuyomi.web.app"
