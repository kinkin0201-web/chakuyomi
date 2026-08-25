// 開発者に管理権限を付与する。
//
// Custom Claims は Admin SDK からしか設定できない。
// 使い方: node set_admin.js <uid>
//        node set_admin.js --list   (登録済みユーザーを表示)
const admin = require('firebase-admin');
admin.initializeApp({ projectId: 'chakuyomi' });

(async () => {
  const arg = process.argv[2];

  if (!arg || arg === '--list') {
    const r = await admin.auth().listUsers(20);
    if (!r.users.length) {
      console.log('ユーザーがまだいません。一度サイトでログインしてください。');
      return;
    }
    console.log('登録済みユーザー:');
    for (const u of r.users) {
      const isAdmin = u.customClaims && u.customClaims.admin ? ' [管理者]' : '';
      console.log(`  ${u.uid}  ${u.displayName || u.email || ''}${isAdmin}`);
    }
    console.log('\n付与するには: node set_admin.js <uid>');
    return;
  }

  await admin.auth().setCustomUserClaims(arg, { admin: true });
  console.log(`${arg} に管理権限を付与しました。`);
  console.log('反映には再ログイン(またはトークン更新)が必要です。');
})().catch(e => { console.error('失敗:', e.message); process.exit(1); });
