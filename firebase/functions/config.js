// 公開してよい設定値。
//
// 価格IDは秘密情報ではない(クライアントのチェックアウト画面にも現れる)。
// 一方、APIキーとWebhookシークレットは Secret Manager で管理し、
// このファイルには絶対に書かない。
module.exports = {
  PROJECT_ID: 'chakuyomi',
  REGION: 'asia-northeast1',

  // Stripe(テストモード)
  STRIPE_PRICE_ID: 'price_1U89jnRuEOH7LiIYbp06J8r5',
  PLAN_NAME: 'チャクヨミ 月額プラン',
  PLAN_YEN: 1980,

  // 無料で見せるレース数(1日あたり)
  FREE_RACES_PER_DAY: 3,

  // 判定のしきい値。実測に基づく。
  //   荒れ 0.70以上 -> 的中 74.4%
  //   堅い 0.20以下 -> 的中 83.2%
  THRESHOLD_ROUGH: 0.70,
  THRESHOLD_SOLID: 0.20,
};
