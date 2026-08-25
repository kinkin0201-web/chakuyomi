# -*- coding: utf-8 -*-
"""本命(1号艇)が飛ぶレースを予測する。

■ なぜこの方向か
   「1着を当てる」モデルは、高確信度のとき99%以上「1号艇」を選ぶ。
   1号艇を買うだけの人と同じ答えになり、商品価値が出ない。

   一方「1号艇が飛ぶか」は約45%の頻度で起き、
   1号艇を買う人が最も知りたい情報であり、
   「1号艇固定」という強力なベースラインとの比較からも逃れられる。

■ 予測対象
   1レース1行。1号艇が2着以下なら 1(飛ぶ)、1着なら 0(堅い)。
   二値分類なので LGBMClassifier を使う。
"""
import argparse
import numpy as np, pandas as pd, sqlite3, lightgbm as lgb
from sklearn.metrics import roc_auc_score
from train_model import load_data, add_recent_form

def build(db, require_result=True):
    """レース単位の特徴量を作る。

    require_result=True  : 学習用。1号艇の着順が確定したレースのみ。
    require_result=False : 予測配信用。着順が未確定・失格でも残す。

    学習時は目的変数(1号艇が飛んだか)が必要だが、配信時は不要。
    ここを区別しないと、失格者が出たレースが配信から丸ごと消える。
    """
    df = load_data(db)
    df = add_recent_form(df)
    if require_result:
        df = df[df["rank"].notna()].copy()
    else:
        df = df.copy()

    # レース単位に集約する
    rows = []
    for rid, g in df.groupby("race_id", sort=False):
        g = g.sort_values("boat_number")
        b1 = g[g.boat_number == 1]
        if b1.empty or len(g) < 6:
            continue
        b1 = b1.iloc[0]
        # 1号艇の着順が不明(失格・欠場)なら学習対象にはできない
        if require_result and pd.isna(b1["rank"]):
            continue
        rec = {
            "race_id": rid, "date": g["date"].iloc[0],
            "stadium_code": g["stadium_code"].iloc[0],
            "race_number": g["race_number"].iloc[0],
            "distance": g["distance"].iloc[0],
            "weather": g["weather"].iloc[0],
            "wind_direction": g["wind_direction"].iloc[0],
            "wind_speed": g["wind_speed"].iloc[0],
            "wave_height": g["wave_height"].iloc[0],
            # 目的変数: 1号艇が2着以下 = 飛ぶ(未確定なら None)
            "upset": None if pd.isna(b1["rank"]) else int(b1["rank"] > 1),
        }
        # 1号艇の力量
        for c in ["win_rate_all", "top2_rate_all", "win_rate_local", "top2_rate_local",
                  "motor_win_rate", "exhibition_time", "player_age",
                  "recent_win_10", "recent_top2_10", "recent_rank_10"]:
            rec[f"b1_{c}"] = b1[c]
        rec["b1_class"] = b1["player_class"]

        # 対抗勢(2〜6号艇)の力量。1号艇との差が肝。
        rest = g[g.boat_number > 1]
        for c in ["win_rate_all", "top2_rate_all", "motor_win_rate",
                  "exhibition_time", "recent_top2_10"]:
            rec[f"rest_max_{c}"] = rest[c].max()
            rec[f"rest_mean_{c}"] = rest[c].mean()
            # 1号艇がどれだけ抜けているか(負なら格上がいる)
            rec[f"gap_{c}"] = b1[c] - rest[c].max()

        # 展示タイムは小さいほど速いので符号を反転
        rec["gap_exhibition_time"] = rest["exhibition_time"].min() - b1["exhibition_time"]
        rows.append(rec)

    d = pd.DataFrame(rows)
    for c in ["stadium_code", "weather", "wind_direction", "b1_class"]:
        d[c] = d[c].astype("category")
    return d

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--db", default="up.db")
    p.add_argument("--out", default="upset_model.txt")
    p.add_argument("--valid-days", type=int, default=21)
    p.add_argument("--test-days", type=int, default=21)
    a = p.parse_args()

    print("データ構築中...")
    d = build(a.db)
    print(f"  {len(d):,} レース / 飛ぶ率 {d['upset'].mean():.1%}")

    days = np.sort(d["date"].unique())
    ts, vs = days[-a.test_days], days[-(a.test_days + a.valid_days)]
    tr, va, te = d[d.date < vs], d[(d.date >= vs) & (d.date < ts)], d[d.date >= ts]
    print(f"  train {len(tr):,} / valid {len(va):,} / test {len(te):,}")

    feats = [c for c in d.columns if c not in ("race_id", "date", "upset")]
    m = lgb.LGBMClassifier(
        objective="binary", n_estimators=2000, learning_rate=0.03,
        num_leaves=31, min_child_samples=40, colsample_bytree=0.8,
        subsample=0.8, subsample_freq=1, random_state=42, verbose=-1)
    m.fit(tr[feats], tr["upset"],
          eval_set=[(va[feats], va["upset"])], eval_metric="auc",
          callbacks=[lgb.early_stopping(80, verbose=False), lgb.log_evaluation(200)])
    print(f"  最良イテレーション: {m.best_iteration_}")

    for name, s in [("VALID", va), ("TEST (未知)", te)]:
        p_ = m.predict_proba(s[feats])[:, 1]
        auc = roc_auc_score(s["upset"], p_)
        print(f"\n===== {name} =====")
        print(f"レース数 : {len(s):,}   実際の飛ぶ率 : {s['upset'].mean():.1%}")
        print(f"AUC      : {auc:.4f}")
        print("\n確信度で絞った場合(「飛ぶ」と予測):")
        print("しきい値  対象   的中率(実際に飛んだ)   全体比")
        for th in [0.5, 0.6, 0.7, 0.8]:
            sel = s[p_ >= th]
            if len(sel) < 30: continue
            print(f"  {th:.0%}  {len(sel):>6,}   {sel['upset'].mean():>8.1%}   {len(sel)/len(s):>6.1%}")
        print("\n逆に「堅い」と予測した場合:")
        print("しきい値  対象   1号艇が1着だった率")
        for th in [0.3, 0.2, 0.1]:
            sel = s[p_ <= th]
            if len(sel) < 30: continue
            print(f"  {th:.0%}以下 {len(sel):>6,}   {1-sel['upset'].mean():>8.1%}")

    m.booster_.save_model(a.out)
    imp = pd.DataFrame({"f": feats, "g": m.booster_.feature_importance("gain")})
    print("\n===== 重要度 TOP10 =====")
    for _, r in imp.sort_values("g", ascending=False).head(10).iterrows():
        print(f"  {r['f']:<28} {r['g']:>10,.0f}")

if __name__ == "__main__":
    main()
