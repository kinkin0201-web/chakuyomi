# -*- coding: utf-8 -*-
"""
競艇予測システム Phase 1 (一括DL版) : データ収集・蓄積基盤

日本モーターボート競走会が配布する公式一括ファイルを取得し、
SQLite3 (kyotei_prediction_core.db) へ格納する。

  B ファイル (番組表) : モーター番号 / モーター2連対率 / 選手情報
  K ファイル (競走成績): 着順 / 展示タイム / 気象 / 3連単配当

1日1ファイルに全24場・全12Rが収録されているため、
スクレイピング版(1日あたり864リクエスト)に対し1日2リクエストで済む。
"""

import argparse
import io
import re
import sqlite3
import time
import warnings
from datetime import date, datetime, timedelta

import requests

try:
    import lhafile
except ImportError:
    raise SystemExit("lhafile が必要です:  pip install lhafile")

# ===================== 設定 =====================
DB_PATH = "kyotei_prediction_core.db"

# 取得対象の場コード。None なら全24場を格納する。
# 例: 福岡のみなら ["22"]
TARGET_STADIUMS = None

START_DATE = date(2026, 7, 1)
END_DATE = date(2026, 7, 31)

SLEEP_SEC = 3          # サーバ負荷軽減(必須)
TIMEOUT = 30
MAX_RETRY = 2

BASE = "https://www1.mbrace.or.jp/od2"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ja,en-US;q=0.9",
}

# 場コード -> 場名
STADIUMS = {
    "01": "桐生", "02": "戸田", "03": "江戸川", "04": "平和島", "05": "多摩川",
    "06": "浜名湖", "07": "蒲郡", "08": "常滑", "09": "津", "10": "三国",
    "11": "びわこ", "12": "住之江", "13": "尼崎", "14": "鳴門", "15": "丸亀",
    "16": "児島", "17": "宮島", "18": "徳山", "19": "下関", "20": "若松",
    "21": "芦屋", "22": "福岡", "23": "唐津", "24": "大村",
}
# ================================================


