// Cloud Functions のエントリポイント。
// 実処理は auth.js / billing.js / api.js に分離してある(テスト可能にするため)。

const functions = require('firebase-functions/v2/https');
const admin = require('firebase-admin');
const { defineSecret } = require('firebase-functions/params');

const { login, verify, logout, logoutOthers } = require('./auth');
const { applyWebhook, canAccess } = require('./billing');
const { getPredictions, ERR } = require('./api');
const { createCheckout, createPortal } = require('./checkout');
const adminOps = require('./admin');
const { resolvePlan } = require('./plans');

admin.initializeApp();
const fs = admin.firestore();

// シークレットは環境変数に置かず Secret Manager で管理する
const STRIPE_SECRET = defineSecret('STRIPE_SECRET_KEY');
const STRIPE_WEBHOOK_SECRET = defineSecret('STRIPE_WEBHOOK_SECRET');

const REGION = 'asia-northeast1';   // 東京。日本の利用者に近い方が速い

// Cookie を伴うリクエストでは、許可オリジンをワイルドカードにできない。
// 明示的に列挙し、レスポンスに Allow-Credentials を付ける。
const ORIGINS = [
  'https://chakuyomi.web.app',
  'https://chakuyomi.firebaseapp.com',
  'https://chakuyomi.jp',
  'http://localhost:5000',
];

/** CORS ヘッダを付与する。プリフライトならここで応答を完了する。 */
function cors(req, res) {
  const origin = req.get('origin');
  if (origin && ORIGINS.includes(origin)) {
    res.set('Access-Control-Allow-Origin', origin);
    res.set('Access-Control-Allow-Credentials', 'true');
    res.set('Vary', 'Origin');
  }
  res.set('Access-Control-Allow-Methods', 'GET,POST,OPTIONS');
  res.set('Access-Control-Allow-Headers', 'Authorization,Content-Type,x-session-id');
  res.set('Access-Control-Max-Age', '3600');
  if (req.method === 'OPTIONS') { res.status(204).send(''); return true; }
  return false;
}

// --- Firestore を auth.js が期待する形に薄くラップする ---
const db = {
  async get(path) {
    const snap = await fs.doc(path).get();
    return snap.exists ? snap.data() : null;
  },
  async set(path, value) { await fs.doc(path).set(value, { merge: false }); },
  async delete(path) { await fs.doc(path).delete(); },
  /** コレクションをページングして読む。全件読みを避けるための唯一の入口。 */
  async query(col, { limit = 20, cursor = null, orderBy = 'lastLoginAt' } = {}) {
    let q = fs.collection(col).orderBy(orderBy, 'desc').limit(limit);
    if (cursor) {
      const snap = await fs.doc(`${col}/${cursor}`).get();
      if (snap.exists) q = q.startAfter(snap);
    }
    const snap = await q.get();
    const items = snap.docs.map(d => ({ uid: d.id, ...d.data() }));
    return { items, next: items.length === limit ? items[items.length - 1].uid : null };
  },
};

/** ID トークンを検証して uid を取り出す。 */
async function authenticate(req) {
  const h = req.headers.authorization || '';
  if (!h.startsWith('Bearer ')) return null;
  try {
    return await admin.auth().verifyIdToken(h.slice(7));
  } catch {
    return null;   // 期限切れ・改竄はすべて未認証扱い
  }
}

/**
 * 管理者かを判定する。
 * Custom Claims のみを見る。Firestore の値では判定しない
 * (書き換えられた時点で権限昇格になるため)。
 */
async function requireAdmin(req, res) {
  const token = await authenticate(req);
  if (!token) { send(res, { ...ERR.UNAUTH, ok: false }); return null; }
  if (token.admin !== true) {
    send(res, { ok: false, code: 403, msg: '権限がありません' });
    return null;
  }
  return token;
}

function send(res, result) {
  if (result.ok) return res.status(200).json(result);
  return res.status(result.code || 400).json({ error: result.msg, code: result.code2 });
}

// ===== ログイン =====
// LINE でのサインイン成功後、クライアントがこれを呼んでセッションを作る。
exports.sessionLogin = functions.onRequest({ region: REGION }, async (req, res) => {
    if (cors(req, res)) return;
  if (req.method !== 'POST') return res.status(405).end();
  const token = await authenticate(req);
  if (!token) return send(res, { ...ERR.UNAUTH, ok: false });

  const device = (req.body && req.body.device) || req.get('user-agent') || 'unknown';
  const r = await login(db, token.uid, token, String(device).slice(0, 120),
    Date.now(), null, token.admin === true);
  if (r.suspended) {
    return send(res, { ok: false, code: 403, msg: 'このアカウントは利用を停止されています' });
  }
  // サイト(web.app)とAPI(run.app)はドメインが異なるため、
  // SameSite=Lax の Cookie は送信されない。
  // セッションIDは本文で返し、以降は x-session-id ヘッダで送ってもらう。
  res.set('Set-Cookie',
    `sid=${r.sid}; HttpOnly; Secure; SameSite=None; Path=/; Max-Age=${30 * 86400}`);
  return res.status(200).json({ ok: true, sid: r.sid, revoked: !!r.revoked });
});

