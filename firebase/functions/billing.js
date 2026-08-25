// 課金状態の管理。Stripe Webhook を受けて Firestore に反映する。
//
// 設計方針
//   Webhook は「落ちる」前提で組む。配送失敗・順序逆転が実際に起きる。
//   そのため currentPeriodEnd を必ず保存し、アクセス時に日付で判定する。
//   Webhook が届かなくても、期末日を過ぎれば自動的に閲覧できなくなる。

// 契約中とみなす Stripe のステータス
const ACTIVE = new Set(['active', 'trialing']);

/**
 * Webhook イベントを Firestore に反映する。
 * 冪等性: 同じイベントが複数回届いても結果が変わらないようにする。
 */
async function applyWebhook(db, event, now = Date.now()) {
  const type = event.type;
  const obj = event.data && event.data.object;
  if (!obj) return { ok: false, reason: 'no_object' };

  // uid は Checkout 作成時に metadata へ入れておく
  const uid = (obj.metadata && obj.metadata.uid)
    || (obj.subscription_details && obj.subscription_details.metadata
        && obj.subscription_details.metadata.uid);
  if (!uid) return { ok: false, reason: 'no_uid' };

  const ref = `subscriptions/${uid}`;
  const prev = await db.get(ref);

  // 古いイベントが遅れて届いた場合は無視する(順序逆転への対処)
  const created = event.created ? event.created * 1000 : now;
  if (prev && prev.updatedAt && created < prev.updatedAt) {
    return { ok: true, skipped: 'stale_event' };
  }

  switch (type) {
    case 'checkout.session.completed':
    case 'customer.subscription.created':
    case 'customer.subscription.updated': {
      const status = obj.status || 'active';
      await db.set(ref, {
        uid,
        status,
        stripeCustomerId: obj.customer || (prev && prev.stripeCustomerId) || null,
        stripeSubscriptionId: obj.subscription || obj.id
          || (prev && prev.stripeSubscriptionId) || null,
        // 期末日。これが実質的な有効期限になる。
        currentPeriodEnd: obj.current_period_end
          ? obj.current_period_end * 1000
          : (prev && prev.currentPeriodEnd) || null,
        cancelAtPeriodEnd: !!obj.cancel_at_period_end,
        updatedAt: created,
      });
      return { ok: true, status };
    }

    case 'customer.subscription.deleted': {
      await db.set(ref, {
        ...(prev || { uid }),
        status: 'canceled',
        cancelAtPeriodEnd: false,
        updatedAt: created,
      });
      return { ok: true, status: 'canceled' };
    }

    case 'invoice.payment_failed': {
      await db.set(ref, {
        ...(prev || { uid }),
        status: 'past_due',
        updatedAt: created,
      });
      return { ok: true, status: 'past_due' };
    }

    default:
      return { ok: true, skipped: type };
  }
}

/**
 * 閲覧可能かを判定する。
 * ステータスと期末日の両方を見る。片方だけでは穴が残る。
 */
function canAccess(sub, now = Date.now()) {
  if (!sub) return { ok: false, code: 'UNPAID' };
  if (!ACTIVE.has(sub.status)) return { ok: false, code: 'UNPAID' };
  // 期末日を過ぎていれば、ステータスが active でも許可しない
  if (sub.currentPeriodEnd && sub.currentPeriodEnd < now) {
    return { ok: false, code: 'EXPIRED' };
  }
  return { ok: true };
}

module.exports = { applyWebhook, canAccess, ACTIVE };
