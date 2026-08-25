# -*- coding: utf-8 -*-
"""オッズを毎日蓄積する。

■ なぜ蓄積が必要か
   過去のオッズは公開されていない。期待値ベースの検証には
   「そのとき市場がどう見ていたか」が要るため、今日から貯めるしかない。

■ 保存先
   odds.db に1行=1買い目で保存する。
   締切直前のオッズが最も正確なので、取得時刻も残す。

■ 使い方
   python collect_odds.py --stadiums 22        # 特定の場
   python collect_odds.py                       # 開催中の全場
"""
import argparse, sqlite3, sys, time
from datetime import datetime, timedelta

sys.path.insert(0, '.')
from fetch_odds import fetch
from build_db_bulk import download, parse_b_file
from predict_today import parse_deadlines, _mins_to

DB = "odds.db"


def init_db(conn):
    conn.execute("""
    CREATE TABLE IF NOT EXISTS odds (
        race_id   TEXT NOT NULL,      -- '20260825_22_1'
        combo     TEXT NOT NULL,      -- '1-2-3'
        odds      REAL NOT NULL,
        fetched_at INTEGER NOT NULL,  -- 取得時刻(UNIX秒)
        PRIMARY KEY (race_id, combo, fetched_at)
    )""")
    # 最新のオッズだけ引くための索引
    conn.execute("CREATE INDEX IF NOT EXISTS idx_odds_race ON odds(race_id, fetched_at DESC)")
    conn.commit()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--date", help="YYYY-MM-DD (既定: 本日JST)")
    p.add_argument("--stadiums", help="場コード(カンマ区切り)。省略で開催中の全場")
    p.add_argument("--db", default=DB)
    p.add_argument("--window", type=int, default=0,
                   help="締切まで何分先のレースを取るか(0=全件)")
    a = p.parse_args()

    d = (datetime.strptime(a.date, "%Y-%m-%d").date() if a.date
         else (datetime.utcnow() + timedelta(hours=9)).date())
    hd = d.strftime("%Y%m%d")

    # 開催中の場は番組表から判定する(無駄なアクセスを避ける)
    if a.stadiums:
        targets = a.stadiums.split(",")
        races_by_st = {t: range(1, 13) for t in targets}
    else:
        text = download("B", d)
        if not text:
            print("番組表が未配信です", file=sys.stderr)
            return
        b = parse_b_file(text, hd, None)
        races_by_st = {}
        for rid, rec in b.items():
            races_by_st.setdefault(rec["stadium_code"], []).append(rec["race_number"])

    # 締切が近いレースだけに絞る。
    # オッズは締切直前ほど正確で、遠いレースを取っても意味が薄い。
    if a.window:
        text = download("B", d)
        if text:
            dls = parse_deadlines(text, hd, None)
            filtered = {}
            for jcd, rnos in races_by_st.items():
                keep = []
                for rno in rnos:
                    left = _mins_to(dls.get(f"{hd}_{jcd}_{rno}", ""))
                    if left is not None and -5 < left <= a.window:
                        keep.append(rno)
                if keep:
                    filtered[jcd] = keep
            races_by_st = filtered

    conn = sqlite3.connect(a.db)
    init_db(conn)
    now = int(time.time())
    total = 0

    for jcd, rnos in sorted(races_by_st.items()):
        for rno in sorted(rnos):
            o = fetch(jcd, rno, hd)
            if not o:
                continue
            rid = f"{hd}_{jcd}_{rno}"
            conn.executemany(
                "INSERT OR REPLACE INTO odds VALUES (?,?,?,?)",
                [(rid, f"{k[0]}-{k[1]}-{k[2]}", v, now) for k, v in o.items()])
            conn.commit()
            total += len(o)
            print(f"  {jcd} {rno}R: {len(o)}通り")

    n = conn.execute("SELECT COUNT(DISTINCT race_id) FROM odds").fetchone()[0]
    conn.close()
    print(f"\n{total:,}件を保存 / 累計 {n:,}レース分")


if __name__ == "__main__":
    main()