// ===== ログアウト =====
exports.sessionLogout = functions.onRequest({ region: REGION }, async (req, res) => {
    if (cors(req, res)) return;
  const token = await authenticate(req);
  if (!token) return send(res, { ...ERR.UNAUTH, ok: false });
  const sid = readSid(req);
  await logout(db, token.uid, sid);
  res.set('Set-Cookie', 'sid=; HttpOnly; Secure; SameSite=None; Path=/; Max-Age=0');
  return res.status(200).json({ ok: true });
});

// ===== 他端末をログアウト(マイページ) =====
exports.logoutOtherDevices = functions.onRequest({ region: REGION }, async (req, res) => {
    if (cors(req, res)) return;
  const token = await authenticate(req);
  if (!token) return send(res, { ...ERR.UNAUTH, ok: false });
  await logoutOthers(db, token.uid, readSid(req));
  return res.status(200).json({ ok: true });
});

// ===== 予測データ配信 =====
exports.api = functions.onRequest({ region: REGION }, async (req, res) => {
    if (cors(req, res)) return;
  const token = await authenticate(req);
  const date = (req.query.date || '').toString().slice(0, 10) || todayJST();

  const result = await getPredictions(db, {
    uid: token && token.uid,
    sid: readSid(req),
    isAdmin: !!(token && token.admin === true),
    date,
    ip: req.ip || (req.get('x-forwarded-for') || '').split(',')[0],
  }, loadRaces);

  // 予測データは全会員共通なので短時間キャッシュしてよい
  if (result.ok) res.set('Cache-Control', 'private, max-age=60');
  return send(res, result);
});

// ===== 契約状況(マイページ) =====
exports.me = functions.onRequest({ region: REGION }, async (req, res) => {
    if (cors(req, res)) return;
  const token = await authenticate(req);
  if (!token) return send(res, { ...ERR.UNAUTH, ok: false });

  const v = await verify(db, token.uid, readSid(req), Date.now(), token.admin === true);
  if (!v.ok) return send(res, { ...ERR[v.code], ok: false, code2: v.code });

  const sub = await db.get(`subscriptions/${token.uid}`);
  const user = await db.get(`users/${token.uid}`);
  const sess = await db.get(`sessions/${readSid(req)}`);

  const p = resolvePlan(user, sub, Date.now());

  return res.status(200).json({
    ok: true,
    displayName: (user && user.displayName) || null,
    photoURL: (user && user.photoURL) || null,
    plan: p.plan,               // free | paid | invited
    planLabel: p.label,
    fullAccess: p.fullAccess,
    planUntil: p.until,
    subscription: sub ? {
      status: sub.status,
      currentPeriodEnd: sub.currentPeriodEnd,
      cancelAtPeriodEnd: !!sub.cancelAtPeriodEnd,
    } : null,
    device: sess ? { name: sess.device, lastSeen: sess.lastSeen } : null,
  });
});

// ===== 決済ページへ =====
exports.createCheckout = functions.onRequest(
  { region: REGION, secrets: [STRIPE_SECRET] },
  async (req, res) => {
    if (cors(req, res)) return;
    const token = await authenticate(req);
    if (!token) return send(res, { ...ERR.UNAUTH, ok: false });

    const v = await verify(db, token.uid, readSid(req), Date.now(), token.admin === true);
    if (!v.ok) return send(res, { ...ERR[v.code], ok: false, code2: v.code });

    const stripe = require('stripe')(STRIPE_SECRET.value());
    const origin = req.get('origin') || 'https://chakuyomi.jp';
    const r = await createCheckout(stripe, db, token.uid, origin, token.email);
    return send(res, r);
  });

// ===== 契約管理(解約・カード変更) =====
exports.createPortal = functions.onRequest(
  { region: REGION, secrets: [STRIPE_SECRET] },
  async (req, res) => {
    if (cors(req, res)) return;
    const token = await authenticate(req);
    if (!token) return send(res, { ...ERR.UNAUTH, ok: false });

    const v = await verify(db, token.uid, readSid(req), Date.now(), token.admin === true);
    if (!v.ok) return send(res, { ...ERR[v.code], ok: false, code2: v.code });

    const stripe = require('stripe')(STRIPE_SECRET.value());
    const origin = req.get('origin') || 'https://chakuyomi.jp';
    const r = await createPortal(stripe, db, token.uid, origin);
    return send(res, r);
  });

