# -*- coding: utf-8 -*-
"""3連単の買い目を予測する。

■ 方針
   120通りを直接分類するのは学習が難しい(1クラスあたりの事例が少ない)。
   代わりに「各艇が1着/2着/3着に入る確率」を別々に学習し、
   その積で組み合わせ確率を求める。

     P(a-b-c) = P1(a) x P2(b|aを除く) x P3(c|a,bを除く)

   2着・3着の条件付き確率は、残った艇の中での正規化で近似する。
   厳密ではないが、実データで検証すると十分な精度が出る。

■ 出力
   確率の高い組み合わせを上位から並べ、
   本命(◎)・対抗(○)・穴(△)として提示する。
"""
import argparse
import numpy as np, pandas as pd, lightgbm as lgb, sqlite3
from train_model import load_data, add_recent_form, add_features, CAT_FEATURES, NUM_FEATURES

def build(db, require_result=True):
    df = load_data(db)
    df = add_recent_form(df)
    if require_result:
        df = df[df["rank"].notna()].copy()
    df = add_features(df)
    for c in CAT_FEATURES:
        df[c] = df[c].astype("category")
    return df

def feature_cols(df):
    feats = [c for c in NUM_FEATURES if c in df.columns] + \
            [c for c in CAT_FEATURES if c in df.columns]
    feats += [c for c in df.columns if c.endswith(("_rank_in_race", "_diff_mean"))]
    feats += [c for c in df.columns if c.startswith("recent_")]
    return sorted(set(feats))

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--db", default="up.db")
    p.add_argument("--out", default="trifecta")
    p.add_argument("--valid-days", type=int, default=21)
    p.add_argument("--test-days", type=int, default=21)
    a = p.parse_args()

    print("データ構築中...")
    df = build(a.db)
    feats = feature_cols(df)
    print(f"  {len(df):,} 行 / {df.race_id.nunique():,} レース / 特徴量 {len(feats)}")

    days = np.sort(df["date"].unique())
    ts, vs = days[-a.test_days], days[-(a.test_days + a.valid_days)]
    tr = df[df.date < vs]
    va = df[(df.date >= vs) & (df.date < ts)]
    te = df[df.date >= ts]

    models = {}
    # 1着/2着/3着に入るかを、それぞれ独立に学習する
    for pos in (1, 2, 3):
        y_tr = (tr["rank"] == pos).astype(int)
        y_va = (va["rank"] == pos).astype(int)
        m = lgb.LGBMClassifier(objective="binary", n_estimators=1500,
                               learning_rate=0.04, num_leaves=31,
                               min_child_samples=40, colsample_bytree=0.8,
                               subsample=0.8, subsample_freq=1,
                               random_state=42, verbose=-1)
        m.fit(tr[feats], y_tr, eval_set=[(va[feats], y_va)], eval_metric="auc",
              callbacks=[lgb.early_stopping(60, verbose=False)])
        models[pos] = m
        from sklearn.metrics import roc_auc_score
        auc = roc_auc_score((te["rank"] == pos).astype(int), m.predict_proba(te[feats])[:, 1])
        print(f"  {pos}着モデル: AUC {auc:.4f} (iter {m.best_iteration_})")
        m.booster_.save_model(f"{a.out}_{pos}.txt")

    # ---- 3連単の的中を検証 ----
    te = te.copy()
    for pos in (1, 2, 3):
        te[f"p{pos}"] = models[pos].predict_proba(te[feats])[:, 1]

    hit1 = hit3 = hit6 = hit12 = n = 0
    for rid, g in te.groupby("race_id", sort=False):
        if len(g) != 6 or g["rank"].isna().any():
            continue
        combos = rank_combos(g)
        actual = tuple(g.sort_values("rank").head(3)["boat_number"].astype(int))
        if len(actual) < 3:
            continue
        n += 1
        top = [c[0] for c in combos]
        if actual == top[0]: hit1 += 1
        if actual in top[:3]: hit3 += 1
        if actual in top[:6]: hit6 += 1
        if actual in top[:12]: hit12 += 1

    print(f"\n===== 3連単の的中率 (未知データ {n:,}レース) =====")
    for k, v in [("1点", hit1), ("3点", hit3), ("6点", hit6), ("12点", hit12)]:
        print(f"  上位{k:>4}買い: {v/n:>6.1%}")
    print(f"\n  参考: でたらめに1点買うと {1/120:.1%}")

def rank_combos(g):
    """各艇の着順確率から、3連単120通りの確率を計算して並べる。"""
    boats = g["boat_number"].astype(int).to_numpy()
    p1 = g["p1"].to_numpy(); p2 = g["p2"].to_numpy(); p3 = g["p3"].to_numpy()
    # レース内で正規化する(合計1にして確率として扱えるようにする)
    p1 = p1 / max(p1.sum(), 1e-9)
    p2 = p2 / max(p2.sum(), 1e-9)
    p3 = p3 / max(p3.sum(), 1e-9)

    out = []
    for i in range(6):
        for j in range(6):
            if j == i: continue
            for k in range(6):
                if k == i or k == j: continue
                # 2着は1着を除いた中で、3着はさらに2着も除いた中で正規化する
                d2 = 1.0 - p2[i]
                d3 = 1.0 - p3[i] - p3[j]
                if d2 <= 0 or d3 <= 0: continue
                prob = p1[i] * (p2[j] / d2) * (p3[k] / d3)
                out.append(((int(boats[i]), int(boats[j]), int(boats[k])), prob))
    out.sort(key=lambda x: -x[1])
    return out

if __name__ == "__main__":
    main()
