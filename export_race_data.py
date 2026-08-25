# -*- coding: utf-8 -*-
"""配信用の予測データJSONを書き出す。

Firestore に置くと読み取り課金が跳ね上がるため、
JSONファイルとして書き出し Cloud Functions 経由で配信する。

場ごと・日ごとに分割することで1ファイルを小さく保ち、
Functions の無料転送枠(5GB/月)に収める。
"""
import argparse, json, os, sqlite3

DB = "kyotei_prediction_core.db"
OUT = "public/data"

# 締切前に判明する項目のみ。着順・配当は含めない(リーク防止)
SQL = """
SELECT r.race_id, r.stadium_code, r.stadium_name, r.race_number, r.title,
       r.distance, r.weather, r.wind_direction, r.wind_speed, r.wave_height,
       e.boat_number, e.player_id, e.player_name, e.player_class, e.player_age,
       e.win_rate_all, e.top2_rate_all, e.win_rate_local, e.top2_rate_local,
       e.motor_number, e.motor_win_rate, e.boat_win_rate, e.exhibition_time
FROM races r JOIN entries e USING(race_id)
WHERE r.date = ? AND r.stadium_code = ?
ORDER BY r.race_number, e.boat_number
"""

def export(db, date, out_dir):
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    stadiums = [r[0] for r in conn.execute(
        "SELECT DISTINCT stadium_code FROM races WHERE date=? ORDER BY 1", (date,))]

    os.makedirs(out_dir, exist_ok=True)
    written = []
    for jcd in stadiums:
        races = {}
        for row in conn.execute(SQL, (date, jcd)):
            rid = row["race_id"]
            if rid not in races:
                races[rid] = {
                    "no": row["race_number"], "title": row["title"],
                    "distance": row["distance"], "weather": row["weather"],
                    "wind_dir": row["wind_direction"], "wind": row["wind_speed"],
                    "wave": row["wave_height"], "entries": [],
                }
            races[rid]["entries"].append({
                "boat": row["boat_number"], "name": row["player_name"],
                "class": row["player_class"], "age": row["player_age"],
                "win_all": row["win_rate_all"], "top2_all": row["top2_rate_all"],
                "win_loc": row["win_rate_local"], "top2_loc": row["top2_rate_local"],
                "motor": row["motor_number"], "motor_top2": row["motor_win_rate"],
                "boat_top2": row["boat_win_rate"], "exhibition": row["exhibition_time"],
            })
        if not races:
            continue
        path = os.path.join(out_dir, f"{date.replace('-','')}_{jcd}.json")
        # 空白を詰めて転送量を最小化する
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"date": date, "stadium": jcd, "races": races},
                      f, ensure_ascii=False, separators=(",", ":"))
        written.append((path, os.path.getsize(path)))
    conn.close()
    return written

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--date", required=True)
    p.add_argument("--db", default=DB)
    p.add_argument("--out", default=OUT)
    a = p.parse_args()
    files = export(a.db, a.date, a.out)
    total = sum(s for _, s in files)
    for path, size in files:
        print(f"  {os.path.basename(path):<24} {size/1024:>6.1f} KB")
    print(f"\n{len(files)}場 / 合計 {total/1024:.1f} KB / 平均 {total/len(files)/1024:.1f} KB")
