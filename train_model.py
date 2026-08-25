# -*- coding: utf-8 -*-
"""
競艇予測システム Phase 2 : LightGBM 学習パイプライン

Phase 1 で構築した kyotei_prediction_core.db を読み込み、
「1レース6艇の中でどの艇が勝つか」を予測するモデルを学習する。

■ なぜ LGBMRanker (lambdarank) か
   競艇は1レース6艇のうち1着が必ず1つだけ。各艇を独立した二値分類で
   解くと「6艇の勝率合計が100%にならない」問題が起きる。
   レース単位のグループ学習(ランキング学習)なら艇同士を相対比較でき、
   出力をsoftmaxで正規化すれば合計100%の確率として扱える。

■ リーク防止(最重要)
   締切後にしか判明しない列は特徴量に入れない。
     - start_course / start_timing : 進入・STは発走後に確定
     - rank / rank_status          : 結果そのもの
     - exact_trifecta_*            : 配当は結果
   展示タイム・モーター2連対率・気象は締切前に判明するため使用可。

■ 検証方法
   時系列データのためランダム分割は不可(未来を見て過去を当ててしまう)。
   日付で train/valid/test を前後に分割する。
"""

import argparse
import sqlite3

import numpy as np
import pandas as pd

try:
    import lightgbm as lgb
except ImportError:
    raise SystemExit("lightgbm が必要です: pip install lightgbm (macOSは brew install libomp も)")

from sklearn.metrics import roc_auc_score

DB_PATH = "kyotei_prediction_core.db"
MODEL_PATH = "kyotei_model.txt"

# ===== 締切前に判明する特徴量のみを使用する =====
NUM_FEATURES = [
    "boat_number",       # 枠番(競艇では最重要級。1コースが有利)
    "player_age",
    "win_rate_all",      # 全国勝率
    "top2_rate_all",     # 全国2連対率
    "win_rate_local",    # 当地勝率
    "top2_rate_local",   # 当地2連対率
    "motor_win_rate",    # モーター2連対率
    "boat_win_rate",     # ボート2連対率
    "exhibition_time",   # 展示タイム
    "wind_speed",
    "wave_height",
    "race_number",
    "distance",
    # 直近成績(今の調子)
    "recent_win_5", "recent_top2_5",
    "recent_win_10", "recent_top2_10", "recent_rank_10",
    # スタート力(2着・3着の予測に効く)
    "avg_st", "recent_st_10", "course_change_rate",
]
CAT_FEATURES = [
    "player_class",      # A1/A2/B1/B2
    "stadium_code",
    "weather",
    "wind_direction",
]

# 明示的に除外する列(リーク源)。将来列が増えた際の事故防止も兼ねる。
LEAKY = {
    "rank", "rank_status", "start_course", "start_timing",
    "exact_trifecta_payout", "exact_trifecta_combo", "is_win",
}


def load_data(db_path):
    """DBから学習用データを読み出す。"""
    conn = sqlite3.connect(db_path)
    df = pd.read_sql("""
        SELECT r.race_id, r.date, r.stadium_code, r.race_number, r.distance,
               r.weather, r.wind_direction, r.wind_speed, r.wave_height,
               e.boat_number, e.player_id, e.player_class, e.player_age,
               e.win_rate_all, e.top2_rate_all,
               e.win_rate_local, e.top2_rate_local,
               e.motor_number, e.motor_win_rate, e.boat_win_rate, e.exhibition_time,
               e.start_course, e.start_timing,
               e.rank
        FROM races r
        JOIN entries e USING(race_id)
        ORDER BY r.date, r.race_id, e.boat_number
    """, conn)
    conn.close()
    return df


