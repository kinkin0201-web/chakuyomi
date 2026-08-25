# -*- coding: utf-8 -*-
"""UI用データ生成(本命が飛ぶか予測版)。"""
import json, sqlite3, sys
import numpy as np, pandas as pd, lightgbm as lgb
sys.path.insert(0, '.')
from train_upset import build

d = build('up.db')
b = lgb.Booster(model_file='upset_model.txt')
feats = [c for c in d.columns if c not in ('race_id', 'date', 'upset')]
d['p'] = b.predict(d[feats])

date = sys.argv[1] if len(sys.argv) > 1 else d['date'].max()
day = d[d.date == date]

conn = sqlite3.connect('up.db')
det = pd.read_sql("""
 SELECT r.race_id,r.stadium_code,r.stadium_name,r.race_number,r.title,
        r.weather,r.wind_direction,r.wind_speed,r.wave_height,
        r.exact_trifecta_payout,r.exact_trifecta_combo,
        e.boat_number,e.player_name,e.player_class,
        e.win_rate_all,e.motor_win_rate,e.exhibition_time,e.rank
 FROM races r JOIN entries e USING(race_id) WHERE r.date=?
 ORDER BY r.stadium_code,r.race_number,e.boat_number
""", conn, params=(date,)); conn.close()

pm = dict(zip(day.race_id, day.p))
out = {"date": date, "stadiums": {}}
for (jcd, nm), g_st in det.groupby(['stadium_code', 'stadium_name']):
    races = []
    for rno, g in g_st.groupby('race_number'):
        rid = g.race_id.iloc[0]
        if rid not in pm: continue
        p = float(pm[rid])
        r0 = g.iloc[0]
        boats = [{
            "boat": int(x.boat_number), "name": x.player_name, "cls": x.player_class,
            "win": None if pd.isna(x.win_rate_all) else float(x.win_rate_all),
            "motor": None if pd.isna(x.motor_win_rate) else float(x.motor_win_rate),
            "ex": None if pd.isna(x.exhibition_time) else float(x.exhibition_time),
            "actual": None if pd.isna(x.rank) else int(x.rank),
        } for x in g.itertuples()]
        races.append({
            "no": int(rno), "title": r0.title, "upsetP": round(p, 4),
            "weather": r0.weather, "wind": r0.wind_direction,
            "windSpeed": None if pd.isna(r0.wind_speed) else float(r0.wind_speed),
            "wave": None if pd.isna(r0.wave_height) else float(r0.wave_height),
            "payout": None if pd.isna(r0.exact_trifecta_payout) else int(r0.exact_trifecta_payout),
            "combo": r0.exact_trifecta_combo,
            "boats": boats,
        })
    if races:
        out["stadiums"][jcd] = {"name": nm, "races": races}

print(json.dumps(out, ensure_ascii=False, separators=(',', ':')))
