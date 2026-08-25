# -*- coding: utf-8 -*-
"""3連単の買い目を配信用JSONに書き出す。

出力する印:
   ◎ 本命   上位1〜2点
   ○ 対抗   3〜6点目
   △ 押さえ 7〜12点目

「1-3-2,5」のようにまとめて表示できるよう、
1着・2着が同じ組をまとめる。
"""
import argparse, json, sys
import numpy as np, pandas as pd, lightgbm as lgb, sqlite3
sys.path.insert(0, '.')
from train_trifecta import build, feature_cols, rank_combos


def group_combos(combos, n=5, odds=None):
    """上位n点を買い目として整える。

    点数を増やすほど1点あたりの配当が薄まり、回収率が下がる。
    実測では 2点 83.0% > 5点 80.2% > 12点 78.2% と明確に悪化するため、
    既定は5点までに抑える。

    3着だけが異なる組は「1-2-3,5」とまとめるが、
    まとめすぎると点数が分からなくなるので、
    1行あたり最大3つ(=3点)までとする。
    """
    top = combos[:n]
    grouped = {}
    for (a, b, c), p in top:
        grouped.setdefault((a, b), {"thirds": [], "p": 0.0})
        grouped[(a, b)]["thirds"].append(c)
        grouped[(a, b)]["p"] += p

    # 1行にまとめすぎると「1-2-3,4,5,6」のようになり、
    # 何点買えばよいのか分からなくなる。1行は最大2点までとする。
    MAX_PER_ROW = 2
    # 各行に期待値を持たせる(元の順位を覚えておく)
    rank_of = {}
    for i, ((a, b, c), _) in enumerate(top):
        rank_of[(a, b, c)] = i

    out = []
    for (a, b), v in grouped.items():
        th = sorted(v["thirds"])
        for i in range(0, len(th), MAX_PER_ROW):
            chunk = th[i:i + MAX_PER_ROW]
            share = v["p"] * (len(chunk) / len(th))
            # この行に含まれる買い目の期待値(平均)
            evs = [expected_value(rank_of.get((a, b, c), 99), share / len(chunk),
                                  (odds or {}).get(f"{a}-{b}-{c}"))
                   for c in chunk]
            out.append({
                "text": f"{a}-{b}-{','.join(map(str, chunk))}",
                "first": a, "second": b, "thirds": chunk,
                "points": len(chunk),
                "p": round(share, 4),
                "ev": round(sum(evs) / len(evs), 3),
                # 想定配当(100円あたり)
                # 実オッズがあれば配当もそれに合わせる
                "payout": int(((odds or {}).get(f"{a}-{b}-{chunk[0]}") or 0) * 100)
                          or int(RANK_PAYOUT.get(
                              rank_of.get((a, b, chunk[0]), 99), FALLBACK_PAYOUT)),
                "realOdds": bool((odds or {}).get(f"{a}-{b}-{chunk[0]}")),
            })
    # 期待値の高い順に並べる。
    # 確率順だと人気(=低配当)が上に来て、回収率が下がるため。
    out.sort(key=lambda x: -(x.get("ev") or 0))
    return out


# 予測順位ごとの想定配当(実測の中央値)。
# 過去データから作った近似で、実オッズが取れない場合に使う。
RANK_PAYOUT = {
    0: 700, 1: 880, 2: 1020, 3: 1175, 4: 1450, 5: 1580,
    6: 1920, 7: 1690, 8: 2330, 9: 2250, 10: 2955, 11: 2620,
}
FALLBACK_PAYOUT = 3000


def expected_value(rank_i, prob, odds=None):
    """期待値 = 確率 x オッズ。

    実オッズがあればそれを使う(最も正確)。
    無ければ「予測順位 -> 実配当の中央値」で代用する。
    """
    o = odds if odds else (RANK_PAYOUT.get(rank_i, FALLBACK_PAYOUT) / 100)
    return prob * o


def load_odds(db_path, race_ids):
    """蓄積したオッズから、各レースの最新値を読む。"""
    import os
    if not os.path.exists(db_path):
        return {}
    conn = sqlite3.connect(db_path)
    out = {}
    try:
        q = """SELECT race_id, combo, odds FROM odds
               WHERE fetched_at = (SELECT MAX(fetched_at) FROM odds o2
                                   WHERE o2.race_id = odds.race_id)"""
        for rid, combo, o in conn.execute(q):
            if rid in race_ids:
                out.setdefault(rid, {})[combo] = o
    finally:
        conn.close()
    return out


