# -*- coding: utf-8 -*-
"""書き出したJSONの推論結果が本家LightGBMと一致するか検証する。"""
import json, sqlite3, numpy as np, pandas as pd, lightgbm as lgb
from train_model import add_features, CAT_FEATURES

M = json.load(open("model.json"))

def walk(node, x):
    while "v" not in node:
        val = x[node["f"]]
        # missing_type='None' のモデルでは欠損は0として扱われる
        if val is None or (isinstance(val, float) and np.isnan(val)):
            val = 0.0
        if node["c"]:
            node = node["l"] if int(val) in node["s"] else node["r"]
        else:
            node = node["l"] if val <= node["t"] else node["r"]
    return node["v"]

def predict(row):
    return sum(walk(t, row) for t in M["trees"])

conn = sqlite3.connect("kyotei_prediction_core.db")
df = pd.read_sql("""
 SELECT r.race_id,r.stadium_code,r.race_number,r.distance,r.weather,
        r.wind_direction,r.wind_speed,r.wave_height,e.boat_number,e.player_class,
        e.player_age,e.win_rate_all,e.top2_rate_all,e.win_rate_local,
        e.top2_rate_local,e.motor_win_rate,e.boat_win_rate,e.exhibition_time
 FROM races r JOIN entries e USING(race_id)
 WHERE r.race_id IN (SELECT race_id FROM races LIMIT 200)
 ORDER BY r.race_id,e.boat_number
""", conn); conn.close()

df = add_features(df)
for c in CAT_FEATURES:
    df[c] = df[c].astype("category")

b = lgb.Booster(model_file="kyotei_model.txt")
feats = b.feature_name()
X = df[feats]
ref = b.predict(X)

# 学習時のカテゴリ順序(model.json)でコード化する
Xn = X.copy()
for c in feats:
    if c in M["categories"]:
        order = {v: i for i, v in enumerate(M["categories"][c])}
        Xn[c] = Xn[c].astype(str).map(order)
rows = Xn.to_numpy(dtype=float)
mine = np.array([predict(r) for r in rows])

diff = np.abs(ref - mine)
print(f"検証件数    : {len(ref):,}")
print(f"最大誤差    : {diff.max():.10f}")
print(f"平均誤差    : {diff.mean():.10f}")
print("判定        :", "一致" if diff.max() < 1e-6 else "不一致")
