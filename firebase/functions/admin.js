// 会員管理。開発者のみが実行できる操作をまとめる。
//
// 権限判定は Custom Claims (token.admin === true) のみ。
// Firestore の値で判定すると、その文書を書き換えられた時点で
// 権限昇格が成立してしまう。

const PAGE_SIZE = 20;
const VALID_PLANS = new Set(['free', 'paid', 'invited']);

/** 監査ログを残す。誰が何をしたか後から追えるようにする。 */
async function audit(db, actor, action, target, detail = {}, now = Date.now()) {
  const id = `${now}_${Math.random().toString(36).slice(2, 8)}`;
  await db.set(`audit_logs/${id}`, {
    at: now, actor, action, target, detail,
  });
  return id;
}

/**
 * 会員一覧。必ずページングする。
 * 全件読むと1回の表示で会員数ぶんの読み取りが発生し、無料枠を使い切る。
 */
async function listUsers(db, { limit = PAGE_SIZE, cursor = null } = {}) {
  const rows = await db.query('users', { limit, cursor, orderBy: 'lastLoginAt' });
  return { ok: true, users: rows.items, nextCursor: rows.next };
}

/** 会員1件の詳細。契約状況も併せて返す。 */
async function getUser(db, uid) {
  const user = await db.get(`users/${uid}`);
  if (!user) return { ok: false, code: 404, msg: '会員が見つかりません' };
  const sub = await db.get(`subscriptions/${uid}`);
  return { ok: true, user: { uid, ...user }, subscription: sub };
}

/**
 * 特定の会員に無料アクセスを付与する。
 *
 * Stripe を介さず Firestore 側だけで完結させる。
 * 招待・関係者・不具合のお詫びなどで使う。
 * grantedBy を残し、通常の課金と区別できるようにする。
 */
async function grantAccess(db, actor, uid, days, reason = '', now = Date.now()) {
  const user = await db.get(`users/${uid}`);
  if (!user) return { ok: false, code: 404, msg: '会員が見つかりません' };

  const d = Number(days);
  if (!Number.isFinite(d) || d <= 0 || d > 3650) {
    return { ok: false, code: 400, msg: '日数は1〜3650で指定してください' };
  }

  const prev = await db.get(`subscriptions/${uid}`);
  // 既に期限がある場合は延長する(上書きすると短くなる恐れがある)
  const base = (prev && prev.currentPeriodEnd && prev.currentPeriodEnd > now)
    ? prev.currentPeriodEnd : now;
  const until = base + d * 864e5;

  await db.set(`subscriptions/${uid}`, {
    ...(prev || {}),
    uid,
    status: 'active',
    currentPeriodEnd: until,
    cancelAtPeriodEnd: false,
    // 手動付与の印。Stripe の Webhook と取り違えないようにする。
    grantedBy: actor,
    grantReason: reason.slice(0, 200),
    updatedAt: now,
  });

  await audit(db, actor, 'grant_access', uid, { days: d, until, reason }, now);
  return { ok: true, until, days: d };
}

/**
 * プランを設定する。
 *
 * invited は課金せずに全機能を開放する。日数を指定すれば期限付き、
 * 省略すれば無期限。free を指定すると招待を打ち切る。
 * paid は Stripe が管理するため、ここでは設定させない。
 */
async function setPlan(db, actor, uid, plan, days = null, reason = '', now = Date.now()) {
  const user = await db.get(`users/${uid}`);
  if (!user) return { ok: false, code: 404, msg: '会員が見つかりません' };
  if (!VALID_PLANS.has(plan)) {
    return { ok: false, code: 400, msg: 'プランの指定が不正です' };
  }
  if (plan === 'paid') {
    return { ok: false, code: 400, msg: '有料プランはStripeの課金でのみ設定されます' };
  }

  let until = null;
  if (plan === 'invited' && days != null && days !== '') {
    const d = Number(days);
    if (!Number.isFinite(d) || d <= 0 || d > 3650) {
      return { ok: false, code: 400, msg: '日数は1〜3650で指定してください' };
    }
    until = now + d * 864e5;
  }

  await db.set(`users/${uid}`, {
    ...user,
    planOverride: plan === 'free' ? 'free' : plan,
    planUntil: until,
    planSetBy: actor,
    planReason: String(reason).slice(0, 200),
    planSetAt: now,
  });

  await audit(db, actor, 'set_plan', uid, { plan, until, reason }, now);
  return { ok: true, plan, until };
}

/** 手動付与を取り消す。Stripe 契約は対象にしない。 */
async function revokeAccess(db, actor, uid, reason = '', now = Date.now()) {
  const sub = await db.get(`subscriptions/${uid}`);
  if (!sub) return { ok: false, code: 404, msg: '契約が見つかりません' };
  if (!sub.grantedBy) {
    return { ok: false, code: 409, msg: 'Stripe契約は管理画面から取り消せません' };
  }
  await db.set(`subscriptions/${uid}`, {
    ...sub, status: 'canceled', currentPeriodEnd: now, updatedAt: now,
  });
  await audit(db, actor, 'revoke_access', uid, { reason }, now);
  return { ok: true };
}

/**
 * アカウントを停止する。規約違反時に即座にログインできなくする。
 * セッションも破棄し、開いている画面からも締め出す。
 */
async function suspendUser(db, actor, uid, reason = '', now = Date.now()) {
  const user = await db.get(`users/${uid}`);
  if (!user) return { ok: false, code: 404, msg: '会員が見つかりません' };
  if (user.activeSid) await db.delete(`sessions/${user.activeSid}`);
  await db.set(`users/${uid}`, {
    ...user, suspended: true, suspendedAt: now,
    suspendReason: reason.slice(0, 200), activeSid: null,
  });
  await audit(db, actor, 'suspend', uid, { reason }, now);
  return { ok: true };
}

/** 停止を解除する。 */
async function unsuspendUser(db, actor, uid, now = Date.now()) {
  const user = await db.get(`users/${uid}`);
  if (!user) return { ok: false, code: 404, msg: '会員が見つかりません' };
  await db.set(`users/${uid}`, {
    ...user, suspended: false, suspendedAt: null, suspendReason: null,
  });
  await audit(db, actor, 'unsuspend', uid, {}, now);
  return { ok: true };
}

/**
 * 集計。全会員を読むと無料枠を圧迫するため、
 * 日次で作った集計文書を1回読むだけにする。
 */
async function getStats(db) {
  const s = await db.get('stats/summary');
  return { ok: true, stats: s || null };
}

module.exports = {
  listUsers, getUser, setPlan, grantAccess, revokeAccess,
  suspendUser, unsuspendUser, getStats, audit, PAGE_SIZE,
};
