// Stripe の決済導線。
//
// カード情報は当サイトを通らない。Stripe がホストする画面へ遷移させ、
// 完了後に戻ってくる。これにより PCI 準拠の負担を負わずに済む。

const { STRIPE_PRICE_ID } = require('./config');

/**
 * 決済ページのURLを作る。
 *
 * metadata に uid を必ず入れる。Webhook はこれを見て
 * 「どの会員の支払いか」を判別するため、抜けると課金が紐づかない。
 */
async function createCheckout(stripe, db, uid, origin, email = null) {
  const prev = await db.get(`subscriptions/${uid}`);

  // 既に契約中なら二重課金させない
  if (prev && (prev.status === 'active' || prev.status === 'trialing')) {
    return { ok: false, code: 409, msg: 'すでにご契約いただいています' };
  }

  const session = await stripe.checkout.sessions.create({
    mode: 'subscription',
    line_items: [{ price: STRIPE_PRICE_ID, quantity: 1 }],
    // 再契約の場合は既存の顧客に紐づける(カード再入力を省ける)
    customer: (prev && prev.stripeCustomerId) || undefined,
    customer_email: (prev && prev.stripeCustomerId) ? undefined : (email || undefined),
    success_url: `${origin}/welcome?ok=1`,
    cancel_url: `${origin}/?canceled=1`,
    // Webhook 側で uid を取れるよう、両方に入れておく。
    // セッション側だけだと subscription 系イベントで拾えないことがある。
    metadata: { uid },
    subscription_data: { metadata: { uid } },
    locale: 'ja',
  });

  return { ok: true, url: session.url };
}

/**
 * 契約管理ページ(解約・カード変更)のURLを作る。
 * 解約フローを自前で実装しないための仕組み。
 */
async function createPortal(stripe, db, uid, origin) {
  const sub = await db.get(`subscriptions/${uid}`);
  if (!sub || !sub.stripeCustomerId) {
    return { ok: false, code: 404, msg: 'ご契約が見つかりません' };
  }
  const portal = await stripe.billingPortal.sessions.create({
    customer: sub.stripeCustomerId,
    return_url: `${origin}/mypage`,
    locale: 'ja',
  });
  return { ok: true, url: portal.url };
}

module.exports = { createCheckout, createPortal };
