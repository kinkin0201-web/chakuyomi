# -*- coding: utf-8 -*-
"""
競艇予測システム Phase 1 : データ収集・蓄積基盤 (MVP)

ボートレース公式サイトから出走表・直前情報・結果を取得し、
SQLite3 (kyotei_prediction_core.db) の races / entries に格納する。

将来的な全会場・複数年への拡張を想定し、期間と場コードは変数化している。
"""

import re
import sqlite3
import time
import warnings
from datetime import date, timedelta

import requests
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

# 公式サイトはXHTML宣言付きのため、lxmlのHTMLパーサ利用時の警告を抑制
warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

# ===================== 設定 =====================
DB_PATH = "kyotei_prediction_core.db"

# 場コード: 22 = 福岡。将来はリストに複数追加するだけで全会場対応可能。
STADIUM_CODES = ["22"]

START_DATE = date(2026, 7, 1)     # 取得開始日
END_DATE = date(2026, 7, 31)      # 取得終了日
RACE_NUMBERS = range(1, 13)       # 1R〜12R

SLEEP_SEC = 3                     # BAN対策: リクエスト毎の待機秒数(必須)
TIMEOUT = 20
MAX_RETRY = 2                     # 通信エラー時の再試行回数

BASE = "https://www.boatrace.jp/owpc/pc/race"

# User-Agent偽装: 一般的なChromeを名乗る
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ja,en-US;q=0.9",
}
# ================================================


# ---------------------------------------------------------------
# 風向きコードの対応表
#   公式サイトは風向きをテキストではなくCSSクラス(is-windN)の
#   矢印アイコンで表現している。実際のアイコン画像を確認した結果、
#   1=上, 5=右, 9=下, 13=左 の16方位(22.5度刻み・時計回り)、
#   17=無風 であることを確認済み。
#
#   ※この矢印は「競技場のレイアウト基準」で描画されており、
#     真北基準の方位とは限らない。学習時は文字列ラベルよりも
#     数値コード(wind_direction_code)を特徴量に使う方が安全。
# ---------------------------------------------------------------
WIND_LABELS = [
    "上", "上右上", "右上", "右右上", "右", "右右下", "右下", "下右下",
    "下", "下左下", "左下", "左左下", "左", "左左上", "左上", "上左上",
]


def wind_direction_from_class(class_list):
    """CSSクラス ['weather1_bodyUnitImage','is-wind10'] -> (コード, ラベル)"""
    if not class_list:
        return None, None
    for cls in class_list:
        m = re.fullmatch(r"is-wind(\d+)", cls)
        if m:
            code = int(m.group(1))
            if code == 17:
                return code, "無風"
            if 1 <= code <= 16:
                return code, WIND_LABELS[code - 1]
            return code, None
    return None, None


