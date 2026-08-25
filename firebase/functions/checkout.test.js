const C=require('./checkout');
function mockDb(d={}){return{d:new Map(Object.entries(d)),
 async get(p){const v=this.d.get(p);return v?{...v}:null},
 async set(p,v){this.d.set(p,{...v})}}}
// Stripe を模す。呼び出し引数を記録して検証する。
function mockStripe(){
 const calls=[];
 return{calls,
  checkout:{sessions:{create:async o=>{calls.push(['checkout',o]);return{url:'https://pay/x'}}}},
  billingPortal:{sessions:{create:async o=>{calls.push(['portal',o]);return{url:'https://portal/x'}}}}};
}
let pass=0,fail=0;
const t=(n,c)=>{c?pass++:fail++;console.log(`${c?'  OK':'  NG'}  ${n}`)};

(async()=>{
console.log('■ 新規契約');
{
 const db=mockDb(),s=mockStripe();
 const r=await C.createCheckout(s,db,'u1','https://chakuyomi.jp','a@b.jp');
 const o=s.calls[0][1];
 t('決済URLを返す',r.ok&&r.url==='https://pay/x');
 t('uidをmetadataに入れる',o.metadata.uid==='u1');
 t('subscription側にもuidを入れる',o.subscription_data.metadata.uid==='u1');
 t('価格IDが正しい',o.line_items[0].price==='price_1U89jnRuEOH7LiIYbp06J8r5');
 t('サブスクモード',o.mode==='subscription');
 t('日本語表示',o.locale==='ja');
}

console.log('\n■ 二重課金の防止');
{
 const db=mockDb({'subscriptions/u1':{status:'active',currentPeriodEnd:Date.now()+864e5}});
 const s=mockStripe();
 const r=await C.createCheckout(s,db,'u1','https://chakuyomi.jp');
 t('契約中なら拒否',!r.ok&&r.code===409);
 t('Stripeを呼ばない',s.calls.length===0);
}

console.log('\n■ 再契約');
{
 const db=mockDb({'subscriptions/u1':{status:'canceled',stripeCustomerId:'cus_9'}});
 const s=mockStripe();
 const r=await C.createCheckout(s,db,'u1','https://chakuyomi.jp');
 t('解約後は再契約できる',r.ok);
 t('既存顧客に紐づける',s.calls[0][1].customer==='cus_9');
}

console.log('\n■ 契約管理ページ');
{
 const db=mockDb({'subscriptions/u1':{status:'active',stripeCustomerId:'cus_9'}});
 const s=mockStripe();
 const r=await C.createPortal(s,db,'u1','https://chakuyomi.jp');
 t('ポータルURLを返す',r.ok&&r.url==='https://portal/x');
 t('顧客IDを渡す',s.calls[0][1].customer==='cus_9');
}
{
 const db=mockDb(),s=mockStripe();
 const r=await C.createPortal(s,db,'u2','https://chakuyomi.jp');
 t('未契約は404',!r.ok&&r.code===404);
}

console.log(`\n合計: ${pass} 成功 / ${fail} 失敗`);
process.exit(fail?1:0);
})();