// ===== 管理API =====
// すべて requireAdmin を通す。1つでも忘れると全体が無意味になるため、
// ハンドラの冒頭で必ず呼ぶ。
exports.admin = functions.onRequest({ region: REGION }, async (req, res) => {
  if (cors(req, res)) return;
  const token = await requireAdmin(req, res);
  if (!token) return;

  const action = (req.query.action || (req.body && req.body.action) || '').toString();
  const uid = (req.query.uid || (req.body && req.body.uid) || '').toString();
  const b = req.body || {};

  try {
    switch (action) {
      case 'list':
        return send(res, await adminOps.listUsers(db, {
          limit: Math.min(Number(req.query.limit) || 20, 50),
          cursor: req.query.cursor || null,
        }));
      case 'user':
        return send(res, await adminOps.getUser(db, uid));
      case 'setPlan':
        return send(res, await adminOps.setPlan(
          db, token.uid, uid, b.plan, b.days, b.reason || ''));
      case 'grant':
        return send(res, await adminOps.grantAccess(
          db, token.uid, uid, b.days, b.reason || ''));
      case 'revoke':
        return send(res, await adminOps.revokeAccess(
          db, token.uid, uid, b.reason || ''));
      case 'suspend':
        return send(res, await adminOps.suspendUser(
          db, token.uid, uid, b.reason || ''));
      case 'unsuspend':
        return send(res, await adminOps.unsuspendUser(db, token.uid, uid));
      case 'stats':
        return send(res, await adminOps.getStats(db));
      case 'whoami': {
        // 実行時のサービスアカウントを確認するための診断用
        let sa = 'unknown';
        try {
          const r = await fetch(
            'http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/email',
            { headers: { 'Metadata-Flavor': 'Google' } });
          sa = await r.text();
        } catch (e) { sa = 'metadata error: ' + e.message; }
        return send(res, { ok: true, serviceAccount: sa, project: process.env.GCLOUD_PROJECT });
      }
      default:
        return send(res, { ok: false, code: 400, msg: '不明な操作です' });
    }
  } catch (e) {
    console.error('admin error:', e);
    return send(res, { ok: false, code: 500, msg: '処理に失敗しました' });
  }
});

// ===== Stripe Webhook =====
// 署名を検証しないと、誰でも「契約中」を偽装できてしまう。
exports.stripeWebhook = functions.onRequest(
  { region: REGION, secrets: [STRIPE_SECRET, STRIPE_WEBHOOK_SECRET] },
  async (req, res) => {
    if (cors(req, res)) return;
    const stripe = require('stripe')(STRIPE_SECRET.value());
    let event;
    try {
      event = stripe.webhooks.constructEvent(
        req.rawBody, req.get('stripe-signature'), STRIPE_WEBHOOK_SECRET.value());
    } catch (e) {
      console.error('署名検証に失敗:', e.message);
      return res.status(400).send('invalid signature');
    }
    try {
      const r = await applyWebhook(db, event);
      return res.status(200).json(r);
    } catch (e) {
      console.error('Webhook処理に失敗:', e);
      // 500を返すと Stripe が再送してくれる
      return res.status(500).send('error');
    }
  });

// --- 補助 ---
function readSid(req) {
  const c = req.get('cookie') || '';
  const m = c.match(/(?:^|;\s*)sid=([^;]+)/);
  return m ? m[1] : (req.get('x-session-id') || null);
}

function todayJST() {
  const d = new Date(Date.now() + 9 * 3600 * 1000);
  return d.toISOString().slice(0, 10);
}

/**
 * 予測データを読む。
 *
 * Hosting に置いた静的JSONを取得する。
 *   - Firestore に置くと読み取り課金が跳ね上がる
 *   - Storage はバケット作成が要り、CDN も効かない
 * Hosting なら CDN で配信され、転送量も無料枠に収まる。
 */
// インスタンス内の短期キャッシュ。
// 長く持つとデータ更新が反映されないため、30秒に留める。
// (当日は展示タイム発表のたびに内容が変わる)
const RACE_CACHE = new Map();
const RACE_CACHE_MS = 30 * 1000;

async function loadRaces(date) {
  const hit = RACE_CACHE.get(date);
  if (hit && Date.now() - hit.at < RACE_CACHE_MS) return hit.data;

  const url = `https://chakuyomi.web.app/data/${date}.json?t=${Date.now()}`;
  const res = await fetch(url, { cache: 'no-store' });
  if (!res.ok) {
    RACE_CACHE.set(date, { at: Date.now(), data: { races: [], summary: null } });
    return { races: [], summary: null };
  }
  const json = await res.json();
  // 旧形式は配列、新形式は {races, summary}
  const data = Array.isArray(json) ? { races: json, summary: null } : json;
  RACE_CACHE.set(date, { at: Date.now(), data });
  return data;
}
