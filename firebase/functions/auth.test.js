// 認証ロジックの検証。本番デプロイ前に必ず通すこと。
const A = require('./auth');

// Firestore を模したモック。読み書き回数も数える。
function mockDb() {
  return {
    d: new Map(), reads: 0, writes: 0,
    async get(p) { this.reads++; const v = this.d.get(p); return v ? {...v} : null; },
    async set(p, v) { this.writes++; this.d.set(p, {...v}); },
    async delete(p) { this.writes++; this.d.delete(p); },
  };
}

let pass = 0, fail = 0;
const t = (name, cond) => { cond ? pass++ : fail++; console.log(`${cond ? '  OK' : '  NG'}  ${name}`); };
const NOW = 1700000000000;

(async () => {
  console.log('■ 同時ログイン1つ');
  {
    const db = mockDb();
    const a = await A.login(db, 'u1', {sub: 'L1'}, 'iPhone', NOW, 'sidA');
    t('1台目が有効', (await A.verify(db, 'u1', a.sid, NOW)).ok);

    const b = await A.login(db, 'u1', {sub: 'L1'}, 'Android', NOW, 'sidB');
    t('2台目が有効', (await A.verify(db, 'u1', b.sid, NOW)).ok);

    const old = await A.verify(db, 'u1', a.sid, NOW);
    if (A.ENFORCE_SINGLE_SESSION) {
      t('1台目が無効化される', !old.ok && old.code === 'REVOKED');
      t('無効化したsidを返す', b.revoked === 'sidA');
    } else {
      t('制限無効時は1台目も有効', old.ok);
      t('無効化は起きない', b.revoked === null);
    }
  }

  console.log('\n■ なりすまし防止');
  {
    const db = mockDb();
    await A.login(db, 'u1', {}, 'x', NOW, 'sidA');
    await A.login(db, 'u2', {}, 'y', NOW, 'sidB');
    t('他人のsidでは通らない', !(await A.verify(db, 'u1', 'sidB', NOW)).ok);
    t('存在しないsidは拒否', !(await A.verify(db, 'u1', 'sidZ', NOW)).ok);
    t('uid無しは拒否', !(await A.verify(db, null, 'sidA', NOW)).ok);
    t('sid無しは拒否', !(await A.verify(db, 'u1', null, NOW)).ok);
  }

  console.log('\n■ 有効期限');
  {
    const db = mockDb();
    const a = await A.login(db, 'u1', {}, 'x', NOW, 'sidA');
    const later = NOW + (A.SESSION_DAYS + 1) * 864e5;
    const r = await A.verify(db, 'u1', a.sid, later);
    t('31日後は期限切れ', !r.ok && r.code === 'EXPIRED');
    t('29日後は有効', (await A.verify(db, 'u1', a.sid, NOW + 29 * 864e5)).ok);
  }

  console.log('\n■ 書き込みの節約');
  {
    const db = mockDb();
    const a = await A.login(db, 'u1', {}, 'x', NOW, 'sidA');
    const w0 = db.writes;
    for (let i = 0; i < 20; i++) await A.verify(db, 'u1', a.sid, NOW + i * 1000);
    t('連続検証で書込ゼロ', db.writes === w0);

    await A.verify(db, 'u1', a.sid, NOW + A.TOUCH_MS + 1000);
    t('15分経過後は1回だけ書込', db.writes === w0 + 1);
  }

  console.log('\n■ ログアウト');
  {
    const db = mockDb();
    const a = await A.login(db, 'u1', {}, 'x', NOW, 'sidA');
    await A.logout(db, 'u1', a.sid);
    t('ログアウト後は無効', !(await A.verify(db, 'u1', a.sid, NOW)).ok);
    t('他人のsidはログアウトできない', !(await A.logout(db, 'u2', 'sidA')).ok);
  }

  console.log('\n■ 他端末を強制ログアウト');
  {
    const db = mockDb();
    await A.login(db, 'u1', {}, 'iPhone', NOW, 'sidA');
    const b = await A.login(db, 'u1', {}, 'PC', NOW, 'sidB');
    await A.logoutOthers(db, 'u1', b.sid);
    t('自分は残る', (await A.verify(db, 'u1', b.sid, NOW)).ok);
    t('他端末は消える', !(await A.verify(db, 'u1', 'sidA', NOW)).ok);
  }

  console.log('\n■ アカウント停止');
  {
    const db = mockDb();
    const a = await A.login(db, 'u1', {}, 'x', NOW, 'sidA');
    // 管理画面から停止された状態を再現
    const u = await db.get('users/u1');
    await db.set('users/u1', {...u, suspended: true});
    const r = await A.verify(db, 'u1', a.sid, NOW);
    t('停止中は検証を通さない', !r.ok && r.code === 'SUSPENDED');

    const again = await A.login(db, 'u1', {}, 'x', NOW, 'sidB');
    t('停止中は再ログインできない', again.suspended === true);
    t('新しいセッションを作らない', !(await db.get('sessions/sidB')));
  }

  console.log('\n■ 管理者は複数端末を使える');
  {
    const db = mockDb();
    const a = await A.login(db, 'adm', {}, 'PC', NOW, 'sidA', true);
    const b = await A.login(db, 'adm', {}, 'iPhone', NOW, 'sidB', true);
    t('1台目が生き残る', (await A.verify(db, 'adm', a.sid, NOW, true)).ok);
    t('2台目も有効', (await A.verify(db, 'adm', b.sid, NOW, true)).ok);
    t('締め出しは起きない', b.revoked === null);
  }

  console.log('\n■ 一般会員の同時ログイン制限');
  if (A.ENFORCE_SINGLE_SESSION) {
    const db = mockDb();
    const a = await A.login(db, 'u9', {}, 'PC', NOW, 'sidA', false);
    const b = await A.login(db, 'u9', {}, 'iPhone', NOW, 'sidB', false);
    t('1台目は無効化される', !(await A.verify(db, 'u9', a.sid, NOW, false)).ok);
    t('2台目のみ有効', (await A.verify(db, 'u9', b.sid, NOW, false)).ok);
  } else {
    const db = mockDb();
    const a = await A.login(db, 'u9', {}, 'PC', NOW, 'sidA', false);
    const b = await A.login(db, 'u9', {}, 'iPhone', NOW, 'sidB', false);
    t('制限無効: 1台目も残る', (await A.verify(db, 'u9', a.sid, NOW, false)).ok);
    t('制限無効: 2台目も有効', (await A.verify(db, 'u9', b.sid, NOW, false)).ok);
    console.log('  ※ ENFORCE_SINGLE_SESSION=false のため制限は無効です');
  }

  console.log(`\n合計: ${pass} 成功 / ${fail} 失敗`);
  process.exit(fail ? 1 : 0);
})();
