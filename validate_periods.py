# -*- coding: utf-8 -*-
"""複数期間で再現するかを検証する。

1期間だけの好成績は偶然の可能性がある。
時期をずらして繰り返し検証し、安定して優位性が出るかを確かめる。
"""
import numpy as np, pandas as pd, lightgbm as lgb, sys
sys.path.insert(0, '.')
from train_upset import build
from sklearn.metrics import roc_auc_score

d = build('up.db')
days = np.sort(d['date'].unique())
feats = [c for c in d.columns if c not in ('race_id', 'date', 'upset')]

print(f"全 {len(d):,} レース / {len(days)} 日\n")
print("検証期間        レース   AUC    荒れ70%↑  堅い20%↓  ベース")
print("-" * 66)

rows = []
# テスト期間を21日ずつ後ろにずらして4回検証する
for back in [84, 63, 42, 21]:
    te_start = days[-back]
    te_end = days[-back + 21] if back > 21 else days[-1]
    va_start = days[-(back + 21)]

    tr = d[d.date < va_start]
    va = d[(d.date >= va_start) & (d.date < te_start)]
    te = d[(d.date >= te_start) & (d.date <= te_end)]
    if len(te) < 500 or len(tr) < 5000:
        continue

    m = lgb.LGBMClassifier(objective='binary', n_estimators=2000, learning_rate=0.03,
                           num_leaves=31, min_child_samples=40, colsample_bytree=0.8,
                           subsample=0.8, subsample_freq=1, random_state=42, verbose=-1)
    m.fit(tr[feats], tr['upset'], eval_set=[(va[feats], va['upset'])],
          eval_metric='auc', callbacks=[lgb.early_stopping(80, verbose=False)])
    p = m.predict_proba(te[feats])[:, 1]

    auc = roc_auc_score(te['upset'], p)
    hi = te[p >= 0.7]
    lo = te[p <= 0.2]
    base = 1 - te['upset'].mean()
    r = {
        'period': f"{te_start}〜{te_end}", 'n': len(te), 'auc': auc,
        'rough': hi['upset'].mean() if len(hi) >= 30 else np.nan,
        'rough_n': len(hi),
        'solid': (1 - lo['upset'].mean()) if len(lo) >= 30 else np.nan,
        'solid_n': len(lo), 'base': base,
    }
    rows.append(r)
    print(f"{r['period']}  {r['n']:>5,}  {auc:.3f}  "
          f"{r['rough']:>6.1%}({r['rough_n']:>3})  "
          f"{r['solid']:>6.1%}({r['solid_n']:>3})  {base:>5.1%}")

df = pd.DataFrame(rows)
print("-" * 66)
print(f"平均              {df.auc.mean():.3f}  {df.rough.mean():>6.1%}       "
      f"{df.solid.mean():>6.1%}       {df.base.mean():.1%}")
print(f"標準偏差          {df.auc.std():.3f}  {df.rough.std():>6.1%}       {df.solid.std():>6.1%}")
print()
print(f"堅い判定の優位性: 平均 {(df.solid.mean()-df.base.mean())*100:+.1f}pt")
