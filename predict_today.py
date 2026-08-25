# -*- coding: utf-8 -*-
"""当日の予測を生成して配信用JSONに書き出す。

■ なぜスクレイピングが要るか
   モデルは展示タイムを使う。展示タイムは締切の数分前にしか
   公開されず、一括DLファイル(B/K)には含まれない。
   そのため当日分だけは公式サイトから直接取得する。

■ 取得の流れ
   1. 一括DLの B(番組表) から当日の出走表・モーター情報を得る
   2. 公式サイトの直前情報から展示タイムと気象を得る
   3. 両者を突き合わせて予測する

■ 展示タイムが未公開のレース
   まだ発表されていないレースは展示タイムが欠損する。
   モデルは欠損を扱えるので予測自体は出るが、精度は落ちる。
   pending フラグを立てて画面側で区別できるようにする。
"""
import argparse, json, re, sys, time, warnings
from datetime import date, datetime, timedelta

import numpy as np, pandas as pd, requests, lightgbm as lgb
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)
sys.path.insert(0, '.')
from build_db_bulk import download, parse_b_file, STADIUMS
from train_trifecta import rank_combos
from publish_trifecta import group_combos, mark_of, load_odds, build_strategies

HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                         "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"}
SLEEP = 3
BASE = "https://www.boatrace.jp/owpc/pc/race"


def _mins_to(hhmm):
    """締切まで何分か(JST)。過ぎていれば負の値。不明なら None。"""
    try:
        now = datetime.utcnow() + timedelta(hours=9)
        h, m = hhmm.split(":")
        return int(h) * 60 + int(m) - (now.hour * 60 + now.minute)
    except Exception:
        return None


def _past(hhmm):
    """締切時刻を過ぎたか(JST)。"""
    d = _mins_to(hhmm)
    return d is not None and d < 0


def to_num(x, cast=float):
    if x is None:
        return None
    m = re.search(r"-?\d+(?:\.\d+)?", str(x))
    if not m:
        return None
    try:
        return cast(float(m.group()))
    except (TypeError, ValueError):
        return None


ZEN = str.maketrans("０１２３４５６７８９：", "0123456789:")


def parse_deadlines(text, hd, targets):
    """番組表から締切予定時刻を読む。 {race_id: '12:16'} を返す。"""
    out, jcd, rno = {}, None, None
    for line in text.splitlines():
        m = re.match(r"^(\d{2})BBGN", line)
        if m:
            jcd = m.group(1)
            continue
        if jcd is None or (targets and jcd not in targets):
            continue
        m = re.match(r"^　?([０-９\d]{1,2})Ｒ", line)
        if m:
            rno = int(m.group(1).translate(ZEN))
            t = re.search(r"締切予定([０-９\d]{1,2}：[０-９\d]{2})", line)
            if t:
                out[f"{hd}_{jcd}_{rno}"] = t.group(1).translate(ZEN)
    return out


def fetch_result(jcd, rno, hd):
    """確定した着順と3連単配当を取る。未確定なら None。"""
    url = f"{BASE}/raceresult?rno={rno}&jcd={jcd}&hd={hd}"
    try:
        res = requests.get(url, headers=HEADERS, timeout=20)
        time.sleep(SLEEP)
        if res.status_code != 200:
            return None
        soup = BeautifulSoup(res.text, "lxml")
    except Exception:
        time.sleep(SLEEP)
        return None

    ranks, combo, payout = {}, None, None
    for t in soup.select("table.is-w495"):
        head = [th.get_text(strip=True) for th in t.select("thead th")]
        if "着" in head and "枠" in head:
            for tr in t.select("tbody tr"):
                td = tr.select("td")
                if len(td) < 2:
                    continue
                pos = td[0].get_text(strip=True)
                lane = to_num(td[1].get_text(strip=True), int)
                if lane and pos in "１２３４５６":
                    ranks[lane] = "１２３４５６".index(pos) + 1
        elif "勝式" in head:
            for tr in t.select("tbody tr"):
                td = [x.get_text(strip=True) for x in tr.select("td")]
                if len(td) >= 3 and td[0] == "3連単":
                    combo = td[1].replace(" ", "")
                    payout = to_num(td[2].replace(",", ""), int)
    if not combo:
        return None
    return {"ranks": ranks, "combo": combo, "payout": payout}


