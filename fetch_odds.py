# -*- coding: utf-8 -*-
"""3連単オッズを取得する。

■ なぜ必要か
   モデルの確率と市場(オッズ)の相関は 0.853。
   ほぼ同じものを見ているため、当たりそうな順に買うと控除率25%に負ける。

   実測では予測1位の回収率が79.8%と最も低く、
   11位が91.9%と高い。人気が過剰に買われている証拠。

   期待値 = モデル確率 x オッズ
   これが1.0を超える買い目だけを買うのが、控除率を越える唯一の理屈。

■ 注意
   オッズは締切直前まで変動する。
   実運用では締切の1〜2分前に取得する必要がある。
"""
import argparse, json, re, sys, time, warnings
import requests
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                         "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"}
SLEEP = 3
URL = "https://www.boatrace.jp/owpc/pc/race/odds3t"


def fetch(jcd, rno, hd):
    """1レースの3連単オッズを {(1,2,3): 8.2, ...} で返す。"""
    try:
        res = requests.get(f"{URL}?rno={rno}&jcd={jcd}&hd={hd}",
                           headers=HEADERS, timeout=20)
        time.sleep(SLEEP)
        if res.status_code != 200:
            return {}
        soup = BeautifulSoup(res.text, "lxml")
    except Exception as e:
        time.sleep(SLEEP)
        print(f"    [WARN] {jcd} {rno}R: {e}", file=sys.stderr)
        return {}

    odds = {}
    # 表の構造(実際のHTMLを確認して判明):
    #   6ブロックが横に並び、左から1着=1号艇〜6号艇。
    #   各ブロックは「2着 3着 オッズ」だが、2着が同じ間は
    #   rowspan で省略され「3着 オッズ」の2列だけになる。
    #   そのためブロックごとに直前の2着を覚えながら読む。
    for table in soup.select("table"):
        rows = table.select("tbody tr")
        if len(rows) < 15:
            continue
        last_second = [None] * 6
        for tr in rows:
            cells = [td.get_text(strip=True) for td in tr.select("td")]
            idx = 0
            for blk in range(6):
                if idx >= len(cells):
                    break
                # 3列(2着・3着・オッズ)か2列(3着・オッズ)かを判定する
                if idx + 2 < len(cells) and re.fullmatch(r"[1-6]", cells[idx]) \
                        and re.fullmatch(r"[1-6]", cells[idx + 1]) \
                        and re.fullmatch(r"[\d.]+", cells[idx + 2]):
                    second, third, o = cells[idx], cells[idx + 1], cells[idx + 2]
                    last_second[blk] = second
                    idx += 3
                elif idx + 1 < len(cells) and re.fullmatch(r"[1-6]", cells[idx]) \
                        and re.fullmatch(r"[\d.]+", cells[idx + 1]):
                    second, third, o = last_second[blk], cells[idx], cells[idx + 1]
                    idx += 2
                else:
                    idx += 1
                    continue
                if second is None:
                    continue
                first = blk + 1
                a, b, c = first, int(second), int(third)
                if len({a, b, c}) != 3:
                    continue
                try:
                    odds[(a, b, c)] = float(o)
                except ValueError:
                    pass
        if len(odds) >= 100:
            break
    return odds


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--date", required=True, help="YYYYMMDD")
    p.add_argument("--stadiums", required=True, help="場コード(カンマ区切り)")
    p.add_argument("--races", default="1-12")
    p.add_argument("--out", required=True)
    a = p.parse_args()

    lo, hi = (a.races.split("-") + [a.races])[:2]
    rnos = range(int(lo), int(hi) + 1)

    out = {}
    for jcd in a.stadiums.split(","):
        for rno in rnos:
            o = fetch(jcd, rno, a.date)
            if o:
                key = f"{a.date}_{jcd}_{rno}"
                out[key] = {f"{k[0]}-{k[1]}-{k[2]}": v for k, v in o.items()}
                print(f"  {jcd} {rno}R: {len(o)}通り")
    json.dump(out, open(a.out, "w"), separators=(",", ":"))
    print(f"{a.out}: {len(out)}レース")


if __name__ == "__main__":
    main()