# ===================== DB =====================
def init_db(conn):
    """テーブルを作成。race_id で races と entries を紐付ける。"""
    cur = conn.cursor()
    cur.execute("PRAGMA foreign_keys = ON")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS races (
        race_id      TEXT PRIMARY KEY,   -- '20260722_22_1' = 日付_場コード_R
        date         TEXT NOT NULL,
        stadium_code TEXT NOT NULL,
        stadium_name TEXT,
        race_number  INTEGER NOT NULL,
        title        TEXT,               -- 予選/準優勝戦 など
        kimarite     TEXT,               -- 決まり手(逃げ/差し/まくり等)
        note         TEXT,               -- 進入固定 など特記事項
        distance     INTEGER,            -- m
        weather      TEXT,
        wind_direction TEXT,             -- 北/南西 など(公式表記)
        wind_speed   REAL,               -- m
        wave_height  REAL,               -- cm
        exact_trifecta_payout INTEGER,   -- 3連単配当(円) 不成立はNULL
        exact_trifecta_combo  TEXT       -- 3連単の組番 '3-4-5'
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS entries (
        race_id         TEXT NOT NULL,
        boat_number     INTEGER NOT NULL,   -- 枠番(艇番)
        player_id       TEXT,               -- 選手登録番号
        player_name     TEXT,
        player_age      INTEGER,
        player_branch   TEXT,
        player_class    TEXT,               -- A1/A2/B1/B2
        win_rate_all    REAL,               -- 全国勝率
        top2_rate_all   REAL,               -- 全国2連対率
        win_rate_local  REAL,               -- 当地勝率
        top2_rate_local REAL,               -- 当地2連対率
        motor_number    INTEGER,
        motor_win_rate  REAL,               -- モーター2連対率
        boat_number_id  INTEGER,            -- ボート番号
        boat_win_rate   REAL,               -- ボート2連対率
        exhibition_time REAL,               -- 展示タイム
        start_course    INTEGER,            -- 進入コース
        start_timing    REAL,               -- STタイミング
        rank            INTEGER,            -- 着順1-6 (失格/欠場はNULL)
        rank_status     TEXT,               -- 正常時NULL, 失格はS0/S1/S2, 欠場K0/K1
        PRIMARY KEY (race_id, boat_number),
        FOREIGN KEY (race_id) REFERENCES races(race_id)
    )
    """)

    cur.execute("CREATE INDEX IF NOT EXISTS idx_races_date ON races(date, stadium_code)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_entries_player ON entries(player_id)")
    conn.commit()


def upsert_race(conn, r):
    """races をUPSERT。再実行しても重複せず最新値で上書きされる(冪等性)。"""
    conn.execute("""
        INSERT INTO races (race_id, date, stadium_code, stadium_name, race_number,
                           title, note, distance, weather, wind_direction, wind_speed,
                           wave_height, kimarite,
                           exact_trifecta_payout, exact_trifecta_combo)
        VALUES (:race_id, :date, :stadium_code, :stadium_name, :race_number,
                :title, :note, :distance, :weather, :wind_direction, :wind_speed,
                :wave_height, :kimarite, :exact_trifecta_payout, :exact_trifecta_combo)
        ON CONFLICT(race_id) DO UPDATE SET
            stadium_name = excluded.stadium_name,
            title        = excluded.title,
            kimarite     = COALESCE(excluded.kimarite, races.kimarite),
            note         = COALESCE(excluded.note, races.note),
            distance     = excluded.distance,
            weather      = excluded.weather,
            wind_direction = excluded.wind_direction,
            wind_speed   = excluded.wind_speed,
            wave_height  = excluded.wave_height,
            -- B(番組表)先行投入時のNULLを、K(成績)の値で上書きする。
            -- ただしKにも無い場合は既存値を消さない。
            exact_trifecta_payout = COALESCE(excluded.exact_trifecta_payout,
                                             races.exact_trifecta_payout),
            exact_trifecta_combo  = COALESCE(excluded.exact_trifecta_combo,
                                             races.exact_trifecta_combo)
    """, r)


# entries の更新対象列 (race_id, boat_number 以外)
_ENTRY_COLS = [
    "player_id", "player_name", "player_age", "player_branch", "player_class",
    "win_rate_all", "top2_rate_all", "win_rate_local", "top2_rate_local",
    "motor_number", "motor_win_rate", "boat_number_id", "boat_win_rate",
    "exhibition_time", "start_course", "start_timing", "rank", "rank_status",
]


def upsert_entry(conn, e):
    """entries をUPSERT。

    B と K は別ファイルで、それぞれが持つ項目が異なる。
    後から来た側のNULLで既存値を消さないよう COALESCE で合成する。
    """
    cols = ", ".join(_ENTRY_COLS)
    binds = ", ".join(f":{c}" for c in _ENTRY_COLS)
    updates = ", ".join(
        f"{c} = COALESCE(excluded.{c}, entries.{c})" for c in _ENTRY_COLS
    )
    row = {c: e.get(c) for c in _ENTRY_COLS}
    row["race_id"] = e["race_id"]
    row["boat_number"] = e["boat_number"]
    conn.execute(f"""
        INSERT INTO entries (race_id, boat_number, {cols})
        VALUES (:race_id, :boat_number, {binds})
        ON CONFLICT(race_id, boat_number) DO UPDATE SET {updates}
    """, row)


# ===================== ダウンロード =====================
def download(kind, d):
    """B or K のLZHを取得し、中のテキストを返す。無い日は None。"""
    ym = d.strftime("%Y%m")
    ymd = d.strftime("%y%m%d")
    url = f"{BASE}/{kind}/{ym}/{kind.lower()}{ymd}.lzh"

    for attempt in range(MAX_RETRY + 1):
        try:
            res = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
            time.sleep(SLEEP_SEC)
            if res.status_code == 404:
                return None                      # 開催が無い日など
            if res.status_code != 200:
                print(f"    [WARN] HTTP {res.status_code}: {url}")
                return None

            # LZHを解凍(1アーカイブ1テキスト)
            arc = lhafile.Lhafile(io.BytesIO(res.content))
            names = arc.namelist()
            if not names:
                print(f"    [WARN] 空のアーカイブ: {url}")
                return None
            # 公式ファイルはCP932。壊れた文字があっても止めない。
            return arc.read(names[0]).decode("cp932", errors="replace")

        except Exception as e:
            time.sleep(SLEEP_SEC)
            if attempt == MAX_RETRY:
                print(f"    [WARN] 取得失敗 {url}: {type(e).__name__}: {e}")
                return None
            print(f"    [RETRY] {kind} {ymd} ({attempt + 1}/{MAX_RETRY})")
    return None


# ===================== パース補助 =====================
def to_num(text, cast=float):
    """数値化できなければ None (DB上はNULL)。"""
    if text is None:
        return None
    m = re.search(r"-?\d+(?:\.\d+)?", str(text))
    if not m:
        return None
    try:
        return cast(float(m.group()))
    except (TypeError, ValueError):
        return None


def _parse_st(text):
    """STタイミングを数値化。'F0.03' は負値(-0.03)として扱う。"""
    if not text:
        return None
    t = text.strip()
    v = to_num(t)
    if v is None:
        return None
    return -v if t.upper().startswith("F") else v


def clean_name(s):
    """全角スペース埋めの文字列を正規化。

    K(成績)の氏名は '武　井　　莉里佳' のように全角スペースで
    桁揃えされている。姓名の区切り(2個以上)は半角スペース1つに、
    文字間の1個は詰めて '武井 莉里佳' に整える。
    """
    if not s:
        return ""
    s = s.strip("　 ")
    # 全角スペース2個以上 = 姓名の区切り -> 半角スペース
    s = re.sub(r"[　 ]{2,}", "\x00", s)
    # 残った1個は文字間の桁揃え -> 削除
    s = s.replace("　", "").replace(" ", "")
    return " ".join(s.replace("\x00", " ").split())


def sjis_slice(line, start, end):
    """CP932のバイト位置で切り出す(固定長レイアウトのため)。"""
    try:
        return line.encode("cp932", errors="replace")[start:end].decode(
            "cp932", errors="replace"
        )
    except Exception:
        return ""


# ===================== B (番組表) =====================
# 場ブロック: '22BBGN' 〜 '22BEND'
RE_B_BEGIN = re.compile(r"^(\d{2})BBGN")
# レース見出し: '　１Ｒ  予選 ...' / '１０Ｒ  予選 ...'
# 1桁レースは先頭が全角スペース、2桁レースは0桁目から始まるため
# 先頭の全角スペースは「あってもなくてもよい」扱いにする。
RE_B_RACE = re.compile(r"^　?([０-９\d]{1,2})Ｒ\s+(\S*)")
# 出走行: '1 5092篠原晟弥25福岡51A2 6.25 ...'
RE_B_ENTRY = re.compile(r"^([1-6])\s(\d{4})")

ZEN2HAN = str.maketrans("０１２３４５６７８９", "0123456789")


def parse_b_file(text, date_str, targets):
    """番組表テキスト -> {race_id: {...}} を返す。"""
    out = {}
    jcd = None
    rno = None
    title = None

    for line in text.splitlines():
        m = RE_B_BEGIN.match(line)
        if m:
            jcd = m.group(1)
            rno = None
            continue
        if line.startswith(("BEND", "\x1a")) or "BEND" in line[:8]:
            jcd = None
            continue
        if jcd is None:
            continue
        if targets and jcd not in targets:
            continue

        m = RE_B_RACE.match(line)
        if m:
            rno = to_num(m.group(1).translate(ZEN2HAN), int)
            title = clean_name(m.group(2)) or None
            continue

        if rno is None:
            continue

        m = RE_B_ENTRY.match(line)
        if not m:
            continue

        # 固定長(CP932バイト単位)で切り出す
        race_id = f"{date_str}_{jcd}_{rno}"
        rec = out.setdefault(race_id, {
            "race_id": race_id, "stadium_code": jcd,
            "race_number": rno, "title": title, "entries": [],
        })
        rec["entries"].append({
            "race_id": race_id,
            "boat_number": int(m.group(1)),
            "player_id": sjis_slice(line, 2, 6).strip() or None,
            "player_name": clean_name(sjis_slice(line, 6, 14)) or None,
            "player_age": to_num(sjis_slice(line, 14, 16), int),
            "player_branch": clean_name(sjis_slice(line, 16, 20)) or None,
            "player_class": sjis_slice(line, 22, 24).strip() or None,
            "win_rate_all": to_num(sjis_slice(line, 24, 29)),
            "top2_rate_all": to_num(sjis_slice(line, 29, 35)),
            "win_rate_local": to_num(sjis_slice(line, 35, 40)),
            "top2_rate_local": to_num(sjis_slice(line, 40, 46)),
            "motor_number": to_num(sjis_slice(line, 46, 49), int),
            "motor_win_rate": to_num(sjis_slice(line, 49, 55)),
            "boat_number_id": to_num(sjis_slice(line, 55, 58), int),
            "boat_win_rate": to_num(sjis_slice(line, 58, 64)),
        })
    return out


# ===================== K (競走成績) =====================
RE_K_BEGIN = re.compile(r"^(\d{2})KBGN")
# 払戻行: '   1R  3-4-5    6280  ...'  / 不成立あり
RE_K_PAYOUT = re.compile(r"^\s+(\d{1,2})R\s+(\S+)\s+(\S+)")
# 気象行: '   1R  予選   H1800m  晴  風  北  3m  波  3cm'
# レース名と距離の間に '進入固定' 等の注記が入る場合があるため、
# 間の任意文字列を許容する(注記は kimarite 列ではなくレース属性)。
RE_K_COND = re.compile(
    r"^\s+(\d{1,2})R\s+(\S*?)\s+(.*?)\s*H(\d+)m\s+(\S+?)\s*風\s+(\S+?)\s+(\d+)m\s+波\s+(\d+)cm"
)
# 結果行: '  01  3 5188 武　井　　莉里佳 50  124  6.87   3    0.13   1.51.6'
#
# 1桁目の着順欄には以下が入りうる:
#   01〜06 = 着順
#   F      = フライング, L = 出遅れ
#   S0/S1/S2 = 失格(妨害/選手責任/責任外)
#   K0/K1  = 欠場
# またSTタイミングは 'F0.03' のようにF/L接頭辞が付く場合がある。
# 着順ヘッダの末尾に決まり手が入る: '... ﾚｰｽﾀｲﾑ 逃げ'
RE_K_KIMARITE = re.compile(r"ﾚｰｽﾀｲﾑ\s*(\S+)")

RE_K_RESULT = re.compile(
    r"^\s{2}(\d{2}|[SK]\d|[FL])\s+([1-6])\s+(\d{4})\s+(.+?)\s+(\d+)\s+(\d+)\s+"
    r"([\d.]+|\s*)\s*(\d?)\s*([FL]?[\d.]*)"
)


def parse_k_file(text, date_str, targets):
    """成績テキスト -> {race_id: {...}} を返す。"""
    out = {}
    jcd = None
    rno = None
    payouts = {}

    for line in text.splitlines():
        m = RE_K_BEGIN.match(line)
        if m:
            jcd = m.group(1)
            rno = None
            payouts = {}
            continue
        if jcd is None:
            continue
        if targets and jcd not in targets:
            continue

        # --- 気象行(レースの開始を兼ねる) ---
        m = RE_K_COND.match(line)
        if m:
            rno = to_num(m.group(1), int)
            race_id = f"{date_str}_{jcd}_{rno}"
            combo, yen = payouts.get(rno, (None, None))
            out[race_id] = {
                "race_id": race_id, "date": date_str,
                "stadium_code": jcd, "stadium_name": STADIUMS.get(jcd),
                "race_number": rno,
                "title": clean_name(m.group(2)) or None,
                "note": clean_name(m.group(3)) or None,   # 進入固定 など
                "distance": to_num(m.group(4), int),
                "weather": clean_name(m.group(5)) or None,
                "wind_direction": clean_name(m.group(6)) or None,
                "wind_speed": to_num(m.group(7)),
                "wave_height": to_num(m.group(8)),
                "exact_trifecta_combo": combo,
                "exact_trifecta_payout": yen,
                "kimarite": None,
                "entries": [],
            }
            continue

        # 決まり手(気象行の次の見出し行にある)
        if rno is not None:
            race_id = f"{date_str}_{jcd}_{rno}"
            if race_id in out and out[race_id].get("kimarite") is None:
                mk = RE_K_KIMARITE.search(line)
                if mk:
                    k = mk.group(1).replace("\u3000", "").strip()
                    if k and not k.startswith("-"):
                        out[race_id]["kimarite"] = k

        # --- 払戻表(気象行より前に出るので先に貯める) ---
        m = RE_K_PAYOUT.match(line)
        if m and "-" in m.group(2) or (m and "不成立" in m.group(2)):
            r = to_num(m.group(1), int)
            combo = m.group(2)
            if "不成立" in combo:
                payouts[r] = (None, None)     # 3連単不成立
            else:
                payouts[r] = (combo, to_num(m.group(3), int))
            continue

        # --- 着順行 ---
        if rno is None:
            continue
        m = RE_K_RESULT.match(line)
        if not m:
            continue
        race_id = f"{date_str}_{jcd}_{rno}"
        if race_id not in out:
            continue

        code = m.group(1)
        # 数字なら着順、S0/S1/S2(失格)・K0/K1(欠場)は rank=NULL。
        # '00' はレース不成立を示す特殊コードで着順ではない。
        if code == "00":
            rank, status = None, "00"
        elif code.isdigit():
            rank, status = int(code), None
        else:
            rank, status = None, code

        out[race_id]["entries"].append({
            "race_id": race_id,
            "boat_number": int(m.group(2)),
            "player_id": m.group(3),
            "player_name": clean_name(m.group(4)),
            "motor_number": to_num(m.group(5), int),
            "boat_number_id": to_num(m.group(6), int),
            "exhibition_time": to_num(m.group(7)),
            "start_course": to_num(m.group(8), int),
            # 'F0.03'(フライング)は負のSTとして記録する
            "start_timing": _parse_st(m.group(9)),
            "rank": rank,
            "rank_status": status,
        })
    return out


# ===================== メイン =====================
def process_day(conn, d, targets):
    """1日分(B+K)を取得してDBへ格納。格納した出走数を返す。"""
    date_str = d.strftime("%Y%m%d")
    date_iso = d.strftime("%Y-%m-%d")

    b_text = download("B", d)
    k_text = download("K", d)

    if not b_text and not k_text:
        print("    -> 開催なし / ファイル未配信")
        return 0

    b_races = {}
    k_races = {}
    # ファイル単位のtry-except: 片方が壊れていても他方は活かす
    try:
        if b_text:
            b_races = parse_b_file(b_text, date_str, targets)
    except Exception as e:
        print(f"    [SKIP] B解析失敗 {date_str}: {type(e).__name__}: {e}")
    try:
        if k_text:
            k_races = parse_k_file(k_text, date_str, targets)
    except Exception as e:
        print(f"    [SKIP] K解析失敗 {date_str}: {type(e).__name__}: {e}")

    count = 0
    # B と K の和集合をレース単位で処理
    for race_id in sorted(set(b_races) | set(k_races)):
        try:
            b = b_races.get(race_id)
            k = k_races.get(race_id)
            src = k or b

            race_row = {
                "race_id": race_id,
                "date": date_iso,
                "stadium_code": src["stadium_code"],
                "stadium_name": STADIUMS.get(src["stadium_code"]),
                "race_number": src["race_number"],
                "title": (k or b).get("title"),
                "note": (k or {}).get("note"),
                "distance": (k or {}).get("distance"),
                "weather": (k or {}).get("weather"),
                "wind_direction": (k or {}).get("wind_direction"),
                "wind_speed": (k or {}).get("wind_speed"),
                "wave_height": (k or {}).get("wave_height"),
                "kimarite": (k or {}).get("kimarite"),
                "exact_trifecta_payout": (k or {}).get("exact_trifecta_payout"),
                "exact_trifecta_combo": (k or {}).get("exact_trifecta_combo"),
            }
            upsert_race(conn, race_row)

            # B(番組表)を先に入れ、K(結果)で上書き合成する
            for e in (b or {}).get("entries", []):
                upsert_entry(conn, e)
            for e in (k or {}).get("entries", []):
                upsert_entry(conn, e)
                count += 1
            if not k:
                count += len((b or {}).get("entries", []))

        except Exception as e:
            # 1レース失敗しても全体は止めない
            print(f"    [SKIP] {race_id}: {type(e).__name__}: {e}")
            continue

    conn.commit()   # 日単位でコミット(中断しても取得済みは保全)
    return count


def main():
    p = argparse.ArgumentParser(description="競艇データ一括取込 (Phase 1)")
    p.add_argument("--start", help="開始日 YYYY-MM-DD")
    p.add_argument("--end", help="終了日 YYYY-MM-DD")
    p.add_argument("--stadiums", help="場コードをカンマ区切り指定 (例 22,24)")
    p.add_argument("--db", default=DB_PATH)
    args = p.parse_args()

    start = datetime.strptime(args.start, "%Y-%m-%d").date() if args.start else START_DATE
    end = datetime.strptime(args.end, "%Y-%m-%d").date() if args.end else END_DATE
    targets = set(args.stadiums.split(",")) if args.stadiums else (
        set(TARGET_STADIUMS) if TARGET_STADIUMS else None
    )

    conn = sqlite3.connect(args.db)
    init_db(conn)

    total = 0
    days = (end - start).days + 1
    print(f"取得期間: {start} 〜 {end} ({days}日)")
    print(f"対象場  : {'全24場' if not targets else ','.join(sorted(targets))}")
    print(f"推定時間: 約{days * 2 * SLEEP_SEC / 60:.1f}分\n")

    try:
        current = start
        while current <= end:
            print(f"[{current}] 取得中...")
            try:
                n = process_day(conn, current, targets)
                if n:
                    print(f"    -> {n} 件")
                total += n
            except Exception as e:
                # 日単位のtry-except
                print(f"    [SKIP] {current}: {type(e).__name__}: {e}")
                conn.rollback()
            current += timedelta(days=1)
    except KeyboardInterrupt:
        print("\n中断されました。取得済みデータは保存されています。")
    finally:
        conn.close()

    print(f"\n完了: 計 {total} 件を {args.db} に格納しました。")


if __name__ == "__main__":
    main()