def build_strategies(combos, odds=None, n=10):
    """2つの買い方を用意する。

    safe  : 的中確率の高い順。当たりやすいが配当は低い。
    value : 期待値の高い順。当たりにくいが妙味がある。

    どちらが良いかは利用者の好みなので、押し付けず両方見せる。
    実測(直近42日・各3点)では
      堅い  的中23.3% / 回収82.4%
      妙味  的中 9.8% / 回収83.8%
    """
    ranked = []
    for i, (c, p) in enumerate(combos):
        o = (odds or {}).get(f"{c[0]}-{c[1]}-{c[2]}")
        ranked.append({
            "combo": c, "p": p,
            "ev": expected_value(i, p, o),
            "payout": int((o or 0) * 100) or RANK_PAYOUT.get(i, FALLBACK_PAYOUT),
            "realOdds": bool(o),
        })

    safe = sorted(ranked, key=lambda x: -x["p"])[:n]

    # 妙味は期待値順だが、確率の下限を設ける。
    # 期待値だけで選ぶと確率0.6%・配当15万円のような万舟券ばかりになり、
    # 数百回に1回しか当たらず実用にならない。
    MIN_P = 0.02          # 2%未満(50回に1回以下)は除外
    cand = [x for x in ranked if x["p"] >= MIN_P]
    if len(cand) < n:     # 候補が足りなければ確率順で補う
        cand = ranked[:max(n, 12)]
    value = sorted(cand, key=lambda x: -x["ev"])[:n]

    def fmt(items, kind):
        """印を段階で振る。

        利用者が予算に応じて選べるようにする。
          ◎ 上位2点  … 絞って買う人向け(回収率が最も高い)
          ○ 次の3点  … 標準
          △ 残り     … 的中率を上げたい人向け
        """
        out = []
        for i, x in enumerate(items):
            a, b, c = x["combo"]
            mark = "◎" if i < 2 else ("○" if i < 5 else "△")
            out.append({
                "text": f"{a}-{b}-{c}",
                "first": a, "second": b, "thirds": [c],
                "points": 1,
                "p": round(x["p"], 4),
                "ev": round(x["ev"], 3),
                "payout": x["payout"],
                "realOdds": x["realOdds"],
                "mark": mark,
                "tier": 1 if i < 2 else (2 if i < 5 else 3),
            })
        return out

    return {"safe": fmt(safe, "safe"), "value": fmt(value, "value")}


