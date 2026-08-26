# -*- coding: utf-8 -*-
"""レースの読みを言語化する。

■ LLM に何をさせ、何をさせないか
   させない: 予測そのもの。数値予測は LightGBM が圧倒的に優れており、
             LLM に着順を当てさせると精度が落ちる。
   させる  : モデルが「なぜそう判断したか」の言語化。
             寄与度(SHAP)を渡し、競艇の文脈で説明させる。

   つまり予測はモデル、説明は LLM という分担にする。
   LLM が数字を作らないので、説明と買い目が食い違わない。

■ 生成物
   races[].comment に短い解説を入れる。買い目や確率は変えない。
"""
import argparse, json, os, sys

import numpy as np
import pandas as pd
import lightgbm as lgb

sys.path.insert(0, '.')
from train_trifecta import build, feature_cols

MODEL = "claude-opus-5"

# 特徴量名を、そのまま画面に出せる日本語にする
LABELS = {
    "boat_number": "枠番",
    "win_rate_all": "全国勝率",
    "win_rate_all_diff_mean": "勝率の相対差",
    "win_rate_all_rank_in_race": "勝率のレース内順位",
    "top2_rate_all": "全国2連対率",
    "win_rate_local": "当地勝率",
    "top2_rate_local": "当地2連対率",
    "top2_rate_local_diff_mean": "当地2連対率の相対差",
    "motor_win_rate": "モーター2連対率",
    "motor_win_rate_diff_mean": "モーターの相対差",
    "exhibition_time": "展示タイム",
    "exhibition_time_diff_mean": "展示タイムの相対差",
    "exhibition_time_rank_in_race": "展示タイムの順位",
    "avg_st": "平均スタートタイミング",
    "recent_st_10": "直近のスタート",
    "recent_win_10": "直近10走の勝率",
    "recent_top2_10": "直近10走の2連対率",
    "recent_top2_10_diff_mean": "直近成績の相対差",
    "recent_rank_10": "直近の平均着順",
    "player_class": "級別",
    "course_change_rate": "進入変更の多さ",
    "stadium_code": "競艇場の傾向",
    "wind_speed": "風速",
    "wave_height": "波高",
}


def top_factors(booster, X, feats, n=4):
    """各艇について、予測を押し上げた/下げた要因を取り出す。"""
    contrib = booster.predict(X, pred_contrib=True)
    out = []
    for row in contrib:
        idx = np.argsort(-np.abs(row[:-1]))[:n]
        out.append([
            {"name": LABELS.get(feats[i], feats[i]), "impact": round(float(row[i]), 3)}
            for i in idx if abs(row[i]) > 0.01
        ])
    return out


def build_context(race, factors):
    """LLM に渡す材料。数字はすべてモデルが出したもの。"""
    boats = []
    for i, b in enumerate(race.get("boats", [])):
        boats.append({
            "枠": b["boat"], "選手": b.get("name"), "級別": b.get("cls"),
            "勝率": b.get("win"), "展示": b.get("ex"),
            "モーター2連対率": b.get("motor"),
            "この艇の判断材料": factors[i] if i < len(factors) else [],
        })
    picks = (race.get("safe") or [])[:3]
    return {
        "場": race.get("stadiumName"), "R": race.get("no"),
        "気象": {"天候": race.get("weather"), "風": race.get("wind"),
                 "風速": race.get("windSpeed"), "波": race.get("wave")},
        "出走": boats,
        "モデルの本命": [{"買い目": p["text"], "確率": p["p"]} for p in picks],
        "本命が崩れる警告": bool((race.get("upset") or {}).get("warn")),
    }


SYSTEM = """あなたは競艇の出走表を読む解説者です。

渡されるのは、予測モデルが算出した数値と、各艇について
「どの要素が予測を押し上げた/下げたか」の寄与度です。

制約:
- 着順やオッズを自分で予想しないでください。数値はモデルのものを使います。
- 寄与度に現れていない要素を根拠にしないでください。
- 断定を避け、「〜が効いています」「〜が不安材料です」のように書きます。
- 儲かる・当たると書かないでください。

出力は日本語で2〜3文、120字以内。レースの見どころを述べてください。
最初の1文で結論(どの艇が軸か、または軸不在か)を書きます。"""


def explain(client, race, factors):
    ctx = build_context(race, factors)
    msg = client.messages.create(
        model=MODEL,
        max_tokens=400,
        system=SYSTEM,
        messages=[{"role": "user", "content": json.dumps(ctx, ensure_ascii=False)}],
    )
    return "".join(b.text for b in msg.content if b.type == "text").strip()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--file", required=True, help="predict_today.py の出力")
    p.add_argument("--db", default="up.db")
    p.add_argument("--only-warn", action="store_true",
                   help="警告レースだけ解説する(コスト節約)")
    p.add_argument("--limit", type=int, default=0)
    a = p.parse_args()

    try:
        import anthropic
    except ImportError:
        print("anthropic が必要です: pip install anthropic", file=sys.stderr)
        sys.exit(1)

    with open(a.file, encoding="utf-8") as f:
        payload = json.load(f)
    races = payload["races"] if isinstance(payload, dict) else payload

    targets = [r for r in races
               if not a.only_warn or (r.get("upset") or {}).get("warn")]
    if a.limit:
        targets = targets[:a.limit]
    if not targets:
        print("対象レースがありません")
        return

    # 寄与度を出すため、当日の特徴量を作り直す
    df = build(a.db, require_result=False)
    booster = lgb.Booster(model_file="trifecta_1.txt")
    feats = feature_cols(df)
    by_race = {k: v for k, v in df.groupby("race_id")}

    client = anthropic.Anthropic()
    done = 0
    for r in targets:
        g = by_race.get(r["raceId"])
        if g is None or len(g) != 6:
            continue
        g = g.sort_values("boat_number")
        try:
            f = top_factors(booster, g[feats], feats)
            r["comment"] = explain(client, r, f)
            done += 1
            print(f"  {r['stadiumName']} {r['no']}R: {r['comment'][:40]}...")
        except Exception as e:
            print(f"  [SKIP] {r.get('no')}R: {e}", file=sys.stderr)

    out = payload if isinstance(payload, dict) else {"races": races}
    with open(a.file, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    print(f"{done} レースに解説を付与しました")


if __name__ == "__main__":
    main()
