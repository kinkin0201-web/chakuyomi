# -*- coding: utf-8 -*-
"""モデルの健全性を監視する。

予測AIは「静かに劣化する」のが最大の運用リスク。
エラーを出さずに精度だけ落ちるため、定期的な自動チェックが要る。
異常時は終了コード1を返すので、CIやcronで検知できる。
"""
import argparse, json, sqlite3, sys
import numpy as np, pandas as pd, lightgbm as lgb
from train_model import add_features, CAT_FEATURES

# しきい値: これを下回ったら警告する
MIN_EDGE = 0.005      # 1号艇固定に対する優位(0.5pt)
MIN_AUC = 0.80
MAX_NULL_RATE = 0.02  # 主要特徴量の欠損率

def check(db, model, days=14):
    conn = sqlite3.connect(db)
    df = pd.read_sql("""
      SELECT r.race_id,r.date,r.stadium_code,r.race_number,r.distance,r.weather,
             r.wind_direction,r.wind_speed,r.wave_height,e.boat_number,e.player_class,
             e.player_age,e.win_rate_all,e.top2_rate_all,e.win_rate_local,
             e.top2_rate_local,e.motor_win_rate,e.boat_win_rate,e.exhibition_time,e.rank
      FROM races r JOIN entries e USING(race_id)
      ORDER BY r.date,r.race_id,e.boat_number
    """, conn); conn.close()

    issues = []
    report = {}

    # --- 1. データの鮮度 ---
    latest = df["date"].max()
    report["latest_date"] = latest
    gap = (pd.Timestamp.now().normalize() - pd.Timestamp(latest)).days
    report["days_behind"] = gap
    if gap > 3:
        issues.append(f"データが{gap}日遅れています（取り込みが止まっている可能性）")

    # --- 2. 欠損率 ---
    scored = df[df["rank"].notna()]
    for col in ["motor_win_rate", "exhibition_time", "win_rate_all"]:
        rate = scored[col].isna().mean()
        report[f"null_{col}"] = round(rate, 4)
        if rate > MAX_NULL_RATE:
            issues.append(f"{col} の欠損率が {rate:.1%}（パーサ破損の可能性）")

    # --- 3. 直近N日での精度 ---
    d = df[df["rank"].notna()].copy()
    d["is_win"] = (d["rank"] == 1).astype(int)
    d = add_features(d)
    for c in CAT_FEATURES:
        d[c] = d[c].astype("category")
    days_u = np.sort(d["date"].unique())
    recent = d[d["date"] >= days_u[-days]]

    b = lgb.Booster(model_file=model)
    recent = recent.copy()
    recent["score"] = b.predict(recent[b.feature_name()])

    top = recent.loc[recent.groupby("race_id")["score"].idxmax()]
    hit = top["is_win"].mean()
    base = recent[recent.boat_number == 1]["is_win"].mean()
    from sklearn.metrics import roc_auc_score
    auc = roc_auc_score(recent["is_win"], recent["score"])

    report.update({
        "races": int(top.shape[0]), "hit_rate": round(hit, 4),
        "baseline": round(base, 4), "edge": round(hit - base, 4),
        "auc": round(auc, 4),
    })

    if hit - base < MIN_EDGE:
        issues.append(f"優位性が {(hit-base)*100:.1f}pt（基準 {MIN_EDGE*100:.1f}pt を下回る）")
    if auc < MIN_AUC:
        issues.append(f"AUC {auc:.3f}（基準 {MIN_AUC} を下回る）")

    return report, issues

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--db", default="kyotei_prediction_core.db")
    p.add_argument("--model", default="kyotei_model.txt")
    p.add_argument("--days", type=int, default=14)
    p.add_argument("--json", action="store_true")
    a = p.parse_args()

    rep, issues = check(a.db, a.model, a.days)

    if a.json:
        print(json.dumps({"report": rep, "issues": issues}, ensure_ascii=False))
    else:
        print("===== モデル健全性チェック =====")
        print(f"最新データ    : {rep['latest_date']} ({rep['days_behind']}日前)")
        print(f"評価レース数  : {rep['races']:,}")
        print(f"1着的中率     : {rep['hit_rate']:.1%}")
        print(f"1号艇固定     : {rep['baseline']:.1%}")
        print(f"優位性        : {rep['edge']*100:+.1f}pt")
        print(f"AUC           : {rep['auc']:.4f}")
        print()
        if issues:
            print("⚠ 検出された問題:")
            for i in issues:
                print(f"  - {i}")
        else:
            print("✓ 異常なし")

    sys.exit(1 if issues else 0)