# ===================== DB =====================
def init_db(conn):
    """テーブルを作成する。race_id で races と entries を紐付ける。"""
    cur = conn.cursor()

    # 外部キー制約を有効化 (SQLiteは既定で無効)
    cur.execute("PRAGMA foreign_keys = ON")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS races (
        race_id             TEXT PRIMARY KEY,   -- '20260824_22_12' = 日付_場コード_R
        date                TEXT NOT NULL,
        stadium_code        TEXT NOT NULL,
        race_number         INTEGER NOT NULL,
        weather             TEXT,
        wind_direction      TEXT,               -- 方位ラベル(矢印基準)
        wind_direction_code INTEGER,            -- 生のアイコンコード(学習用)
        wind_speed          REAL,               -- m
        wave_height         REAL                -- cm
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS entries (
        race_id               TEXT NOT NULL,
        boat_number           INTEGER NOT NULL,
        player_name           TEXT,
        motor_number          INTEGER,
        motor_win_rate        REAL,             -- モーター2連対率(%)
        exhibition_time       REAL,             -- 展示タイム
        rank                  INTEGER,          -- 着順(失格/欠場はNULL)
        exact_trifecta_payout INTEGER,          -- 3連単配当(円)
        -- race_id + boat_number の複合主キーで一意性を担保
        PRIMARY KEY (race_id, boat_number),
        FOREIGN KEY (race_id) REFERENCES races(race_id)
    )
    """)

    # 日付・会場での絞り込みを高速化 (将来のデータ量増加に備える)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_races_date ON races(date, stadium_code)")
    conn.commit()


def upsert_race(conn, r):
    """races をUPSERT。同じrace_idを再実行しても重複せず最新値で上書きされる。"""
    conn.execute("""
        INSERT INTO races (race_id, date, stadium_code, race_number,
                           weather, wind_direction, wind_direction_code,
                           wind_speed, wave_height)
        VALUES (:race_id, :date, :stadium_code, :race_number,
                :weather, :wind_direction, :wind_direction_code,
                :wind_speed, :wave_height)
        ON CONFLICT(race_id) DO UPDATE SET
            weather             = excluded.weather,
            wind_direction      = excluded.wind_direction,
            wind_direction_code = excluded.wind_direction_code,
            wind_speed          = excluded.wind_speed,
            wave_height         = excluded.wave_height
    """, r)


def upsert_entry(conn, e):
    """entries をUPSERT。(race_id, boat_number) が重複キー。"""
    conn.execute("""
        INSERT INTO entries (race_id, boat_number, player_name, motor_number,
                             motor_win_rate, exhibition_time, rank,
                             exact_trifecta_payout)
        VALUES (:race_id, :boat_number, :player_name, :motor_number,
                :motor_win_rate, :exhibition_time, :rank,
                :exact_trifecta_payout)
        ON CONFLICT(race_id, boat_number) DO UPDATE SET
            player_name           = excluded.player_name,
            motor_number          = excluded.motor_number,
            motor_win_rate        = excluded.motor_win_rate,
            exhibition_time       = excluded.exhibition_time,
            rank                  = excluded.rank,
            exact_trifecta_payout = excluded.exact_trifecta_payout
    """, e)


# ===================== 取得 =====================
def fetch(page, rno, jcd, hd):
    """1ページ取得。必ずsleepを挟み、通信エラーは数回リトライする。

    開催の無い日や中止レースは公式トップへリダイレクトされるため、
    目的のテーブルが存在せず呼び出し側で空扱いになる。
    """
    url = f"{BASE}/{page}?rno={rno}&jcd={jcd}&hd={hd}"
    for attempt in range(MAX_RETRY + 1):
        try:
            res = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
            time.sleep(SLEEP_SEC)          # 成功時のウェイト
            if res.status_code != 200:
                print(f"    [WARN] HTTP {res.status_code}: {page} {hd} {rno}R")
                return None
            res.encoding = res.apparent_encoding or "utf-8"
            return BeautifulSoup(res.text, "lxml")
        except Exception as e:
            time.sleep(SLEEP_SEC)          # 失敗時も必ずウェイト
            if attempt == MAX_RETRY:
                print(f"    [WARN] request failed {page} {hd} {rno}R: {e}")
                return None
            print(f"    [RETRY] {page} {hd} {rno}R ({attempt + 1}/{MAX_RETRY})")
    return None


def to_num(text, cast=float):
    """数値化できなければNone(DB上はNULL)を返す。"""
    if text is None:
        return None
    m = re.search(r"-?\d+(?:\.\d+)?", str(text))
    if not m:
        return None
    try:
        return cast(float(m.group()))
    except (TypeError, ValueError):
        return None


def parse_weather(soup):
    """直前情報ページの水面気象情報を辞書で返す。"""
    info = {
        "weather": None, "wind_direction": None, "wind_direction_code": None,
        "wind_speed": None, "wave_height": None,
    }
    if soup is None:
        return info
    box = soup.select_one(".weather1")
    if box is None:
        return info

    # 天候 (晴/曇り/雨/雪など) はラベルのテキスト
    el = box.select_one(".is-weather .weather1_bodyUnitLabelTitle")
    if el:
        info["weather"] = el.get_text(strip=True)

    # 風向きはアイコンのCSSクラスから判定
    el = box.select_one(".is-windDirection .weather1_bodyUnitImage")
    if el:
        code, label = wind_direction_from_class(el.get("class"))
        info["wind_direction_code"] = code
        info["wind_direction"] = label

    # 風速 "3m" / 波高 "3cm" は数値のみ抽出
    el = box.select_one(".is-wind .weather1_bodyUnitLabelData")
    info["wind_speed"] = to_num(el.get_text(strip=True) if el else None)

    el = box.select_one(".is-wave .weather1_bodyUnitLabelData")
    info["wave_height"] = to_num(el.get_text(strip=True) if el else None)

    return info


def parse_racelist(soup):
    """出走表ページ -> {枠番: モーター番号/モーター2連対率}"""
    out = {}
    if soup is None:
        return out
    tables = soup.select("table")
    if len(tables) < 2:
        return out
    for tbody in tables[1].select("tbody"):
        rows = tbody.select("tr")
        if not rows:
            continue
        tds = rows[0].select("td")
        # 想定の列数に満たない行(中止表記など)はスキップ
        if len(tds) < 7:
            continue
        lane = to_num(tds[0].get_text(strip=True), int)
        if lane is None:
            continue
        # index 6 がモーター列: [No, 2連率, 3連率]
        motor = tds[6].get_text("|", strip=True).split("|")
        out[lane] = {
            "motor_number": to_num(motor[0], int) if len(motor) > 0 else None,
            "motor_win_rate": to_num(motor[1]) if len(motor) > 1 else None,
        }
    return out


def parse_beforeinfo(soup):
    """直前情報ページ -> {枠番: 選手名/展示タイム}"""
    out = {}
    if soup is None:
        return out
    table = soup.select_one("table.is-w748")
    if table is None:
        return out
    for tbody in table.select("tbody"):
        rows = tbody.select("tr")
        if not rows:
            continue
        tds = rows[0].select("td")
        if len(tds) < 5:
            continue
        lane = to_num(tds[0].get_text(strip=True), int)
        if lane is None:
            continue
        out[lane] = {
            # 選手名は全角スペース区切りのため半角1つに正規化
            "player_name": " ".join(tds[2].get_text(strip=True).split()),
            "exhibition_time": to_num(tds[4].get_text(strip=True)),  # index4=展示タイム
        }
    return out


# 着順の全角数字 -> 数値 (失格・欠場・転覆などは変換対象外=None)
RANK_MAP = {"１": 1, "２": 2, "３": 3, "４": 4, "５": 5, "６": 6}


def parse_result(soup):
    """結果ページ -> ({枠番: 着順}, 3連単配当)"""
    ranks, payout = {}, None
    if soup is None:
        return ranks, payout

    for table in soup.select("table.is-w495"):
        head = [th.get_text(strip=True) for th in table.select("thead th")]

        # 着順テーブル: 着 / 枠 / ボートレーサー / レースタイム
        if "着" in head and "枠" in head:
            for tr in table.select("tbody tr"):
                tds = tr.select("td")
                if len(tds) < 2:
                    continue
                lane = to_num(tds[1].get_text(strip=True), int)
                if lane is None:
                    continue    # 枠が数値でない行(注記など)は無視
                ranks[lane] = RANK_MAP.get(tds[0].get_text(strip=True))

        # 払戻金テーブル: 勝式 / 組番 / 払戻金 / 人気
        elif "勝式" in head:
            for tr in table.select("tbody tr"):
                tds = tr.select("td")
                if len(tds) >= 3 and tds[0].get_text(strip=True) == "3連単":
                    # "¥6,280" -> 6280
                    payout = to_num(
                        tds[2].get_text(strip=True).replace(",", ""), int
                    )
    return ranks, payout


# ===================== メイン =====================
def scrape_race(conn, jcd, current, rno):
    """1レース分を取得してDBへ格納。取得できた選手数を返す。"""
    hd = current.strftime("%Y%m%d")
    race_id = f"{hd}_{jcd}_{rno}"

    # 出走表が空 = 未開催/中止。以降のリクエストを省略して無駄打ちを防ぐ。
    racelist = parse_racelist(fetch("racelist", rno, jcd, hd))
    if not racelist:
        return 0

    before_soup = fetch("beforeinfo", rno, jcd, hd)
    before = parse_beforeinfo(before_soup)
    weather = parse_weather(before_soup)

    ranks, payout = parse_result(fetch("raceresult", rno, jcd, hd))

    # --- races ---
    race_row = {
        "race_id": race_id,
        "date": current.strftime("%Y-%m-%d"),
        "stadium_code": jcd,
        "race_number": rno,
    }
    race_row.update(weather)
    upsert_race(conn, race_row)

    # --- entries ---
    count = 0
    for lane in range(1, 7):
        r = racelist.get(lane, {})
        b = before.get(lane, {})
        upsert_entry(conn, {
            "race_id": race_id,
            "boat_number": lane,
            "player_name": b.get("player_name"),
            "motor_number": r.get("motor_number"),
            "motor_win_rate": r.get("motor_win_rate"),
            "exhibition_time": b.get("exhibition_time"),
            "rank": ranks.get(lane),
            # 3連単配当はレース単位で共通の値を全艇に保持する
            "exact_trifecta_payout": payout,
        })
        count += 1

    # レース単位でコミットし、途中で中断しても取得済み分を失わない
    conn.commit()
    return count


def main():
    conn = sqlite3.connect(DB_PATH)
    init_db(conn)

    total = 0
    try:
        for jcd in STADIUM_CODES:
            current = START_DATE
            while current <= END_DATE:
                hd = current.strftime("%Y%m%d")
                print(f"[{hd}] 場コード{jcd} 取得中...")
                day_rows = 0

                for rno in RACE_NUMBERS:
                    # レース単位のtry-except: 1レース失敗でも全体は止めない
                    try:
                        day_rows += scrape_race(conn, jcd, current, rno)
                    except Exception as e:
                        print(f"    [SKIP] {hd} {rno}R: {type(e).__name__}: {e}")
                        conn.rollback()
                        continue

                if day_rows == 0:
                    print("    -> 開催なし / データ取得できずスキップ")
                else:
                    print(f"    -> {day_rows} 件")
                total += day_rows
                current += timedelta(days=1)
    finally:
        # 中断(Ctrl+C)時もDBを正しく閉じる
        conn.close()

    print(f"\n完了: 計 {total} 件を {DB_PATH} に格納しました。")


if __name__ == "__main__":
    main()
