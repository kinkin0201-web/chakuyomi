const A=require('./admin');
function mockDb(init={}){return{
 d:new Map(Object.entries(init)),
 async get(p){const v=this.d.get(p);return v?{...v}:null},
 async set(p,v){this.d.set(p,{...v})},
 async delete(p){this.d.delete(p)},
 async query(col,{limit=20,cursor=null}={}){
   const items=[...this.d.entries()].filter(([k])=>k.startsWith(col+'/'))
     .map(([k,v])=>({uid:k.split('/')[1],...v}));
   const start=cursor?items.findIndex(x=>x.uid===cursor)+1:0;
   const page=items.slice(start,start+limit);
   return {items:page,next:page.length===limit?page[page.length-1].uid:null};
 }};}
const NOW=1700000000000, DAY=864e5;
let pass=0,fail=0;
const t=(n,c)=>{c?pass++:fail++;console.log(`${c?'  OK':'  NG'}  ${n}`)};

(async()=>{
console.log('■ 無料アクセスの付与');
{
 const db=mockDb({'users/u1':{plan:'free'}});
 const r=await A.grantAccess(db,'adm','u1',30,'招待',NOW);
 const s=await db.get('subscriptions/u1');
 t('付与できる',r.ok);
 t('30日後まで有効',s.currentPeriodEnd===NOW+30*DAY);
 t('activeになる',s.status==='active');
 t('手動付与の印が残る',s.grantedBy==='adm');
 t('理由が残る',s.grantReason==='招待');
 const logs=[...db.d.keys()].filter(k=>k.startsWith('audit_logs/'));
 t('監査ログが残る',logs.length===1);
}

console.log('\n■ 期限の延長（上書きしない）');
{
 const db=mockDb({'users/u1':{},'subscriptions/u1':{currentPeriodEnd:NOW+10*DAY,grantedBy:'adm'}});
 await A.grantAccess(db,'adm','u1',30,'',NOW);
 const s=await db.get('subscriptions/u1');
 t('既存期限に加算される',s.currentPeriodEnd===NOW+40*DAY);
}
{
 const db=mockDb({'users/u1':{},'subscriptions/u1':{currentPeriodEnd:NOW-5*DAY,grantedBy:'adm'}});
 await A.grantAccess(db,'adm','u1',30,'',NOW);
 const s=await db.get('subscriptions/u1');
 t('期限切れなら現在から起算',s.currentPeriodEnd===NOW+30*DAY);
}

console.log('\n■ 入力の検証');
{
 const db=mockDb({'users/u1':{}});
 t('0日は拒否',!(await A.grantAccess(db,'adm','u1',0)).ok);
 t('負数は拒否',!(await A.grantAccess(db,'adm','u1',-5)).ok);
 t('過大な日数は拒否',!(await A.grantAccess(db,'adm','u1',9999)).ok);
 t('数値以外は拒否',!(await A.grantAccess(db,'adm','u1','abc')).ok);
 t('存在しない会員は404',(await A.grantAccess(db,'adm','nobody',30)).code===404);
}

console.log('\n■ 付与の取り消し');
{
 const db=mockDb({'users/u1':{},'subscriptions/u1':{status:'active',grantedBy:'adm'}});
 const r=await A.revokeAccess(db,'adm','u1','終了',NOW);
 t('手動付与は取り消せる',r.ok);
 t('canceledになる',(await db.get('subscriptions/u1')).status==='canceled');
}
{
 const db=mockDb({'users/u1':{},'subscriptions/u1':{status:'active',stripeCustomerId:'cus_1'}});
 const r=await A.revokeAccess(db,'adm','u1','',NOW);
 t('Stripe契約は取り消せない',!r.ok&&r.code===409);
}

console.log('\n■ アカウント停止');
{
 const db=mockDb({'users/u1':{activeSid:'sidA'},'sessions/sidA':{uid:'u1'}});
 const r=await A.suspendUser(db,'adm','u1','規約違反',NOW);
 t('停止できる',r.ok);
 t('セッションが破棄される',!(await db.get('sessions/sidA')));
 t('activeSidが消える',(await db.get('users/u1')).activeSid===null);
 t('理由が残る',(await db.get('users/u1')).suspendReason==='規約違反');
 await A.unsuspendUser(db,'adm','u1',NOW);
 t('解除できる',(await db.get('users/u1')).suspended===false);
}

console.log('\n■ 一覧のページング');
{
 const init={};
 for(let i=0;i<45;i++) init[`users/u${i}`]={lastLoginAt:NOW-i*1000};
 const db=mockDb(init);
 const p1=await A.listUsers(db,{limit:20});
 t('20件ずつ返す',p1.users.length===20);
 t('次ページの目印がある',!!p1.nextCursor);
 const p2=await A.listUsers(db,{limit:20,cursor:p1.nextCursor});
 t('続きが取れる',p2.users.length===20&&p2.users[0].uid!==p1.users[0].uid);
}

console.log('\n■ 会員詳細');
{
 const db=mockDb({'users/u1':{plan:'free'},'subscriptions/u1':{status:'active'}});
 const r=await A.getUser(db,'u1');
 t('契約状況も返る',r.ok&&r.subscription.status==='active');
 t('存在しなければ404',(await A.getUser(db,'x')).code===404);
}

console.log(`\n合計: ${pass} 成功 / ${fail} 失敗`);
process.exit(fail?1:0);
})();
