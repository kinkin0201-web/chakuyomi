# チャクヨミ

競艇の3連単をAIが予測するサブスクリプションサービス。

- 本番: https://chakuyomi.web.app
- 管理: https://chakuyomi.web.app/admin.html

## 何をするものか

出走表・展示タイム・オッズから、各艇の1着/2着/3着確率を予測し、
3連単の買い目を提示する。買い方は2種類から選べる。

| 戦略 | 選び方 | 実測(直近42日・各3点) |
|---|---|---|
| 堅い | 的中確率の高い順 | 的中23.3% / 回収82.4% |
| 妙味 | 期待値の高い順 | 的中 9.8% / 回収83.8% |

**回収率は100%を超えていない。** 控除率25%の壁は越えられておらず、
現時点で「儲かる」商品ではない。買い目提示による時間短縮が提供価値。

## 構成

```
データ収集 → 学習 → 予測配信 → Webアプリ
```

| 層 | 使うもの |
|---|---|
| ホスティング | Firebase Hosting |
| API | Cloud Functions (asia-northeast1) |
| 認証 | Firebase Auth + LINE (OIDC) |
| DB | Firestore (会員・セッションのみ) |
| 決済 | Stripe |
| 自動化 | GitHub Actions |

予測データは Firestore に置かない(読み取り課金が跳ね上がるため)。
Hosting の静的JSONとして配信し、Functions が認証を通して返す。

## スクリプト

### データ収集
| ファイル | 用途 | 頻度 |
|---|---|---|
| `build_db_bulk.py` | 公式一括DLから過去データを取り込む(**主系統**) | 毎日 |
| `collect_odds.py` | 3連単オッズを蓄積する | 15分毎 |
| `scrape_boatrace.py` | 直前情報のスクレイピング(予備) | — |

一括DLは1日2リクエストで全24場が揃い、スクレイピングの432倍速い。

### 学習・予測
| ファイル | 用途 |
|---|---|
| `train_trifecta.py` | 1着/2着/3着モデルを学習(**現行**) |
| `predict_today.py` | 当日の買い目を生成 |
| `publish_trifecta.py` | 過去日の買い目を生成(検証用) |
| `train_model.py` | 特徴量の定義(共通) |
| `train_upset.py` | 旧モデル(1号艇が飛ぶか) |

### 運用
| ファイル | 用途 |
|---|---|
| `monitor.py` | 精度劣化・データ遅延・欠損を検知 |
| `validate_periods.py` | 期間を分けた再現性の検証 |
| `set_admin.js` | 管理者権限の付与 |

## 日常運用

`.github/workflows/predict.yml` が JST 11:00〜19:00 に15分間隔で実行する。
夜間は動かないため費用は発生しない(公開リポジトリは無料)。

手動で回す場合:

```bash
# 当日の買い目を生成
python predict_today.py --stadiums 22 --window 45 --out firebase/public/data/$(date +%F).json

# オッズを蓄積
python collect_odds.py --stadiums 22

# 健全性チェック(異常時は終了コード1)
python monitor.py

# デプロイ
cd firebase && firebase deploy --only hosting
```

## 設計上の鉄則

1. **リーク防止** — `rank` `start_course` `start_timing` `exact_trifecta_*` は
   締切後にしか判明しない。特徴量に入れてはいけない。
   直近成績は `shift()` で自分の結果を除外してから集計する。

2. **予測データを Firestore に置かない** — 読み取り課金が跳ね上がる。
   Firestore は会員情報とセッションのみ。

3. **DBはGit管理しない** — 年71MB増える。Actions のキャッシュに置く。

4. **管理者判定は Custom Claims のみ** — Firestore の値で判定すると、
   その文書を書き換えられた時点で権限昇格が成立する。

## 未了

- `ENFORCE_SINGLE_SESSION = false` — 開発中は同時ログイン制限を無効化。
  公開前に `firebase/functions/auth.js` で `true` に戻す。
- Stripe はテストモード。本番切替時は商品・Webhookを作り直す。
- 法務確認(景表法・特商法の表示)が未了。
- 対象は福岡のみ。全24場対応は未着手。

## データ

- 出典: 日本モーターボート競走会の一括配布ファイル
- 収録: 全24場・1日1ファイル
- 増加量: 約71MB/年
