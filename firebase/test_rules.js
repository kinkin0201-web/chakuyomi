// Security Rules のアクセス可否を検証する(ルールの論理を再現してテスト)
// 本番デプロイ前に @firebase/rules-unit-testing で同じ表を検証すること

const rules = {
  'users/{uid}':        { read: (a,uid)=> a && (a.uid===uid || a.admin), write: ()=>false },
  'sessions/{sid}':     { read: ()=>false, write: ()=>false },
  'subscriptions/{uid}':{ read: (a,uid)=> a && (a.uid===uid || a.admin), write: ()=>false },
  'audit_logs/{id}':    { read: (a)=> a && a.admin===true, write: ()=>false },
  'other/{doc}':        { read: ()=>false, write: ()=>false },
};

const anon  = null;
const user1 = {uid:'u1', admin:false};
const user2 = {uid:'u2', admin:false};
const admin = {uid:'adm', admin:true};

let pass=0, fail=0;
const t=(name, actual, expect)=>{
  const ok = actual===expect;
  ok?pass++:fail++;
  console.log(`${ok?'  OK':'  NG'}  ${name} -> ${actual?'許可':'拒否'}`);
};

console.log('■ 会員情報 users/u1');
t('未ログインが読む',      !!rules['users/{uid}'].read(anon,'u1'),  false);
t('本人が読む',            !!rules['users/{uid}'].read(user1,'u1'), true);
t('他人が読む',            !!rules['users/{uid}'].read(user2,'u1'), false);
t('管理者が読む',          !!rules['users/{uid}'].read(admin,'u1'), true);
t('本人でも書けない',      rules['users/{uid}'].write(), false);

console.log('\n■ セッション sessions/*');
t('本人でも読めない',      rules['sessions/{sid}'].read(), false);
t('管理者でも読めない',    rules['sessions/{sid}'].read(), false);

console.log('\n■ 購読情報 subscriptions/u1');
t('本人が読む',            !!rules['subscriptions/{uid}'].read(user1,'u1'), true);
t('他人が読む',            !!rules['subscriptions/{uid}'].read(user2,'u1'), false);
t('誰も書けない(Webhookのみ)', rules['subscriptions/{uid}'].write(), false);

console.log('\n■ 監査ログ audit_logs/*');
t('一般会員は読めない',    !!rules['audit_logs/{id}'].read(user1), false);
t('管理者は読める',        !!rules['audit_logs/{id}'].read(admin), true);

console.log('\n■ 想定外のコレクション');
t('管理者でも拒否',        rules['other/{doc}'].read(), false);

console.log(`\n合計: ${pass} 成功 / ${fail} 失敗`);
process.exit(fail?1:0);
