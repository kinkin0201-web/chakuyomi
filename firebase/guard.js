// Functions の共通ガード。
// 認証・課金・セッション・レート制限を1か所に集約し、
// 「この関数だけ検証を忘れた」という事故を構造的に防ぐ。

const DENY = {
  UNAUTH:    {code: 401, msg: 'ログインしてください'},
  REVOKED:   {code: 401, msg: '別の端末でログインされました'},
  EXPIRED:   {code: 401, msg: '再度ログインしてください'},
  UNPAID:    {code: 402, msg: 'ご契約が確認できません'},
  RATE:      {code: 429, msg: 'アクセスが集中しています。少し待ってからお試しください'},
  FORBIDDEN: {code: 403, msg: '権限がありません'},
};

// レート制限(uid単位)。本番は Firestore か Memorystore で共有する。
const buckets = new Map();
function rateLimit(key, limit = 60, windowMs = 60000, now = Date.now()) {
  const b = buckets.get(key);
  if (!b || now - b.start > windowMs) {
    buckets.set(key, {start: now, n: 1});
    return true;
  }
  if (b.n >= limit) return false;
  b.n++;
  return true;
}

// 検証の本体。すべての API はこれを通す。
async function guard(req, db, {requirePaid = true, requireAdmin = false, now = Date.now()} = {}) {
  const token = req.auth;                    // 検証済み ID トークン
  if (!token) return {ok: false, ...DENY.UNAUTH};

  if (!rateLimit(token.uid, requireAdmin ? 300 : 60, 60000, now)) {
    return {ok: false, ...DENY.RATE};
  }

  // 管理者判定は Custom Claims のみ。Firestore の値は信用しない。
  // (Firestore で判定すると、その文書を書き換えられた時に昇格される)
  if (requireAdmin && token.admin !== true) {
    return {ok: false, ...DENY.FORBIDDEN};
  }

  const user = await db.get(`users/${token.uid}`);
  if (!user) return {ok: false, ...DENY.UNAUTH};

  // --- 同時ログイン1つの検証 ---
  const sid = req.sid;
  if (!sid || user.activeSid !== sid) return {ok: false, ...DENY.REVOKED};
  const sess = await db.get(`sessions/${sid}`);
  if (!sess) return {ok: false, ...DENY.REVOKED};
  if (now - sess.issuedAt > 30 * 864e5) return {ok: false, ...DENY.EXPIRED};

  // --- 課金状態の検証(解約後の閲覧を止める) ---
  if (requirePaid && !requireAdmin) {
    const sub = await db.get(`subscriptions/${token.uid}`);
    const active = sub && sub.status === 'active' && sub.currentPeriodEnd > now;
    if (!active) return {ok: false, ...DENY.UNPAID};
  }

  return {ok: true, uid: token.uid, admin: token.admin === true, plan: user.plan};
}

module.exports = {guard, rateLimit, DENY};

// ===== 検証 =====
if (require.main === module) {
  const now = Date.now();
  const db = {data: new Map(), async get(p) { return this.data.get(p) || null; }};
  db.data.set('users/u1', {activeSid: 's1', plan: 'standard'});
  db.data.set('sessions/s1', {uid: 'u1', issuedAt: now - 864e5});
  db.data.set('subscriptions/u1', {status: 'active', currentPeriodEnd: now + 30 * 864e5});
  db.data.set('users/u2', {activeSid: 's2', plan: 'standard'});
  db.data.set('sessions/s2', {uid: 'u2', issuedAt: now - 864e5});
  db.data.set('subscriptions/u2', {status: 'canceled', currentPeriodEnd: now - 864e5});
  db.data.set('users/adm', {activeSid: 'sa', plan: 'admin'});
  db.data.set('sessions/sa', {uid: 'adm', issuedAt: now});

  let pass = 0, fail = 0;
  const t = async (name, req, opt, expect) => {
    const r = await guard(req, db, {...opt, now});
    const got = r.ok ? 'ok' : r.code;
    const good = String(got) === String(expect);
    good ? pass++ : fail++;
    console.log(`${good ? '  OK' : '  NG'}  ${name} -> ${r.ok ? '許可' : r.code + ' ' + r.msg}`);
  };

  (async () => {
    console.log('■ 正常系');
    await t('契約中の会員', {auth: {uid: 'u1'}, sid: 's1'}, {}, 'ok');

    console.log('\n■ 認証');
    await t('未ログイン', {auth: null, sid: 's1'}, {}, 401);
    await t('sid不一致(別端末でログイン)', {auth: {uid: 'u1'}, sid: '古いsid'}, {}, 401);

    console.log('\n■ 課金');
    await t('解約済みの会員', {auth: {uid: 'u2'}, sid: 's2'}, {}, 402);
    await t('解約済みでも無料機能は可', {auth: {uid: 'u2'}, sid: 's2'}, {requirePaid: false}, 'ok');

    console.log('\n■ 管理者');
    await t('一般会員が管理APIを叩く', {auth: {uid: 'u1'}, sid: 's1'}, {requireAdmin: true}, 403);
    await t('claimsを持つ管理者', {auth: {uid: 'adm', admin: true}, sid: 'sa'}, {requireAdmin: true}, 'ok');
    await t('Firestoreのplan=adminでは昇格しない', {auth: {uid: 'adm'}, sid: 'sa'}, {requireAdmin: true}, 403);

    console.log('\n■ レート制限');
    for (let i = 0; i < 60; i++) rateLimit('u1', 60, 60000, now);
    await t('61回目は拒否', {auth: {uid: 'u1'}, sid: 's1'}, {}, 429);

    console.log(`\n合計: ${pass} 成功 / ${fail} 失敗`);
    process.exit(fail ? 1 : 0);
  })();
}