def fetch_before(jcd, rno, hd):
    """直前情報(展示タイム・気象)を取得する。未公開なら空を返す。"""
    url = f"{BASE}/beforeinfo?rno={rno}&jcd={jcd}&hd={hd}"
    try:
        res = requests.get(url, headers=HEADERS, timeout=20)
        time.sleep(SLEEP)
        if res.status_code != 200:
            return {}, {}
        soup = BeautifulSoup(res.text, "lxml")
    except Exception as e:
        time.sleep(SLEEP)
        print(f"    [WARN] {jcd} {rno}R: {e}", file=sys.stderr)
        return {}, {}

    ex = {}
    table = soup.select_one("table.is-w748")
    if table:
        for tb in table.select("tbody"):
            tds = tb.select("tr")[0].select("td")
            if len(tds) < 5:
                continue
            lane = to_num(tds[0].get_text(strip=True), int)
            if lane:
                ex[lane] = to_num(tds[4].get_text(strip=True))

    w = {}
    box = soup.select_one(".weather1")
    if box:
        el = box.select_one(".is-weather .weather1_bodyUnitLabelTitle")
        w["weather"] = el.get_text(strip=True) if el else None
        el = box.select_one(".is-wind .weather1_bodyUnitLabelData")
        w["wind_speed"] = to_num(el.get_text(strip=True) if el else None)
        el = box.select_one(".is-wave .weather1_bodyUnitLabelData")
        w["wave_height"] = to_num(el.get_text(strip=True) if el else None)
    return ex, w


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--date", help="YYYY-MM-DD (既定: 本日JST)")
    p.add_argument("--db", default="up.db", help="直近成績の参照元")
    p.add_argument("--model", default="trifecta")
    p.add_argument("--points", type=int, default=10)
    p.add_argument("--odds-db", default="odds.db")
    p.add_argument("--form", default="player_form.json",
                   help="集計済みの選手成績")
    p.add_argument("--window", type=int, default=0,
                   help="締切まで何分先のレースを更新するか(0=全件)")
    p.add_argument("--out", required=True)
    p.add_argument("--stadiums", help="場コードをカンマ区切りで限定")
    a = p.parse_args()

    d = (datetime.strptime(a.date, "%Y-%m-%d").date() if a.date
         else (datetime.utcnow() + timedelta(hours=9)).date())
    hd = d.strftime("%Y%m%d")
    print(f"対象日: {d}")

    # 既存ファイルがあれば、確定済みの結果を引き継ぐ。
    # 結果は変わらないので取り直す必要がない(時間の節約)。
    prev = {}
    try:
        with open(a.out, encoding="utf-8") as f:
            old = json.load(f)
        for r in (old.get("races") if isinstance(old, dict) else old) or []:
            if r.get("result") and r["result"].get("combo"):
                prev[r["raceId"]] = r["result"]
    except Exception:
        pass

    # --- 1. 番組表を取得 ---
    text = download("B", d)
    if not text:
        print("番組表が未配信です（開催なし、または未公開）", file=sys.stderr)
        json.dump([], open(a.out, "w"))
        return
    targets = set(a.stadiums.split(",")) if a.stadiums else None
    b_races = parse_b_file(text, hd, targets)
    deadlines = parse_deadlines(text, hd, targets)
    print(f"  番組表: {len(b_races)} レース")

    # --- 2. 選手の直近成績を用意する ---
    # 過去DB(145MB)はGit管理できないため、集計済みの軽量ファイルを使う。
    # 無ければDBから直接読む(ローカル実行時)。
    recent = None
    try:
        with open(a.form, encoding="utf-8") as f:
            form = json.load(f)
        recent = pd.DataFrame.from_dict(
            {k: {"recent_win_10": v[0], "recent_top2_10": v[1],
                 "recent_rank_10": v[2], "recent_win_5": v[0],
                 "recent_top2_5": v[1]} for k, v in form.items()},
            orient="index")
        print(f"  選手成績: {len(recent):,}人 ({a.form})")
    except FileNotFoundError:
        pass

    if recent is None:
        import sqlite3
        try:
            conn = sqlite3.connect(a.db)
            hist = pd.read_sql("""
              SELECT e.player_id, e.rank FROM races r JOIN entries e USING(race_id)
              WHERE e.rank IS NOT NULL
            """, conn)
            conn.close()
            hist["win"] = (hist["rank"] == 1).astype(float)
            hist["top2"] = (hist["rank"] <= 2).astype(float)
            g = hist.groupby("player_id")
            recent = pd.DataFrame({
                "recent_win_10": g["win"].mean(),
                "recent_top2_10": g["top2"].mean(),
                "recent_rank_10": g["rank"].mean(),
                "recent_win_5": g["win"].mean(),
                "recent_top2_5": g["top2"].mean(),
            })
        except Exception as e:
            print(f"  [WARN] 選手成績を読めません: {e}", file=sys.stderr)
            recent = pd.DataFrame(columns=[
                "recent_win_10", "recent_top2_10", "recent_rank_10",
                "recent_win_5", "recent_top2_5"])

    # --- 3. レースごとに直前情報を取得して特徴量を作る ---
    rows, details = [], []
    for race_id in sorted(b_races):
        rec = b_races[race_id]
        jcd, rno = rec["stadium_code"], rec["race_number"]
        dl = deadlines.get(race_id)
        left = _mins_to(dl) if dl else None

        # --- 取得範囲を絞る ---
        # 15分ごとに全レースを取り直すと時間もアクセスも無駄になる。
        #   締切が遠い  -> 展示タイムがまだ無いので取りに行かない
        #   締切を過ぎた -> 結果だけあればよい
        need_before = True
        if a.window and left is not None:
            # 締切より先すぎる、または大きく過ぎたものは直前情報を取らない
            need_before = -180 < left <= a.window

        ex, w = fetch_before(jcd, rno, hd) if need_before else ({}, {})

        entries = sorted(rec["entries"], key=lambda x: x["boat_number"])
        if len(entries) < 6:
            continue
        for e in entries:
            e["exhibition_time"] = ex.get(e["boat_number"])
            rr = recent.reindex([e["player_id"]]).iloc[0] if e["player_id"] in recent.index else None
            for c in recent.columns:
                e[c] = float(rr[c]) if rr is not None and pd.notna(rr[c]) else np.nan

        # 3連単モデルは「1行=1艇」で特徴量を作る
        for e in entries:
            rows.append({
                "race_id": race_id,
                "boat_number": e["boat_number"],
                "stadium_code": jcd,
                "race_number": rno,
                "distance": 1800,
                "weather": w.get("weather"),
                "wind_direction": None,
                "wind_speed": w.get("wind_speed"),
                "wave_height": w.get("wave_height"),
                "player_class": e.get("player_class"),
                "player_age": e.get("player_age"),
                "win_rate_all": e.get("win_rate_all"),
                "top2_rate_all": e.get("top2_rate_all"),
                "win_rate_local": e.get("win_rate_local"),
                "top2_rate_local": e.get("top2_rate_local"),
                "motor_win_rate": e.get("motor_win_rate"),
                "boat_win_rate": e.get("boat_win_rate"),
                "exhibition_time": e.get("exhibition_time"),
                "recent_win_5": e.get("recent_win_5"),
                "recent_top2_5": e.get("recent_top2_5"),
                "recent_win_10": e.get("recent_win_10"),
                "recent_top2_10": e.get("recent_top2_10"),
                "recent_rank_10": e.get("recent_rank_10"),
            })

        # 締切を過ぎたレースだけ結果を取りに行く(無駄なアクセスを避ける)
        result = None
        if race_id in prev:
            result = {"ranks": {}, **prev[race_id]}   # 確定済みは再利用
        elif dl and _past(dl):
            result = fetch_result(jcd, rno, hd)

        details.append({
            "race_id": race_id, "jcd": jcd, "rno": rno,
            "title": rec.get("title"), "weather": w, "entries": entries,
            "deadline": dl,
            "result": result,
            "pending": all(e.get("exhibition_time") is None for e in entries),
        })

    if not rows:
        print("対象レースがありません", file=sys.stderr)
        json.dump([], open(a.out, "w"))
        return

    # --- 4. レース内の相対特徴量を作って予測する ---
    from train_model import add_features, CAT_FEATURES
    df = pd.DataFrame(rows)
    # 欠損だけの列は object 型になり LightGBM が受け付けないため、
    # 数値列を明示的に float へ変換する。
    for c in df.columns:
        if c not in ("race_id", "stadium_code", "weather",
                     "wind_direction", "player_class"):
            df[c] = pd.to_numeric(df[c], errors="coerce")
    df = add_features(df)
    for c in CAT_FEATURES:
        if c in df.columns:
            df[c] = df[c].astype("category")

    boosters = {pos: lgb.Booster(model_file=f"{a.model}_{pos}.txt") for pos in (1, 2, 3)}
    feats = boosters[1].feature_name()
    for c in feats:
        if c not in df.columns:
            df[c] = np.nan
    for pos in (1, 2, 3):
        df[f"p{pos}"] = boosters[pos].predict(df[feats])

    # --- 5. 買い目に変換して出力 ---
    odds_map = load_odds(a.odds_db, {d["race_id"] for d in details})
    if odds_map:
        print(f"  実オッズ: {len(odds_map)}レース分")

    out = []
    by_race = {k: v for k, v in df.groupby("race_id")}
    for det in details:
        rid = det["race_id"]
        g = by_race.get(rid)
        if g is None or len(g) != 6:
            continue
        combos = rank_combos(g)
        odds = odds_map.get(rid)
        picks = group_combos(combos, a.points, odds)
        strategies = build_strategies(combos, odds, a.points)
        out.append({
            "raceId": rid, "stadium": det["jcd"],
            "stadiumName": STADIUMS.get(det["jcd"], det["jcd"]),
            "no": det["rno"], "title": det["title"],
            "pending": det["pending"],
            "weather": det["weather"].get("weather"),
            "wind": None,
            "windSpeed": det["weather"].get("wind_speed"),
            "wave": det["weather"].get("wave_height"),
            "picks": [{**x, "mark": mark_of(i, x.get("ev"))} for i, x in enumerate(picks)],
            "safe": strategies["safe"],
            "value": strategies["value"],
            "confidence": round(float(sum(c[1] for c in combos[:a.points])), 4),
            "totalPoints": a.points,
            "deadline": det.get("deadline"),
            "result": ({"combo": det["result"]["combo"],
                        "payout": det["result"]["payout"]}
                       if det.get("result") else None),
            "boats": [{
                "boat": e["boat_number"], "name": e.get("player_name"),
                "cls": e.get("player_class"),
                "win": e.get("win_rate_all"), "motor": e.get("motor_win_rate"),
                "ex": e.get("exhibition_time"),
                "actual": ((det["result"].get("ranks") or {}).get(e["boat_number"])
                           if det.get("result") else None),
            } for e in det["entries"]],
        })

    out.sort(key=lambda r: (r["stadium"], r["no"]))
    json.dump(out, open(a.out, "w", encoding="utf-8"),
              ensure_ascii=False, separators=(",", ":"))
    pend = sum(1 for r in out if r["pending"])
    print(f"  出力: {len(out)} レース (展示未発表 {pend})")


if __name__ == "__main__":
    main()
