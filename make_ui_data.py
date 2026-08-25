# -*- coding: utf-8 -*-
"""UI用の予測データを生成する(実データ)。"""
import json, sqlite3, sys
import numpy as np, pandas as pd, lightgbm as lgb
from train_model import add_features, CAT_FEATURES

DB, MODEL, T = "ui.db", "ui_model.txt", 0.75
date = sys.argv[1] if len(sys.argv) > 1 else None

conn = sqlite3.connect(DB)
if not date:
    date = conn.execute("SELECT MAX(date) FROM races").fetchone()[0]
df = pd.read_sql("""
 SELECT r.race_id,r.date,r.stadium_code,r.stadium_name,r.race_number,r.title,
        r.distance,r.weather,r.wind_direction,r.wind_speed,r.wave_height,
        e.boat_number,e.player_name,e.player_class,e.player_age,
        e.win_rate_all,e.top2_rate_all,e.win_rate_local,e.top2_rate_local,
        e.motor_number,e.motor_win_rate,e.boat_win_rate,e.exhibition_time,e.rank
 FROM races r JOIN entries e USING(race_id)
 WHERE r.date=? ORDER BY r.stadium_code,r.race_number,e.boat_number
""", conn, params=(date,)); conn.close()

feat = add_features(df.copy())
for c in CAT_FEATURES: feat[c] = feat[c].astype("category")
b = lgb.Booster(model_file=MODEL)
df["score"] = b.predict(feat[b.feature_name()])

def softmax(s, t=T):
    s = np.asarray(s)/t; s = s - s.max(); e = np.exp(s); return e/e.sum()

out = {"date": date, "stadiums": {}}
for (jcd, name), g_st in df.groupby(["stadium_code", "stadium_name"]):
    races = []
    for rno, g in g_st.groupby("race_number"):
        g = g.copy()
        g["p"] = softmax(g["score"].to_numpy())
        g = g.sort_values("p", ascending=False)
        boats = [{
            "boat": int(r.boat_number), "name": r.player_name,
            "cls": r.player_class, "p": round(float(r.p), 4),
            "exhibition": None if pd.isna(r.exhibition_time) else float(r.exhibition_time),
            "motor": None if pd.isna(r.motor_win_rate) else float(r.motor_win_rate),
            "winAll": None if pd.isna(r.win_rate_all) else float(r.win_rate_all),
            "actual": None if pd.isna(r.rank) else int(r.rank),
        } for r in g.itertuples()]
        r0 = g.iloc[0]
        races.append({
            "no": int(rno), "title": r0.title,
            "weather": r0.weather, "wind": r0.wind_direction,
            "windSpeed": None if pd.isna(r0.wind_speed) else float(r0.wind_speed),
            "wave": None if pd.isna(r0.wave_height) else float(r0.wave_height),
            "boats": boats,
        })
    out["stadiums"][jcd] = {"name": name, "races": races}

print(json.dumps(out, ensure_ascii=False, separators=(",", ":")))
