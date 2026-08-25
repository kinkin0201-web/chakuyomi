// プランの定義と判定。
//
//   free   : 無料。各場1レースのみ閲覧できる。
//   paid   : 有料。Stripe で課金している。
//   invited: 招待。課金せずに有料と同じ権限を持つ。
//
// 招待プランは Stripe を介さないため、管理画面から手動で設定する。
// 期限を設けることも、無期限にすることもできる。

const PLANS = {
  free:    { key: 'free',    label: '無料プラン',   fullAccess: false },
  paid:    { key: 'paid',    label: '有料プラン',   fullAccess: true  },
  invited: { key: 'invited', label: '招待プラン',   fullAccess: true  },
};

const ACTIVE_STRIPE = new Set(['active', 'trialing']);

/**
 * 会員の現在のプランを判定する。
 *
 * 招待を優先する。招待中の会員が課金した場合でも、
 * 招待期限が残っていれば招待として扱い、二重に権限を与えない。
 */
function resolvePlan(user, sub, now = Date.now()) {
  // --- 招待プラン ---
  if (user && user.planOverride === 'invited') {
    const until = user.planUntil || null;
    if (!until || until > now) {
      return { plan: 'invited', fullAccess: true, until, label: PLANS.invited.label };
    }
    // 期限切れの招待は無料に戻す
  }

  // --- 明示的に無料へ固定されている場合 ---
  if (user && user.planOverride === 'free') {
    return { plan: 'free', fullAccess: false, until: null, label: PLANS.free.label };
  }

  // --- Stripe の課金状態 ---
  if (sub && ACTIVE_STRIPE.has(sub.status)) {
    const end = sub.currentPeriodEnd || null;
    if (!end || end > now) {
      return { plan: 'paid', fullAccess: true, until: end, label: PLANS.paid.label };
    }
  }

  return { plan: 'free', fullAccess: false, until: null, label: PLANS.free.label };
}

module.exports = { PLANS, resolvePlan, ACTIVE_STRIPE };
