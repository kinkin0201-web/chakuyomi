const {resolvePlan}=require('./plans');
const API=require('./api'); const A=require('./auth');
function mockDb(i={}){return{d:new Map(Object.entries(i)),
 async get(p){const v=this.d.get(p);return v?{...v}:null},
 async set(p,v){this.d.set(p,{...v})},async delete(p){this.d.delete(p)}}}
const races=n=>Array.from({length:n},(_,i)=>{const st=i<12?'22':'24';
 return{raceId:st+'_'+(i%12+1),stadium:st,stadiumName:'場',no:i%12+1,upsetP:.5,boats:[1]}});
const loader=async()=>races(24);
const NOW=1700000000000,DAY=864e5;
let pass=0,fail=0;
const t=(n,c)=>{c?pass++:fail++;console.log(`${c?'  OK':'  NG'}  ${n}`)};
const unlocked=r=>r.races.filter(x=>!x.locked).length;

(async()=>{
console.log('■ プラン判定');
t('未契約は無料',resolvePlan({},null,NOW).plan==='free');
t('Stripe課金中は有料',resolvePlan({},{status:'active',currentPeriodEnd:NOW+DAY},NOW).plan==='paid');
t('期限切れは無料',resolvePlan({},{status:'active',currentPeriodEnd:NOW-DAY},NOW).plan==='free');
t('招待は無期限で有効',resolvePlan({planOverride:'invited'},null,NOW).plan==='invited');
t('招待は期限内なら有効',resolvePlan({planOverride:'invited',planUntil:NOW+DAY},null,NOW).plan==='invited');
t('招待の期限切れは無料',resolvePlan({planOverride:'invited',planUntil:NOW-DAY},null,NOW).plan==='free');
t('無料固定はStripeより優先',resolvePlan({planOverride:'free'},{status:'active',currentPeriodEnd:NOW+DAY},NOW).plan==='free');
t('招待は全機能',resolvePlan({planOverride:'invited'},null,NOW).fullAccess===true);

console.log('\n■ 招待プランで全レース見られる');
{
 const db=mockDb();
 const s=await A.login(db,'u1',{},'x',NOW,'sidA');
 const u=await db.get('users/u1');
 await db.set('users/u1',{...u,planOverride:'invited'});
 const r=await API.getPredictions(db,{uid:'u1',sid:s.sid,date:'2026-08-25'},loader,NOW);
 t('全24レース見える',unlocked(r)===24);
 t('planはinvited',r.plan==='invited');
}

console.log('\n■ 招待の期限切れは制限される');
{
 const db=mockDb();
 const s=await A.login(db,'u2',{},'x',NOW,'sidB');
 const u=await db.get('users/u2');
 await db.set('users/u2',{...u,planOverride:'invited',planUntil:NOW-DAY});
 const r=await API.getPredictions(db,{uid:'u2',sid:s.sid,date:'2026-08-25'},loader,NOW);
 t('無料枠に戻る',unlocked(r)===2&&r.plan==='free');
}

console.log('\n■ LINEプロフィールの保存');
{
 const db=mockDb();
 await A.login(db,'u3',{name:'山田太郎',picture:'https://line/p.jpg'},'x',NOW,'sidC');
 const u=await db.get('users/u3');
 t('名前が保存される',u.displayName==='山田太郎');
 t('アイコンが保存される',u.photoURL==='https://line/p.jpg');
}

console.log(`\n合計: ${pass} 成功 / ${fail} 失敗`);
process.exit(fail?1:0);
})();
