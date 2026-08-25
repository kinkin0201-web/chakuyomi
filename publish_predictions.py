# -*- coding: utf-8 -*-
"""予測データを配信用JSONに書き出し、Storageへ上げる準備をする。

APIが期待する形(レースの配列)で出力する。
Firestoreには置かない(読み取り課金が跳ね上がるため)。
"""
import argparse, json, sys
import numpy as np, pandas as pd, lightgbm as lgb, sqlite3
sys.path.insert(0, '.')
from train_upset import build

def main():
    p = argparse.ArgumentParser()
    p.add_argument('--db', default='up.db')
    p.add_argument('--model', default='upset_model.txt')
    p.add_argument('--date')
    p.add_argument('--out')
    a = p.parse_args()

    d = build(a.db, require_result=False)
    b = lgb.Booster(model_file=a.model)
    feats = [c for c in d.columns if c not in ('race_id', 'date', 'upset')]
    d['p'] = b.predict(d[feats])

    date = a.date or d['date'].max()
    day = d[d.date == date]
    if day.empty:
        print(f'{date} のデータがありません', file=sys.stderr)
        sys.exit(1)

    conn = sqlite3.connect(a.db)
    det = pd.read_sql("""
      SELECT r.race_id,r.stadium_code,r.stadium_name,r.race_number,r.title,
             r.weather,r.wind_direction,r.wind_speed,r.wave_height,
             e.boat_number,e.player_name,e.player_class,
             e.win_rate_all,e.motor_win_rate,e.exhibition_time,e.rank
      FROM races r JOIN entries e USING(race_id) WHERE r.date=?
      ORDER BY r.stadium_code,r.race_number,e.boat_number
    """, conn, params=(date,))
    conn.close()

    pm = dict(zip(day.race_id, day.p))
    races = []
    for rid, g in det.groupby('race_id', sort=False):
        if rid not in pm:
            continue
        r0 = g.iloc[0]
        races.append({
            'raceId': rid,
            'stadium': r0.stadium_code,
            'stadiumName': r0.stadium_name,
            'no': int(r0.race_number),
            'title': r0.title,
            'upsetP': round(float(pm[rid]), 4),
            'weather': r0.weather,
            'wind': r0.wind_direction,
            'windSpeed': None if pd.isna(r0.wind_speed) else float(r0.wind_speed),
            'wave': None if pd.isna(r0.wave_height) else float(r0.wave_height),
            'boats': [{
                'boat': int(x.boat_number), 'name': x.player_name,
                'cls': x.player_class,
                'win': None if pd.isna(x.win_rate_all) else float(x.win_rate_all),
                'motor': None if pd.isna(x.motor_win_rate) else float(x.motor_win_rate),
                'ex': None if pd.isna(x.exhibition_time) else float(x.exhibition_time),
                'actual': None if pd.isna(x.rank) else int(x.rank),
            } for x in g.itertuples()],
        })
    races.sort(key=lambda r: (r['stadium'], r['no']))

    out = a.out or f'predictions_{date}.json'
    with open(out, 'w', encoding='utf-8') as f:
        json.dump(races, f, ensure_ascii=False, separators=(',', ':'))
    print(f'{out}: {len(races)}レース')

if __name__ == '__main__':
    main()