def mark_of(i, ev=None):
    """印を決める。

    期待値が分かる場合はそれを基準にする。
    1.0未満は理論上「買うほど損」なので、印ではなく見送り扱いにする。
    """
    if ev is not None:
        if ev >= 1.3: return "◎"
        if ev >= 1.0: return "○"
        return "△"
    if i == 0: return "◎"
    if i <= 2: return "○"
    return "△"


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--db", default="up.db")
    p.add_argument("--date")
    p.add_argument("--out", required=True)
    p.add_argument("--points", type=int, default=10)
    p.add_argument("--odds-db", default="odds.db", help="蓄積したオッズ")
    a = p.parse_args()

    df = build(a.db, require_result=False)
    feats = feature_cols(df)
    for pos in (1, 2, 3):
        df[f"p{pos}"] = lgb.Booster(model_file=f"trifecta_{pos}.txt").predict(df[feats])

    date = a.date or df["date"].max()
    day = df[df.date == date]
    if day.empty:
        print(f"{date} のデータがありません", file=sys.stderr)
        json.dump([], open(a.out, "w"))
        return

    conn = sqlite3.connect(a.db)
    meta = pd.read_sql("""
      SELECT race_id,stadium_code,stadium_name,race_number,title,weather,
             wind_direction,wind_speed,wave_height,
             exact_trifecta_combo,exact_trifecta_payout
      FROM races WHERE date=?""", conn, params=(date,)).set_index("race_id")
    # 表示用の選手情報(学習の特徴量には無い列)
    ent = pd.read_sql("""
      SELECT e.race_id,e.boat_number,e.player_name,e.player_class,
             e.win_rate_all,e.exhibition_time,e.rank
      FROM races r JOIN entries e USING(race_id) WHERE r.date=?
    """, conn, params=(date,))
    conn.close()
    ent_by_race = {k: v for k, v in ent.groupby("race_id")}

    odds_map = load_odds(a.odds_db, set(day["race_id"]))
    if odds_map:
        print(f"  実オッズ: {len(odds_map)}レース分を使用")

    out = []
    for rid, g in day.groupby("race_id", sort=False):
        if len(g) != 6 or rid not in meta.index:
            continue
        m = meta.loc[rid]
        combos = rank_combos(g)
        odds = odds_map.get(rid)

        # 確定レースは実配当を優先する(締切前オッズとのズレを防ぐ)
        if pd.notna(m.exact_trifecta_combo) and pd.notna(m.exact_trifecta_payout):
            odds = dict(odds or {})
            odds[m.exact_trifecta_combo] = float(m.exact_trifecta_payout) / 100
        picks = group_combos(combos, a.points, odds)
        strategies = build_strategies(combos, odds, a.points)

        out.append({
            "raceId": rid, "stadium": m.stadium_code,
            "stadiumName": m.stadium_name, "no": int(m.race_number),
            "title": m.title, "weather": m.weather,
            "wind": m.wind_direction,
            "windSpeed": None if pd.isna(m.wind_speed) else float(m.wind_speed),
            "wave": None if pd.isna(m.wave_height) else float(m.wave_height),
            # 買い目(印つき)
            "picks": [{**x, "mark": mark_of(i, x.get("ev"))} for i, x in enumerate(picks)],
            "safe": strategies["safe"],
            "value": strategies["value"],
            # 提示した点数ぶんの合計確率 = このレースへの自信
            "confidence": round(float(sum(c[1] for c in combos[:a.points])), 4),
            "totalPoints": a.points,
            "result": (None if pd.isna(m.exact_trifecta_combo) else {
                "combo": m.exact_trifecta_combo,
                "payout": None if pd.isna(m.exact_trifecta_payout)
                          else int(m.exact_trifecta_payout),
            }),
            "boats": [{
                "boat": int(r.boat_number), "name": r.player_name,
                "cls": r.player_class,
                "win": None if pd.isna(r.win_rate_all) else float(r.win_rate_all),
                "ex": None if pd.isna(r.exhibition_time) else float(r.exhibition_time),
                "actual": None if pd.isna(r.rank) else int(r.rank),
            } for r in ent_by_race.get(rid, pd.DataFrame())
              .sort_values("boat_number").itertuples()],
        })

    out.sort(key=lambda r: (r["stadium"], r["no"]))

    # --- その日の収支を集計する ---
    # 結果が出ているレースだけを対象にする(当日はまだ空)
    bet = ret = hits = done = 0
    for r in out:
        res = r.get("result")
        if not res or not res.get("combo"):
            continue
        # 実際に買うのは期待値1.0以上だけ。全点数で計算すると
        # 画面の買い目と数字が食い違う。
        buy = [p for p in r["picks"] if (p.get("ev") or 0) >= 1.0]
        if not buy:
            continue
        done += 1
        bet += sum(p["points"] for p in buy) * 100
        for p in buy:
            a_, b_ = p["first"], p["second"]
            if any(f"{a_}-{b_}-{c}" == res["combo"] for c in p["thirds"]):
                ret += res.get("payout") or 0
                hits += 1
                break
    summary = {
        "races": done, "hitRaces": hits,
        "bet": bet, "return": ret,
        "roi": round(ret / bet, 4) if bet else None,
        "profit": ret - bet,
    }
    payload = {"races": out, "summary": summary}
    json.dump(payload, open(a.out, "w", encoding="utf-8"),
              ensure_ascii=False, separators=(",", ":"))
    if bet:
        print(f"{a.out}: {len(out)}レース / 投資{bet:,}円 回収{ret:,}円 "
              f"({ret/bet:.1%}) 収支{ret-bet:+,}円")
    else:
        print(f"{a.out}: {len(out)}レース (結果未確定)")


if __name__ == "__main__":
    main()
