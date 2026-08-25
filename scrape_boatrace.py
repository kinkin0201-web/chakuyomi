# -*- coding: utf-8 -*-
"""
ボートレース公式サイト データ収集スクリプト (MVP)

対象: 福岡競艇場 (場コード 22)
出力: fukuoka_race_data.csv  (1行 = 1選手)

取得項目:
  レース日付 / レース番号 / 枠番 / 選手名 / モーター番号 /
  モーター2連対率 / 展示タイム / 着順 / 3連単配当金
"""

import time
import warnings
from datetime import date, timedelta

import pandas as pd
import requests
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

# 公式サイトはXHTML宣言付きのため、lxmlのHTMLパーサ利用時の警告を抑制
warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

# ===================== 設定 =====================
JCD = "22"                       # 場コード: 22 = 福岡
START_DATE = date(2026, 7, 1)    # 取得開始日
END_DATE = date(2026, 7, 31)     # 取得終了日
RACE_NUMBERS = range(1, 13)      # 1R〜12R
OUTPUT_CSV = "fukuoka_race_data.csv"

SLEEP_SEC = 3                    # サーバ負荷軽減 (絶対ルール)
TIMEOUT = 20

BASE = "https://www.boatrace.jp/owpc/pc/race"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    )
}
# ================================================


def fetch(page: str, rno: int, hd: str):
    """1ページ取得してBeautifulSoupを返す。必ずsleepを挟む。

    開催が無い日/中止レースは公式トップへリダイレクトされ、
    目的のテーブルが存在しないため呼び出し側でNone扱いになる。
    """
    url = f"{BASE}/{page}?rno={rno}&jcd={JCD}&hd={hd}"
    try:
        res = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        res.encoding = res.apparent_encoding or "utf-8"
        if res.status_code != 200:
            return None
        return BeautifulSoup(res.text, "lxml")
    except Exception as e:
        print(f"    [WARN] request failed {page} {hd} {rno}R: {e}")
        return None
    finally:
        time.sleep(SLEEP_SEC)   # 例外時も必ずウェイト


def to_float(text):
    """数値化できなければNaN(欠損値)を返す。"""
    try:
        return float(str(text).strip())
    except (TypeError, ValueError):
        return float("nan")


def parse_racelist(soup):
    """出走表ページから モーター番号 / モーター2連対率 を枠番ごとに取得。"""
    out = {}
    if soup is None:
        return out
    tables = soup.select("table")
    if len(tables) < 2:
        return out
    for tbody in tables[1].select("tbody"):
        tds = tbody.select("tr")[0].select("td")
        # 列数が想定外(中止・特殊表記など)の行はスキップ
        if len(tds) < 7:
            continue
        try:
            lane = int(tds[0].get_text(strip=True))
        except ValueError:
            continue
        # index 6 = モーター列: [No, 2連率, 3連率]
        motor = tds[6].get_text("|", strip=True).split("|")
        out[lane] = {
            "モーター番号": to_float(motor[0]) if len(motor) > 0 else float("nan"),
            "モーター2連対率": to_float(motor[1]) if len(motor) > 1 else float("nan"),
        }
    return out


def parse_beforeinfo(soup):
    """直前情報ページから 選手名 / 展示タイム を枠番ごとに取得。"""
    out = {}
    if soup is None:
        return out
    table = soup.select_one("table.is-w748")
    if table is None:
        return out
    for tbody in table.select("tbody"):
        tds = tbody.select("tr")[0].select("td")
        if len(tds) < 5:
            continue
        try:
            lane = int(tds[0].get_text(strip=True))
        except ValueError:
            continue
        out[lane] = {
            # 全角スペースを半角1つに正規化
            "選手名": " ".join(tds[2].get_text(strip=True).split()),
            "展示タイム": to_float(tds[4].get_text(strip=True)),  # index4 = 展示タイム
        }
    return out


# 着順の漢数字→数値変換 (失格・欠場などはNaN)
RANK_MAP = {"１": 1, "２": 2, "３": 3, "４": 4, "５": 5, "６": 6}


def parse_result(soup):
    """結果ページから 枠番ごとの着順 と レース共通の3連単配当を取得。"""
    ranks, payout = {}, float("nan")
    if soup is None:
        return ranks, payout

    for table in soup.select("table.is-w495"):
        head = [th.get_text(strip=True) for th in table.select("thead th")]

        # --- 着順テーブル: 着 / 枠 / ボートレーサー / レースタイム ---
        if "着" in head and "枠" in head:
            for tr in table.select("tbody tr"):
                tds = tr.select("td")
                if len(tds) < 2:
                    continue
                try:
                    lane = int(tds[1].get_text(strip=True))
                except ValueError:
                    continue   # 枠が数値でない行は無視
                # 失格(妨害失格/転覆/欠場など)はNaN
                ranks[lane] = RANK_MAP.get(tds[0].get_text(strip=True), float("nan"))

        # --- 払戻金テーブル: 勝式 / 組番 / 払戻金 / 人気 ---
        elif "勝式" in head:
            for tr in table.select("tbody tr"):
                tds = tr.select("td")
                if len(tds) >= 3 and tds[0].get_text(strip=True) == "3連単":
                    # "¥6,280" -> 6280.0
                    txt = tds[2].get_text(strip=True).replace("¥", "").replace(",", "")
                    payout = to_float(txt)
    return ranks, payout


def main():
    records = []
    current = START_DATE

    while current <= END_DATE:
        hd = current.strftime("%Y%m%d")
        print(f"[{hd}] 取得中...")
        day_rows = 0

        for rno in RACE_NUMBERS:
            # レース単位でtry-except: 1レース失敗しても全体は止めない
            try:
                racelist = parse_racelist(fetch("racelist", rno, hd))
                # 出走表が無い = 未開催/中止 と判断し、以降のリクエストを省略
                if not racelist:
                    continue

                before = parse_beforeinfo(fetch("beforeinfo", rno, hd))
                ranks, payout = parse_result(fetch("raceresult", rno, hd))

                for lane in range(1, 7):
                    r = racelist.get(lane, {})
                    b = before.get(lane, {})
                    records.append({
                        "レース日付": current.strftime("%Y-%m-%d"),
                        "レース番号": rno,
                        "枠番": lane,
                        "選手名": b.get("選手名", float("nan")),
                        "モーター番号": r.get("モーター番号", float("nan")),
                        "モーター2連対率": r.get("モーター2連対率", float("nan")),
                        "展示タイム": b.get("展示タイム", float("nan")),
                        "着順": ranks.get(lane, float("nan")),
                        "3連単配当金": payout,   # レース単位で共通の値
                    })
                    day_rows += 1

            except Exception as e:
                # HTML構造変化・通信断などは記録してスキップ
                print(f"    [SKIP] {hd} {rno}R: {type(e).__name__}: {e}")
                continue

        print(f"    -> {day_rows} 行")
        current += timedelta(days=1)

    df = pd.DataFrame(records, columns=[
        "レース日付", "レース番号", "枠番", "選手名", "モーター番号",
        "モーター2連対率", "展示タイム", "着順", "3連単配当金",
    ])
    # Excelでの文字化けを防ぐため utf-8-sig で出力
    df.to_csv(OUTPUT_CSV, index=False, encoding="utf-8-sig")
    print(f"\n完了: {len(df)} 行を {OUTPUT_CSV} に出力しました。")
    return df


if __name__ == "__main__":
    main()
