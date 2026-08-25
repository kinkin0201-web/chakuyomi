# -*- coding: utf-8 -*-
"""公開用の実績データを生成する(信用を作るための材料)。

未知データでの検証結果のみを使う。学習に使った期間は含めない。
"""
import json, sys
import numpy as np, pandas as pd, lightgbm as lgb, sqlite3
sys.path.insert(0, '.')
from train_upset import build
from sklearn.metrics import roc_auc_score

d = build('up.db')
days = np.sort(d['date'].unique())
feats = [c for c in d.columns if c not in ('race_id', 'date', 'upset')]

# 直近84日を4期間に分け、それぞれ未知データとして検証
periods = []
for back in [84, 63, 42, 21]:
    ts = days[-back]
    te_end = days[-back + 21] if back > 21 else days[-1]
    vs = days[-(back + 21)]
    tr, va = d[d.date < vs], d[(d.date >= vs) & (d.date < ts)]
    te = d[(d.date >= ts) & (d.date <= te_end)]
    if len(te) < 500: continue
    m = lgb.LGBMClassifier(objective='binary', n_estimators=2000, learning_rate=0.03,
                           num_leaves=31, min_child_samples=40, colsample_bytree=0.8,
                           subsample=0.8, subsample_freq=1, random_state=42, verbose=-1)
    m.fit(tr[feats], tr['upset'], eval_set=[(va[feats], va['upset'])],
          eval_metric='auc', callbacks=[lgb.early_stopping(80, verbose=False)])
    te = te.copy(); te['p'] = m.predict_proba(te[feats])[:, 1]
    periods.append(te)

all_te = pd.concat(periods)
conn = sqlite3.connect('up.db')
pay = pd.read_sql('SELECT race_id,exact_trifecta_payout p3,exact_trifecta_combo c3 FROM races', conn)
conn.close()
all_te = all_te.merge(pay, on='race_id', how='left')

def stat(sel, is_rough):
    if len(sel) == 0: return None
    hit = sel['upset'].mean() if is_rough else 1 - sel['upset'].mean()
    return {"n": int(len(sel)), "hit": round(float(hit), 4)}

out = {
    "period": {"from": str(all_te.date.min()), "to": str(all_te.date.max())},
    "races": int(len(all_te)),
    "baseline": round(float(1 - all_te['upset'].mean()), 4),
    "auc": round(float(roc_auc_score(all_te['upset'], all_te['p'])), 4),
    "rough": {str(int(t*100)): stat(all_te[all_te.p >= t], True) for t in (0.6, 0.7, 0.8)},
    "solid": {str(int(t*100)): stat(all_te[all_te.p <= t], False) for t in (0.3, 0.2, 0.1)},
}

# 月別の推移(安定性を見せる)
all_te['m'] = all_te['date'].str[:7]
months = []
for m, g in all_te.groupby('m'):
    hi, lo = g[g.p >= 0.7], g[g.p <= 0.2]
    months.append({
        "month": m, "races": int(len(g)),
        "rough": round(float(hi['upset'].mean()), 4) if len(hi) >= 20 else None,
        "roughN": int(len(hi)),
        "solid": round(float(1 - lo['upset'].mean()), 4) if len(lo) >= 20 else None,
        "solidN": int(len(lo)),
        "base": round(float(1 - g['upset'].mean()), 4),
    })
out["months"] = months

# 配当の実測
up = all_te[all_te.upset == 1]
out["payout"] = {
    "roughAvg": int(up.p3.mean()) if up.p3.notna().any() else None,
    "solidAvg": int(all_te[all_te.upset == 0].p3.mean()),
}
print(json.dumps(out, ensure_ascii=False))
