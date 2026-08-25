# -*- coding: utf-8 -*-
"""買い目の構成を最適化する。

■ 現状の問題
   確率上位N点をそのまま並べていた。
   その結果、1-2着の組がバラけ、どの組も3着を押さえきれない。

   例) 9R: 1-2-3, 1-2-5, 1-2-4, 1-3-5, 1-3-4
       -> 1-3 の組があるのに 1-3-2 を買っておらず、実際の結果を逃した

■ 考え方
   3連単は「1-2着を絞り、3着を広げる」方が的中しやすい。
   3着は残り4艇から1つなので、2〜3点押さえれば50〜75%カバーできる。

   そこで、同じ点数でも組み方を変えて期待値を比べ、
   最も良い構成を選ぶ。

■ 候補にする買い方
   formation : 1-2着を1組に固定し、3着を複数(1-2-3,4,5)
   spread    : 1着を固定し、2-3着を組み合わせ
   flat      : 確率上位をそのまま(従来方式)
"""
from itertools import combinations


def _score(picks, mode):
    """買い目全体の評価値。

    的中率重視 : 当たる確率の合計
    配当重視   : 期待値の合計(確率 x オッズ)
    """
    if mode == "hit":
        return sum(p["p"] for p in picks)
    return sum(p["p"] * (p["payout"] / 100) for p in picks)


def _build_formation(ranked, n):
    """1-2着を固定し、3着を広げる買い方。"""
    # 1-2着の組ごとに、3着候補を確率順で集める
    by_pair = {}
    for x in ranked:
        a, b, c = x["combo"]
        by_pair.setdefault((a, b), []).append(x)

    best = None
    for pair, items in by_pair.items():
        items = sorted(items, key=lambda x: -x["p"])[:n]
        if len(items) < 2:      # 3着を2点以上押さえられない組は対象外
            continue
        for k in range(2, min(n, len(items)) + 1):
            cand = items[:k]
            # 残りの点数は別の組の上位で埋める
            rest = [x for x in ranked if (x["combo"][0], x["combo"][1]) != pair]
            cand = cand + sorted(rest, key=lambda x: -x["p"])[:n - k]
            if best is None or len(cand) == n:
                yield cand


def _build_spread(ranked, n):
    """1着を固定し、2-3着を広げる買い方。"""
    by_first = {}
    for x in ranked:
        by_first.setdefault(x["combo"][0], []).append(x)
    for first, items in by_first.items():
        items = sorted(items, key=lambda x: -x["p"])[:n]
        if len(items) >= n:
            yield items[:n]


def optimize(ranked, n=5, mode="hit"):
    """N点の買い方を複数試し、評価値が最も高い構成を返す。

    ranked : [{combo:(a,b,c), p:確率, ev:期待値, payout:配当}, ...] 確率順
    mode   : 'hit'(的中率重視) / 'value'(配当重視)
    """
    if len(ranked) < n:
        return sorted(ranked, key=lambda x: -x["p"])[:n]

    candidates = []

    # 従来方式: 確率(または期待値)の上位をそのまま
    key = (lambda x: -x["p"]) if mode == "hit" else (lambda x: -x["ev"])
    candidates.append(sorted(ranked, key=key)[:n])

    # 3着を広げる方式
    for cand in _build_formation(ranked[:40], n):
        if len(cand) == n:
            candidates.append(cand)

    # 1着固定方式
    for cand in _build_spread(ranked[:40], n):
        candidates.append(cand)

    # 重複を除いて最良を選ぶ
    best, best_score = None, -1
    seen = set()
    for cand in candidates:
        key_ = tuple(sorted(tuple(x["combo"]) for x in cand))
        if key_ in seen:
            continue
        seen.add(key_)
        sc = _score(cand, mode)
        if sc > best_score:
            best, best_score = cand, sc
    return sorted(best, key=lambda x: -x["p"])


if __name__ == "__main__":
    # 9R の実データで検証
    import json
    d = json.load(open("/tmp/today.json"))
    races = [r for r in (d["races"] if isinstance(d, dict) else d) if r.get("result")]
    r = races[8]
    print(f"9R 実際 {r['result']['combo']}")
    print()
    print("【現在】確率上位5点")
    for p in r["safe"]:
        print(f"  {p['first']}-{p['second']}-{p['thirds'][0]}  {p['p']:.1%}")
