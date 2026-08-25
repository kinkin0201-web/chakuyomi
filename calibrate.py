# -*- coding: utf-8 -*-
"""softmax温度の校正とキャリブレーション検証。

「勝率70%」と表示したなら、実際に約70%勝たなければ商品として嘘になる。
温度パラメータを最適化し、予測確率と実測勝率の一致度を検証する。
"""
import sqlite3, numpy as np, pandas as pd, lightgbm as lgb
from train_model import add_features, CAT_FEATURES

conn = sqlite3.connect("snap2.db")
df = pd.read_sql("""
 SELECT r.race_id, r.date, r.stadium_code, r.race_number, r.distance,
        r.weather, r.wind_direction, r.wind_speed, r.wave_height,
        e.boat_number, e.player_class, e.player_age, e.win_rate_all,
        e.top2_rate_all, e.win_rate_local, e.top2_rate_local,
        e.motor_win_rate, e.boat_win_rate, e.exhibition_time, e.rank
 FROM races r JOIN entries e USING(race_id) ORDER BY r.date, r.race_id, e.boat_number
""", conn); conn.close()

df = df[df["rank"].notna()].copy()
df["is_win"] = (df["rank"] == 1).astype(int)
df = add_features(df)
for c in CAT_FEATURES: df[c] = df[c].astype("category")

days = np.sort(df["date"].unique())
test = df[df["date"] >= days[-7]].copy()

b = lgb.Booster(model_file="kyotei_model.txt")
test["score"] = b.predict(test[b.feature_name()])

def probs(g, T):
    s = g["score"].to_numpy()/T; s -= s.max(); e = np.exp(s); return e/e.sum()

# 対数尤度が最大になる温度を探索
best=(None,-1e18)
for T in np.arange(0.4, 3.05, 0.05):
    ll=0.0
    for _, g in test.groupby("race_id", sort=False):
        p = probs(g, T); y = g["is_win"].to_numpy()
        ll += np.log(max(p[y==1][0], 1e-12))
    if ll > best[1]: best=(T, ll)
T = best[0]
print(f"最適温度 T = {T:.2f}  (対数尤度 {best[1]:,.0f})")

test["p"] = np.concatenate([probs(g,T) for _,g in test.groupby("race_id", sort=False)])

print("\n=== キャリブレーション検証 ===")
print("予測勝率帯       件数   予測平均   実測勝率")
bins=[0,.05,.1,.2,.3,.5,.7,1.01]
for lo,hi in zip(bins[:-1],bins[1:]):
    m=(test["p"]>=lo)&(test["p"]<hi)
    if m.sum()<20: continue
    print(f"{lo:>5.0%}-{hi:<5.0%} {m.sum():>7,}   {test.loc[m,'p'].mean():>7.1%}   {test.loc[m,'is_win'].mean():>7.1%}")