def add_recent_form(df):
    """選手ごとの直近成績を追加する。

    公式の勝率は期間集計のため反応が遅い。直近N走の成績を足すことで
    「今の調子」を捉える。

    ■ リーク防止
      shift() で自分自身の結果を除外してから rolling する。
      これを忘れると「その日の着順」を使って予測することになり、
      検証では高精度に見えるが本番では機能しない。
    """
    if "player_id" not in df.columns or "rank" not in df.columns:
        return df
    d = df.sort_values(["player_id", "date"]).copy()
    d["_win"] = (d["rank"] == 1).astype(float)
    d["_top2"] = (d["rank"] <= 2).astype(float)
    # 着順が不明(失格等)の行は集計から除く
    d.loc[d["rank"].isna(), ["_win", "_top2"]] = np.nan

    g = d.groupby("player_id")
    for n in (5, 10):
        d[f"recent_win_{n}"] = g["_win"].transform(
            lambda s: s.shift().rolling(n, min_periods=2).mean())
        d[f"recent_top2_{n}"] = g["_top2"].transform(
            lambda s: s.shift().rolling(n, min_periods=2).mean())
    # 直近の平均着順(小さいほど good)
    d["recent_rank_10"] = g["rank"].transform(
        lambda s: s.shift().rolling(10, min_periods=2).mean())

    # --- スタート力 ---
    # ST はレース後にしか分からないが、「その選手が普段どれだけ速いか」は
    # 過去実績から分かる。shift() で自分の結果を除くのでリークしない。
    # 実測: 平均ST 0.14未満の1号艇は68%、0.18以上は40% と28ptの差。
    if "start_timing" in d.columns:
        d["avg_st"] = g["start_timing"].transform(
            lambda s: s.shift().expanding().mean())
        d["recent_st_10"] = g["start_timing"].transform(
            lambda s: s.shift().rolling(10, min_periods=3).mean())

    # --- 進入変更のクセ ---
    # 枠なり以外に入る選手は展開を乱す。荒れる要因になる。
    if "start_course" in d.columns and "boat_number" in d.columns:
        chg = (d["start_course"] != d["boat_number"]).astype(float)
        chg[d["start_course"].isna()] = np.nan
        d["_chg"] = chg
        d["course_change_rate"] = g["_chg"].transform(
            lambda s: s.shift().expanding().mean())
        d = d.drop(columns=["_chg"])

    d = d.drop(columns=["_win", "_top2"])
    return d.sort_index()


def add_features(df):
    """レース内での相対値を特徴量として追加する。

    競艇は「6艇の中での相対的な強さ」が効くため、
    生の値より『そのレース内で何番目に速いか』が効きやすい。
    """
    g = df.groupby("race_id")
    for col in ["exhibition_time", "motor_win_rate", "win_rate_all",
                "top2_rate_local", "recent_top2_10"]:
        if col not in df.columns:
            continue
        # レース内順位(展示タイムは小さいほど速いので昇順)
        asc = col == "exhibition_time"
        df[f"{col}_rank_in_race"] = g[col].rank(ascending=asc, method="min")
        # レース平均との差
        df[f"{col}_diff_mean"] = df[col] - g[col].transform("mean")
    return df


def prepare(df):
    """目的変数の作成と型変換。"""
    # 1着=1, それ以外=0。失格・欠場(rank NULL)は学習から除外する。
    # 直近成績は失格行も履歴として持つため、除外前に計算する
    df = add_recent_form(df)

    df = df[df["rank"].notna()].copy()
    df["is_win"] = (df["rank"] == 1).astype(int)

    df = add_features(df)

    # カテゴリ列はcategory型にする(LightGBMがネイティブ対応)
    for c in CAT_FEATURES:
        df[c] = df[c].astype("category")

    return df


def split_by_date(df, valid_days=30, test_days=30):
    """時系列分割。未来のデータで過去を学習しないようにする。"""
    days = np.sort(df["date"].unique())
    if len(days) < valid_days + test_days + 10:
        raise SystemExit(
            f"データ不足: {len(days)}日分しかありません。"
            f"最低 {valid_days + test_days + 10}日分を推奨します。"
        )
    test_start = days[-test_days]
    valid_start = days[-(test_days + valid_days)]

    train = df[df["date"] < valid_start]
    valid = df[(df["date"] >= valid_start) & (df["date"] < test_start)]
    test = df[df["date"] >= test_start]
    return train, valid, test


