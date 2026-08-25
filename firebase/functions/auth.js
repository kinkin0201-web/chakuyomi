// 認証・セッション管理。同時ログイン1つを保証する。
//
// Firestore 構造
//   users/{uid}         { activeSid, plan, lineUserId, createdAt }
//   sessions/{sid}      { uid, device, issuedAt, lastSeen }
//   subscriptions/{uid} { status, currentPeriodEnd, stripeCustomerId }

const SESSION_DAYS = 30;
const TOUCH_MS = 15 * 60 * 1000;   // lastSeen の更新間隔(書込を節約)

// 同時ログイン1つの制限を有効にするか。
//
// 開発中は複数端末・複数ブラウザを行き来するため邪魔になるので false。
// パスワード共有を防ぐための機能なので、本番公開前に true へ戻すこと。
const ENFORCE_SINGLE_SESSION = false;

/**
 * ID トークンからプロフィール値を取り出す。
 *
 * Firebase は OIDC の claim をトップレベルに置く場合と、
 * firebase.identities / 生の claim 側に残す場合がある。
 * どこにあっても拾えるように順に探す。
 */
function pick(profile, keys) {
  if (!profile) return null;
  const buckets = [profile, profile.firebase, profile.claims];
  for (const b of buckets) {
    if (!b) continue;
    for (const k of keys) {
      if (b[k]) return b[k];
    }
  }
  return null;
}

function newSid(random = null) {
  // 本番は crypto.randomUUID() を使う。テスト時は差し替え可能にする。
  if (random) return random;
  const c = globalThis.crypto;
  return 'sid_' + (c && c.randomUUID ? c.randomUUID().replace(/-/g, '') : '');
}

/**
 * ログイン。既存セッションを無効化してから新規発行する。
 * トランザクションで実行し、同時ログインの競合を防ぐ。
 */
async function login(db, uid, profile, device, now = Date.now(), sid = null, isAdmin = false) {
  const userRef = `users/${uid}`;
  const existing = await db.get(userRef);
  // 停止中のアカウントはログインさせない
  if (existing && existing.suspended) {
    return { suspended: true };
  }
  const user = existing || {
    plan: 'free', createdAt: now, lineUserId: profile && profile.sub,
  };

  // 既存セッションを削除 -> 前の端末は次のアクセスで弾かれる。
  // ただし管理者は対象外。管理画面と会員画面を同時に開く運用があり、
  // 相互に締め出し合うと作業にならない。
  let revoked = null;
  if (user.activeSid && !isAdmin && ENFORCE_SINGLE_SESSION) {
    await db.delete(`sessions/${user.activeSid}`);
    revoked = user.activeSid;
  }

  const newId = newSid(sid);
  await db.set(`sessions/${newId}`, {
    uid, device: device || 'unknown', issuedAt: now, lastSeen: now,
  });
  await db.set(userRef, {
    ...user,
    // LINE のプロフィール(名前・アイコン)を保存する。
    // マイページで表示するため、ログインのたびに最新化する。
    displayName: pick(profile, ['name', 'displayName']) || user.displayName || null,
    photoURL: pick(profile, ['picture', 'photoURL']) || user.photoURL || null,
    // 管理者・制限無効時は既存の activeSid を保持し、複数端末を並行利用できるようにする
    activeSid: (isAdmin || !ENFORCE_SINGLE_SESSION) && user.activeSid
      ? user.activeSid : newId,
    lastLoginAt: now,
  });
  return { sid: newId, revoked };
}

/**
 * セッション検証。sessions と users の両方が一致して初めて有効。
 * 片方だけの確認では、古い端末を締め出せない。
 */
async function verify(db, uid, sid, now = Date.now(), isAdmin = false) {
  if (!uid || !sid) return { ok: false, code: 'UNAUTH' };

  const sess = await db.get(`sessions/${sid}`);
  if (!sess || sess.uid !== uid) return { ok: false, code: 'REVOKED' };

  const user = await db.get(`users/${uid}`);
  if (!user) return { ok: false, code: 'REVOKED' };
  // 管理者は複数端末を許可する(同時ログイン制限は会員向けの機能)
  if (ENFORCE_SINGLE_SESSION && !isAdmin && user.activeSid !== sid) {
    return { ok: false, code: 'REVOKED' };
  }
  // 利用中に停止された場合も次のアクセスで弾く
  if (user.suspended) return { ok: false, code: 'SUSPENDED' };

  if (now - sess.issuedAt > SESSION_DAYS * 864e5) {
    return { ok: false, code: 'EXPIRED' };
  }

  // 15分に1回だけ更新する。毎回書くと無料枠を圧迫する。
  if (now - sess.lastSeen > TOUCH_MS) {
    await db.set(`sessions/${sid}`, { ...sess, lastSeen: now });
  }
  return { ok: true, uid, plan: user.plan };
}

/** 明示的なログアウト。 */
async function logout(db, uid, sid) {
  const sess = await db.get(`sessions/${sid}`);
  if (!sess || sess.uid !== uid) return { ok: false };
  await db.delete(`sessions/${sid}`);
  const user = await db.get(`users/${uid}`);
  if (user && user.activeSid === sid) {
    await db.set(`users/${uid}`, { ...user, activeSid: null });
  }
  return { ok: true };
}

/** 他端末を強制ログアウト(マイページの機能)。 */
async function logoutOthers(db, uid, keepSid) {
  const user = await db.get(`users/${uid}`);
  if (!user) return { ok: false };
  if (user.activeSid && user.activeSid !== keepSid) {
    await db.delete(`sessions/${user.activeSid}`);
  }
  await db.set(`users/${uid}`, { ...user, activeSid: keepSid });
  return { ok: true };
}

module.exports = {
  login, verify, logout, logoutOthers,
  SESSION_DAYS, TOUCH_MS, ENFORCE_SINGLE_SESSION,
};
