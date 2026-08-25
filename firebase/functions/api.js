// 予測データ配信API。すべてのリクエストが認証と課金を通る。
//
// 無料枠の設計
//   未契約でも1日3レースまで見られる。登録不要で精度を体感してもらう。
//   無料分は「確信度の高い順」ではなく日付から決定的に選ぶ。
//   毎日同じレースが出ると価値が伝わらないため。

const { verify } = require('./auth');
const { resolvePlan } = require('./plans');

// 場ごとに開放するレース数。
// 全体から3レースだけ選ぶと、ほとんどの場で無料枠がゼロになり
// 「どの場を開いても試せない」状態になるため、場単位で配る。
const FREE_RACES_PER_STADIUM = 1;

const ERR = {
  UNAUTH:  { code: 401, msg: 'ログインしてください' },
  // 同時ログイン制限が無効な間は「別の端末で〜」と断定しない。
  // セッション切れ・Cookie 未送信でも同じコードになるため。
  REVOKED: { code: 401, msg: 'ログインし直してください' },
  EXPIRED: { code: 401, msg: '再度ログインしてください' },
  SUSPENDED: { code: 403, msg: 'このアカウントは利用を停止されています' },
  UNPAID:  { code: 402, msg: 'ご契約が確認できません' },
  RATE:    { code: 429, msg: 'アクセスが集中しています。少し待ってからお試しください' },
};

// レート制限。本番は Firestore か Memorystore で共有する。
const buckets = new Map();
function rateLimit(key, limit, windowMs, now) {
  const b = buckets.get(key);
  if (!b || now - b.start > windowMs) { buckets.set(key, { start: now, n: 1 }); return true; }
  if (b.n >= limit) return false;
  b.n++; return true;
}

/**
 * 無料で見せるレースを決める。
 * 日付とレースIDから決定的に選ぶので、同じ日なら誰が見ても同じ。
 * ランダムだと「更新すれば別のが見える」抜け道になる。
 */
function pickFreeRaces(races, date, n = FREE_RACES_PER_STADIUM) {
  // 日付とレースIDを混ぜてハッシュする。
  // 単純な加算では日付が1違っても並び順が変わらないため、
  // 文字列を1文字ずつ畳み込む方式にする(FNV系)。
  const hash = (str) => {
    let h = 2166136261;
    for (let i = 0; i < str.length; i++) {
      h ^= str.charCodeAt(i);
      h = Math.imul(h, 16777619) >>> 0;
    }
    return h;
  };

  // 全体から選ぶと特定の場に偏り、他の場では無料枠がゼロになる。
  // どの場を開いても試せるよう、場ごとに n レースずつ開放する。
  const byStadium = new Map();
  for (const r of races) {
    if (!byStadium.has(r.stadium)) byStadium.set(r.stadium, []);
    byStadium.get(r.stadium).push(r);
  }

  const free = new Set();
  for (const [stadium, list] of byStadium) {
    const scored = list.map(r => ({ r, h: hash(date + '#' + r.raceId) }));
    scored.sort((a, b) => a.h - b.h);
    for (const x of scored.slice(0, n)) free.add(x.r.raceId);
  }
  return free;
}

/**
 * 予測データを返す。契約状況で内容を出し分ける。
 */
async function getPredictions(db, req, loader, now = Date.now()) {
  const { uid, sid, date, ip, isAdmin } = req;

  // 未ログインでも無料分は見せる。IP単位でレート制限する。
  if (!uid) {
    if (!rateLimit(`ip:${ip || 'x'}`, 30, 60000, now)) return { ...ERR.RATE, ok: false };
    const { races: all, summary } = norm(await loader(date));
    const free = pickFreeRaces(all, date);
    return {
      ok: true, plan: 'guest', freeLimit: FREE_RACES_PER_STADIUM, summary,
      races: all.map(r => free.has(r.raceId) ? r : mask(r)),
    };
  }

  if (!rateLimit(`uid:${uid}`, 60, 60000, now)) return { ...ERR.RATE, ok: false };

  const v = await verify(db, uid, sid, now, isAdmin);
  if (!v.ok) return { ...ERR[v.code], ok: false, code2: v.code };

  const sub = await db.get(`subscriptions/${uid}`);
  const user = await db.get(`users/${uid}`);
  // 招待プランは課金なしで全機能を使える。管理者も同様(検証のため)。
  const p = resolvePlan(user, sub, now);
  const full = isAdmin || p.fullAccess;

  const { races: all, summary } = norm(await loader(date));
  if (!full) {
    // ログイン済みだが未契約 -> 無料分のみ
    const free = pickFreeRaces(all, date);
    return {
      ok: true, plan: p.plan, freeLimit: FREE_RACES_PER_STADIUM, summary,
      races: all.map(r => free.has(r.raceId) ? r : mask(r)),
    };
  }

  return { ok: true, plan: isAdmin ? 'admin' : p.plan, races: all, summary };
}

/** loader の戻り値を {races, summary} に正規化する(旧形式は配列)。 */
function norm(v) {
  if (Array.isArray(v)) return { races: v, summary: null };
  return { races: (v && v.races) || [], summary: (v && v.summary) || null };
}

/** 未契約者に見せない部分を伏せる。存在は見せて、中身だけ隠す。 */
function mask(r) {
  return {
    raceId: r.raceId, stadium: r.stadium, stadiumName: r.stadiumName,
    no: r.no, title: r.title, locked: true,
  };
}

module.exports = { getPredictions, pickFreeRaces, mask, FREE_RACES_PER_STADIUM, ERR, rateLimit };
