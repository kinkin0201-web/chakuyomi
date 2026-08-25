const API=require('./api'); const A=require('./auth');
function mockDb(){return{d:new Map(),async get(p){const v=this.d.get(p);return v?{...v}:null},
 async set(p,v){this.d.set(p,{...v})},async delete(p){this.d.delete(p)}}}
// 2場 x 12R を用意する(場ごとに無料枠が配られることを確認するため)
const races=n=>Array.from({length:n},(_,i)=>{
 const st=i<12?'22':'24';
 return {raceId:st+'_'+(i%12+1),stadium:st,stadiumName:st==='22'?'福岡':'大村',
  no:i%12+1,title:'予選',upsetP:0.5,boats:[1,2,3,4,5,6]};});
const loader=async()=>races(24);
let pass=0,fail=0;
const t=(n,c)=>{c?pass++:fail++;console.log(`${c?'  OK':'  NG'}  ${n}`)};
const NOW=1700000000000;
const unlocked=r=>r.races.filter(x=>!x.locked).length;

(async()=>{
console.log('■ 未ログイン(ゲスト)');
{
 const db=mockDb();
 const r=await API.getPredictions(db,{date:'2026-08-25',ip:'1.1.1.1'},loader,NOW);
 t('場ごとに1レース無料',unlocked(r)===2);
 t('残りは伏せられる',r.races.filter(x=>x.locked).length===22);
 t('伏せてもレース名は見える',r.races.find(x=>x.locked).stadiumName==='福岡');
 t('予測値は隠れている',r.races.find(x=>x.locked).upsetP===undefined);
}

console.log('\n■ 無料分の選び方');
{
 const a=API.pickFreeRaces(races(24),'2026-08-25');
 const b=API.pickFreeRaces(races(24),'2026-08-25');
 const c=API.pickFreeRaces(races(24),'2026-08-26');
 t('同じ日は同じレース',[...a].join()===[...b].join());
 t('日が変われば別のレース',[...a].join()!==[...c].join());
 t('2場ぶんで2レース',a.size===2);
}

console.log('\n■ ログイン済み・未契約');
{
 const db=mockDb();
 const s=await A.login(db,'u1',{},'x',NOW,'sidA');
 const r=await API.getPredictions(db,{uid:'u1',sid:s.sid,date:'2026-08-25'},loader,NOW);
 t('無料扱いになる',r.plan==='free'&&unlocked(r)===2);
}

console.log('\n■ 契約中');
{
 const db=mockDb();
 const s=await A.login(db,'u2',{},'x',NOW,'sidB');
 await db.set('subscriptions/u2',{status:'active',currentPeriodEnd:NOW+30*864e5});
 const r=await API.getPredictions(db,{uid:'u2',sid:s.sid,date:'2026-08-25'},loader,NOW);
 t('全レース見える',r.plan==='paid'&&unlocked(r)===24);
}

console.log('\n■ 解約後');
{
 const db=mockDb();
 const s=await A.login(db,'u3',{},'x',NOW,'sidC');
 await db.set('subscriptions/u3',{status:'active',currentPeriodEnd:NOW-864e5});
 const r=await API.getPredictions(db,{uid:'u3',sid:s.sid,date:'2026-08-25'},loader,NOW);
 t('期末日超過で無料に戻る',r.plan==='free'&&unlocked(r)===2);
}

console.log('\n■ セッション無効');
{
 const db=mockDb();
 await A.login(db,'u4',{},'iPhone',NOW,'sidA');
 await A.login(db,'u4',{},'PC',NOW,'sidB');   // 2台目
 await db.set('subscriptions/u4',{status:'active',currentPeriodEnd:NOW+864e5});
 const r=await API.getPredictions(db,{uid:'u4',sid:'sidA',date:'2026-08-25'},loader,NOW);
 if (A.ENFORCE_SINGLE_SESSION) {
   t('古い端末は401',!r.ok&&r.code===401);
   t('理由が伝わる',(r.msg||'').length>0);
 } else {
   t('制限無効時は古い端末も通る',r.ok===true);
   console.log('  ※ ENFORCE_SINGLE_SESSION=false のため制限は無効です');
 }
 // 存在しないセッションは常に拒否されること
 const bad=await API.getPredictions(db,{uid:'u4',sid:'notexist',date:'2026-08-25'},loader,NOW);
 t('存在しないsidは必ず拒否',!bad.ok&&bad.code===401);
}

console.log('\n■ レート制限');
{
 const db=mockDb();
 const s=await A.login(db,'u5',{},'x',NOW,'sidE');
 await db.set('subscriptions/u5',{status:'active',currentPeriodEnd:NOW+864e5});
 let last;
 for(let i=0;i<62;i++) last=await API.getPredictions(db,{uid:'u5',sid:s.sid,date:'2026-08-25'},loader,NOW);
 t('61回目以降は429',!last.ok&&last.code===429);
}

console.log(`\n合計: ${pass} 成功 / ${fail} 失敗`);
process.exit(fail?1:0);
})();
