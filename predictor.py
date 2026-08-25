# -*- coding: utf-8 -*-
"""
Phase 3 : 予測サービスのコア (Webアプリから呼ばれる推論層)

学習済みモデルを読み込み、指定レースの各艇の勝率を返す。

■ 設計上の要点
  1. モデルは起動時に1度だけ読み込みプロセス内で使い回す
     (リクエスト毎のロードは数百msかかり、Webアプリでは致命的)
  2. LGBMRanker の出力は「順位づけ用スコア」であり確率ではない。
     レース内で softmax 正規化して合計100%の勝率に変換する。
  3. 締切前に判明する情報のみで予測する(学習時と同じ制約)
"""

import sqlite3
import numpy as np
import pandas as pd

try:
    import lightgbm as lgb
except ImportError:
    raise SystemExit("lightgbm が必要です: pip install lightgbm")

from train_model import NUM_FEATURES, CAT_FEATURES, add_features, LEAKY

MODEL_PATH = "kyotei_model.txt"
DB_PATH = "kyotei_prediction_core.db"


class Predictor:
    """推論を担うクラス。Webアプリ側でシングルトンとして保持する。"""

    def __init__(self, model_path=MODEL_PATH, db_path=DB_PATH):
        self.booster = lgb.Booster(model_file=model_path)
        self.db_path = db_path
        # 学習時の特徴量順序をモデル自身から復元する。
        # 順序がずれると silent に精度が落ちるため必ずモデル基準に合わせる。
        self.features = self.booster.feature_name()

    # ---------- 特徴量の組み立て ----------
    def _build_frame(self, df):
        """生データ -> 学習時と同一の特徴量に変換する。"""
        df = add_features(df)

        for c in CAT_FEATURES:
            if c in df.columns:
                df[c] = df[c].astype("category")

        # 学習時に無かった列/足りない列を吸収する
        for c in self.features:
            if c not in df.columns:
                df[c] = np.nan
        return df[self.features]

    @staticmethod
    def _softmax(scores, temperature=1.0):
        """ランキングスコアをレース内の確率へ変換する。

        lambdarankの出力は相対的な大小のみ意味を持つ。
        expで正規化し、合計1.0の勝率として解釈できるようにする。
        temperature は確率の鋭さ調整用(校正時に最適化する)。
        """
        s = np.asarray(scores, dtype=float) / temperature
        s = s - s.max()          # オーバーフロー対策
        e = np.exp(s)
        return e / e.sum()

    # ---------- 予測 ----------
    def predict_race(self, entries, temperature=1.0):
        """1レース分の出走データ(6行のDataFrame)から勝率を返す。

        entries には締切前に判明する列のみを渡すこと。
        """
        if len(entries) == 0:
            raise ValueError("出走データが空です")

        # リーク列が紛れ込んでいたら明示的に落とす(本番事故の予防)
        leaked = LEAKY & set(entries.columns)
        entries = entries.drop(columns=list(leaked), errors="ignore")

        X = self._build_frame(entries.copy())
        raw = self.booster.predict(X)
        prob = self._softmax(raw, temperature)

        out = entries[["boat_number"]].copy()
        if "player_name" in entries.columns:
            out["player_name"] = entries["player_name"].values
        out["score"] = raw
        out["win_probability"] = prob
        out = out.sort_values("win_probability", ascending=False)
        out["predicted_rank"] = range(1, len(out) + 1)
        return out.reset_index(drop=True)

    def predict_from_db(self, race_id, temperature=1.0):
        """DBに格納済みのレースを予測する(検証・バックテスト用)。"""
        conn = sqlite3.connect(self.db_path)
        df = pd.read_sql("""
            SELECT r.race_id, r.stadium_code, r.race_number, r.distance,
                   r.weather, r.wind_direction, r.wind_speed, r.wave_height,
                   e.boat_number, e.player_name, e.player_class, e.player_age,
                   e.win_rate_all, e.top2_rate_all,
                   e.win_rate_local, e.top2_rate_local,
                   e.motor_win_rate, e.boat_win_rate, e.exhibition_time
            FROM races r JOIN entries e USING(race_id)
            WHERE r.race_id = ?
            ORDER BY e.boat_number
        """, conn, params=(race_id,))
        conn.close()
        if df.empty:
            raise ValueError(f"レースが見つかりません: {race_id}")
        return self.predict_race(df, temperature)


if __name__ == "__main__":
    import sys
    race_id = sys.argv[1] if len(sys.argv) > 1 else None
    p = Predictor()
    print(f"モデル特徴量: {len(p.features)}個")
    if race_id:
        print(p.predict_from_db(race_id).to_string(index=False))
