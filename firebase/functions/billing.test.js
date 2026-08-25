const B = require('./billing');
function mockDb(){return{d:new Map(),async get(p){const v=this.d.get(p);return v?{...v}:null},
 async set(p,v){this.d.set(p,{...v})},async delete(p){this.d.delete(p)}}}
let pass=0,fail=0;
const t=(n,c)=>{c?pass++:fail++;console.log(`${c?'  OK':'  NG'}  ${n}`)};
const NOW=1700000000000, S=1700000000;
const ev=(type,obj,created=S)=>({type,created,data:{object:obj}});

(async()=>{
console.log('■ 課金開始');
{
 const db=mockDb();
 await B.applyWebhook(db,ev('customer.subscription.created',
   {status:'active',customer:'cus_1',id:'sub_1',current_period_end:S+30*86400,
    metadata:{uid:'u1'}}),NOW);
 const s=await db.get('subscriptions/u1');
 t('契約が保存される',s.status==='active');
 t('期末日が保存される',s.currentPeriodEnd===(S+30*86400)*1000);
 t('閲覧できる',B.canAccess(s,NOW).ok);
}

console.log('\n■ 解約');
{
 const db=mockDb();
 await B.applyWebhook(db,ev('customer.subscription.created',
   {status:'active',current_period_end:S+30*86400,metadata:{uid:'u1'}}),NOW);
 await B.applyWebhook(db,ev('customer.subscription.deleted',
   {metadata:{uid:'u1'}},S+100),NOW);
 const s=await db.get('subscriptions/u1');
 t('解約が反映される',s.status==='canceled');
 t('閲覧できなくなる',!B.canAccess(s,NOW).ok);
}

console.log('\n■ 期末日による自動遮断(Webhookが落ちた場合)');
{
 const s={status:'active',currentPeriodEnd:NOW-864e5};
 const r=B.canAccess(s,NOW);
 t('activeでも期末日超過なら拒否',!r.ok&&r.code==='EXPIRED');
}

console.log('\n■ 支払い失敗');
{
 const db=mockDb();
 await B.applyWebhook(db,ev('invoice.payment_failed',{metadata:{uid:'u1'}}),NOW);
 const s=await db.get('subscriptions/u1');
 t('past_dueになる',s.status==='past_due');
 t('閲覧できない',!B.canAccess(s,NOW).ok);
}

console.log('\n■ 冪等性・順序逆転');
{
 const db=mockDb();
 const e=ev('customer.subscription.created',
   {status:'active',current_period_end:S+30*86400,metadata:{uid:'u1'}},S+200);
 await B.applyWebhook(db,e,NOW);
 await B.applyWebhook(db,e,NOW);           // 同じイベントを再送
 const s1=await db.get('subscriptions/u1');
 t('二重適用しても壊れない',s1.status==='active');

 // 古い解約イベントが遅れて届く
 const r=await B.applyWebhook(db,ev('customer.subscription.deleted',
   {metadata:{uid:'u1'}},S+100),NOW);
 const s2=await db.get('subscriptions/u1');
 t('古いイベントは無視される',r.skipped==='stale_event'&&s2.status==='active');
}

console.log('\n■ 異常系');
{
 const db=mockDb();
 const r1=await B.applyWebhook(db,ev('customer.subscription.created',
   {status:'active'}),NOW);
 t('uid無しは弾く',!r1.ok&&r1.reason==='no_uid');
 t('未契約は拒否',!B.canAccess(null,NOW).ok);
 t('trialingは許可',B.canAccess({status:'trialing',currentPeriodEnd:NOW+864e5},NOW).ok);
}

console.log(`\n合計: ${pass} 成功 / ${fail} 失敗`);
process.exit(fail?1:0);
})();
