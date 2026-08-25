# -*- coding: utf-8 -*-
"""単勝ベースの簡易回収率バックテスト。

競艇の控除率は約25%。つまり何も考えずに買うと回収率75%が期待値。
モデルが「控除率を超えられるか」を検証する。
※単勝オッズはDBに無いため、3連単配当から単勝オッズを近似せず、
  ここでは「的中率」と「1号艇比較」の優位性のみを厳密に測る。
"""
import sqlite3, numpy as np, pandas as pd, lightgbm as lgb
from train_model import add_features, CAT_FEATURES

conn=sqlite3.connect("snap2.db")
df=pd.read_sql("""
 SELECT r.race_id,r.date,r.stadium_code,r.race_number,r.distance,r.weather,
        r.wind_direction,r.wind_speed,r.wave_height,r.exact_trifecta_payout,
        r.exact_trifecta_combo,
        e.boat_number,e.player_class,e.player_age,e.win_rate_all,e.top2_rate_all,
        e.win_rate_local,e.top2_rate_local,e.motor_win_rate,e.boat_win_rate,
        e.exhibition_time,e.rank
 FROM races r JOIN entries e USING(race_id) ORDER BY r.date,r.race_id,e.boat_number
""",conn); conn.close()

df=df[df["rank"].notna()].copy(); df["is_win"]=(df["rank"]==1).astype(int)
df=add_features(df)
for c in CAT_FEATURES: df[c]=df[c].astype("category")
days=np.sort(df["date"].unique()); test=df[df["date"]>=days[-7]].copy()
b=lgb.Booster(model_file="kyotei_model.txt")
test["score"]=b.predict(test[b.feature_name()])
T=0.75
def pr(g):
    s=g["score"].to_numpy()/T; s-=s.max(); e=np.exp(s); return e/e.sum()
test["p"]=np.concatenate([pr(g) for _,g in test.groupby("race_id",sort=False)])

top=test.loc[test.groupby("race_id")["p"].idxmax()]
print(f"全レース      : {len(top):,}")
print(f"モデル的中率  : {top['is_win'].mean():.1%}")
print(f"1号艇固定     : {test[test.boat_number==1]['is_win'].mean():.1%}")

print("\n=== 確信度で絞った場合 ===")
print("しきい値   購入数   的中率   全体比")
for th in [0.5,0.6,0.7,0.8,0.9]:
    sel=top[top["p"]>=th]
    if len(sel)<10: continue
    print(f"  {th:.0%}   {len(sel):>6,}   {sel['is_win'].mean():>6.1%}   {len(sel)/len(top):>5.1%}")

# 3連単の的中可能性(1着のみモデル、2-3着はランダムでない前提の上限確認)
print("\n=== 3連単 予測上位3艇の並び ===")
hit=0; tot=0; payouts=[]
for rid,g in test.groupby("race_id",sort=False):
    g=g.sort_values("p",ascending=False)
    combo="-".join(str(int(x)) for x in g.head(3)["boat_number"])
    actual=g[["boat_number","rank"]].dropna().sort_values("rank")
    if len(actual)<3: continue
    act="-".join(str(int(x)) for x in actual.head(3)["boat_number"])
    tot+=1
    if combo==act:
        hit+=1
        pay=g["exact_trifecta_payout"].iloc[0]
        if pd.notna(pay): payouts.append(pay)
print(f"的中率: {hit}/{tot} = {hit/tot:.1%}")
if payouts:
    ret=sum(payouts)/(tot*100)
    print(f"平均配当: {np.mean(payouts):,.0f}円")
    print(f"回収率  : {ret:.1%}  (100円×{tot}レース購入時)")
    print(f"控除率25%の期待値75%に対し {'上回る' if ret>0.75 else '下回る'}")

print("\n=== 3連単: 確信度で絞った場合の回収率 ===")
print("しきい値   購入数   的中率   平均配当   回収率")
rows=[]
for rid,g in test.groupby("race_id",sort=False):
    g=g.sort_values("p",ascending=False)
    combo="-".join(str(int(x)) for x in g.head(3)["boat_number"])
    a=g[["boat_number","rank"]].dropna().sort_values("rank")
    if len(a)<3: continue
    act="-".join(str(int(x)) for x in a.head(3)["boat_number"])
    rows.append({"p":g["p"].iloc[0],"hit":combo==act,
                 "pay":g["exact_trifecta_payout"].iloc[0] if combo==act else 0})
R=pd.DataFrame(rows)
for th in [0.0,0.3,0.5,0.6,0.7,0.8]:
    s=R[R["p"]>=th]
    if len(s)<20: continue
    ret=s["pay"].sum()/(len(s)*100)
    ph=s[s.hit]["pay"].mean() if s.hit.any() else 0
    print(f"  {th:.0%}   {len(s):>6,}   {s['hit'].mean():>6.1%}   {ph:>7,.0f}円   {ret:>6.1%}")