def to_group(df):
    """LGBMRanker用のグループ配列(各レースの行数)を返す。

    race_id で連続していることが前提のため、事前にソート済みであること。
    """
    return df.groupby("race_id", sort=False).size().to_numpy()


def evaluate(name, df, pred):
    """レース単位の的中率で評価する。"""
    d = df[["race_id", "is_win", "boat_number"]].copy()
    d["pred"] = pred

    # 各レースで最もスコアの高い艇を「予想1着」とする
    top = d.loc[d.groupby("race_id")["pred"].idxmax()]
    hit = top["is_win"].mean()

    # 参考: 常に1号艇を買った場合(競艇のベースライン)
    base = d[d["boat_number"] == 1]["is_win"].mean()

    auc = roc_auc_score(d["is_win"], d["pred"])

    print(f"\n===== {name} =====")
    print(f"レース数        : {d['race_id'].nunique():,}")
    print(f"1着的中率       : {hit:.1%}")
    print(f"  (1号艇固定比較): {base:.1%}")
    print(f"AUC             : {auc:.4f}")
    return hit


def main():
    p = argparse.ArgumentParser(description="競艇予測モデル学習 (Phase 2)")
    p.add_argument("--db", default=DB_PATH)
    p.add_argument("--out", default=MODEL_PATH)
    p.add_argument("--valid-days", type=int, default=30)
    p.add_argument("--test-days", type=int, default=30)
    args = p.parse_args()

    print("データ読み込み中...")
    df = load_data(args.db)
    print(f"  全レコード: {len(df):,} 件 / {df['race_id'].nunique():,} レース")

    df = prepare(df)
    print(f"  学習対象  : {len(df):,} 件 (失格・欠場を除外)")

    train, valid, test = split_by_date(df, args.valid_days, args.test_days)
    print(f"\n期間分割:")
    for n, d in [("train", train), ("valid", valid), ("test", test)]:
        print(f"  {n:5s}: {d['date'].min()} 〜 {d['date'].max()}  "
              f"{d['race_id'].nunique():,}レース")

    feats = NUM_FEATURES + CAT_FEATURES
    feats += [c for c in df.columns if c.endswith(("_rank_in_race", "_diff_mean"))]

    # リーク列が混入していないか最終チェック
    leaked = LEAKY & set(feats)
    if leaked:
        raise SystemExit(f"リーク列が特徴量に混入しています: {leaked}")
    print(f"\n特徴量: {len(feats)}個")

    model = lgb.LGBMRanker(
        objective="lambdarank",
        metric="ndcg",
        n_estimators=1000,
        learning_rate=0.05,
        num_leaves=31,
        min_child_samples=30,
        colsample_bytree=0.8,
        subsample=0.8,
        subsample_freq=1,
        random_state=42,
        verbose=-1,
    )

    print("\n学習中...")
    model.fit(
        train[feats], train["is_win"],
        group=to_group(train),
        eval_set=[(valid[feats], valid["is_win"])],
        eval_group=[to_group(valid)],
        eval_at=[1],               # 1着を当てられるかを直接評価
        callbacks=[
            lgb.early_stopping(50, verbose=False),
            lgb.log_evaluation(100),
        ],
    )
    print(f"  最良イテレーション: {model.best_iteration_}")

    # 評価
    evaluate("VALID", valid, model.predict(valid[feats]))
    evaluate("TEST (未知データ)", test, model.predict(test[feats]))

    # 特徴量重要度
    imp = pd.DataFrame({
        "feature": feats,
        "gain": model.booster_.feature_importance("gain"),
    }).sort_values("gain", ascending=False)
    print("\n===== 特徴量重要度 TOP15 =====")
    for _, r in imp.head(15).iterrows():
        print(f"  {r['feature']:32s} {r['gain']:12,.0f}")

    model.booster_.save_model(args.out)
    print(f"\nモデルを {args.out} に保存しました。")


if __name__ == "__main__":
    main()
