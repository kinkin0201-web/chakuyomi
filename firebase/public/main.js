// 画面の描画とイベント処理。
import * as api from '/app.js';

const $ = s => document.querySelector(s);
const S = { data: null, jcd: null, rno: null, me: null, plan: 'guest',
  // 買い方の選択。堅い=当たりやすい / 妙味=期待値重視
  strategy: (() => { try { return localStorage.getItem('ck.strategy') || 'safe'; }
                     catch { return 'safe'; } })() };

const todayJST = () => new Date(Date.now() + 9 * 36e5).toISOString().slice(0, 10);

// ---------- 起動 ----------
api.watchAuth(async (user) => {
  $('#signin').hidden = !!user;
  $('#acct').hidden = !user;
  if (user) {
    let r = await api.getMe();

    // 401 は「別端末で無効化された」とは限らない。
    // Cookie がまだ無い(初回・シークレットウィンドウ)場合も 401 になる。
    // まずセッションを作り直し、それでも駄目なときだけ知らせる。
    if (r.status === 401) {
      try { await api.login(); } catch { /* 作り直せなければ下で無料表示にする */ }
      r = await api.getMe();
    }

    S.me = r.status === 200 ? r.data : null;

    // 停止されたアカウントは利用させない
    if (r.status === 403) {
      alert('このアカウントは利用を停止されています。');
      await api.logout();
      return;
    }
  } else {
    S.me = null;
  }
  await loadRaces();
});

$('#signin').onclick = async () => {
  const btn = $('#signin');
  btn.disabled = true; btn.textContent = '接続中…';
  try {
    await api.login();
  } catch (e) {
    console.error('login failed:', e);
    // 原因を画面に残す。alert だと閉じた瞬間に情報が消えるため。
    const code = (e && e.code) || '';
    const detail = (e && e.message) || String(e);
    const hint = {
      'auth/popup-blocked': 'ポップアップがブロックされました。ブラウザ設定を確認してください。',
      'auth/popup-closed-by-user': 'ログイン画面が閉じられました。もう一度お試しください。',
      'auth/unauthorized-domain': 'このドメインが承認されていません。',
      'auth/operation-not-allowed': 'LINEログインが有効化されていません。',
      'auth/invalid-credential': 'LINE側のチャネルID/シークレットが一致していません。',
      'auth/internal-error': 'LINE側の設定（コールバックURL等）をご確認ください。',
    }[code] || '';
    const b = $('#banner');
    b.hidden = false;
    b.innerHTML =
      `<span class="t">ログインできませんでした</span>` +
      (hint ? `<span class="s">${esc(hint)}</span>` : '') +
      `<span class="s" style="font-family:var(--mono);font-size:.72rem;word-break:break-all">` +
      `${esc(code || '(コードなし)')}<br>${esc(detail).slice(0, 300)}</span>`;
  } finally {
    btn.disabled = false; btn.textContent = 'ログイン';
  }
};

$('#acct').onclick = async () => { renderMyPage(); $('#mypage').showModal(); };

// ---------- データ取得 ----------
/** JSON を取得する。取れなければ null。キャッシュは使わない。 */
async function fetchJson(path) {
  try {
    const sep = path.includes('?') ? '&' : '?';
    const r = await fetch(path + sep + 't=' + Date.now(), { cache: 'no-store' });
    if (!r.ok) return null;
    // 存在しないパスは SPA フォールバックで index.html が返るため、
    // Content-Type が JSON でなければ無効とみなす。
    if (!(r.headers.get('content-type') || '').includes('json')) return null;
    return await r.json();
  } catch { return null; }
}

/** その日の配信データがあるか確かめる。 */
async function fetchDay(date) {
  const j = await fetchJson(`/data/${date}.json`);
  // 旧形式は配列、新形式は {races, summary}。どちらも受け付ける。
  const races = Array.isArray(j) ? j : (j && j.races);
  return Array.isArray(races) && races.length ? date : null;
}

async function loadRaces() {
  // 当日データがあればそれを使う。無ければ配信済みの最新日にする。
  // データ本体を直接見て判断するので、index.json のキャッシュに左右されない。
  let date = await fetchDay(todayJST());
  if (!date) {
    const idx = await fetchJson('/data/index.json');
    if (idx && idx.latest) date = await fetchDay(idx.latest);
  }
  if (!date) {
    renderEmpty('本日のレースはまだ公開されていません。');
    renderBanner();
    return;
  }
  S.date = date;
  $('#dt').textContent = date.replace(/-/g, '.') + ' の予測';

  const r = await api.getRaces(date);
  if (r.status !== 200 || !r.data || !r.data.ok) {
    S.data = null; renderEmpty('データを取得できませんでした'); return;
  }
  S.plan = r.data.plan || 'guest';
  S.summary = r.data.summary || null;
  const races = r.data.races || [];
  if (!races.length) {
    renderEmpty('本日のレースはまだ公開されていません。開催日の朝までにお待ちください。');
    renderBanner();
    return;
  }

  // 場ごとにまとめる
  const st = {};
  for (const x of races) {
    (st[x.stadium] ||= { name: x.stadiumName || x.stadium, races: [] }).races.push(x);
  }
  for (const k in st) st[k].races.sort((a, b) => a.no - b.no);

  S.data = st;
  if (!S.jcd || !st[S.jcd]) S.jcd = Object.keys(st)[0];
  S.rno = st[S.jcd].races[0].no;
  render();
}

function renderEmpty(msg) {
  $('#chips').innerHTML = ''; $('#races').innerHTML = '';
  $('#verdict').innerHTML = ''; $('#cond').innerHTML = '';
  $('#list').innerHTML = `<p class="empty">${msg}</p>`;
  $('#note').textContent = '';
}

// ---------- 描画 ----------
function render() {
  renderSummary(); renderBanner(); chips(); races(); verdict(); cond(); list();
}

/** 選択中の競艇場の収支を出す。回収率は商品の核なので隠さず見せる。 */
function renderSummary() {
  const el = $('#summary');
  if (!S.data || !S.jcd) { el.hidden = true; return; }
  const races = S.data[S.jcd].races;

  // 表示している買い目(期待値1.0以上)だけで集計する。
  // 画面の買い目と数字が食い違わないようにするため。
  let bet = 0, ret = 0, hit = 0, done = 0, plan = 0, skip = 0;
  for (const r of races) {
    if (r.locked) continue;
    const buy = (S.strategy === 'value' ? r.value : r.safe) || [];
    const pts = buy.reduce((a, p) => a + (p.points || 1), 0);
    plan += pts;
    if (!pts) skip++;

    const combo = r.result && r.result.combo;
    if (!combo || !pts) continue;
    done++;
    bet += pts * 100;
    for (const p of buy) {
      if (p.thirds.some(c => `${p.first}-${p.second}-${c}` === combo)) {
        ret += (r.result.payout || 0); hit++; break;
      }
    }
  }

  const name = S.data[S.jcd].name;
  // 結果が出ていなければ、投資予定額だけ示す
  if (!done) {
    el.hidden = false;
    el.innerHTML = `<div class="sm">
        <div class="si"><span class="sk">${esc(name)}</span>
          <span class="sv">${races.length}R</span></div>
        <div class="si"><span class="sk">買い目</span>
          <span class="sv">${plan}点</span></div>
        <div class="si"><span class="sk">投資予定</span>
          <span class="sv">${(plan * 100).toLocaleString()}円</span></div>
        <div class="si"><span class="sk">見送り</span>
          <span class="sv">${skip}R</span></div>
      </div>
      <p class="sn">${S.strategy === 'value' ? '大きく狙う' : '当てにいく'}の買い目で計算。結果確定後に回収率を表示します。</p>`;
    return;
  }

  const roi = bet ? ret / bet : 0;
  const profit = ret - bet;
  const plus = profit >= 0;
  el.hidden = false;
  el.innerHTML = `<div class="sm ${plus ? 'plus' : 'minus'}">
      <div class="si"><span class="sk">回収率</span>
        <span class="sv">${(roi * 100).toFixed(1)}%</span></div>
      <div class="si"><span class="sk">投資</span>
        <span class="sv">${bet.toLocaleString()}円</span></div>
      <div class="si"><span class="sk">回収</span>
        <span class="sv">${ret.toLocaleString()}円</span></div>
      <div class="si"><span class="sk">収支</span>
        <span class="sv">${plus ? '+' : ''}${profit.toLocaleString()}円</span></div>
    </div>
    <p class="sn">${esc(name)}・${done}レースを提示どおり購入した場合。
      的中${hit}レース／見送り${skip}レース。</p>`;
}

function renderBanner() {
  const b = $('#banner');
  // 全機能が使えるプラン(有料・招待・管理者)にはバナーを出さない
  if (['paid', 'invited', 'admin'].includes(S.plan)) { b.hidden = true; return; }
  b.hidden = false;
  const signedIn = !!S.me;
  b.innerHTML =
    `<span class="t">各競艇場から1レースずつ無料で見られます</span>
     <span class="s">すべてのレースを見るには月額1,980円のプランが必要です。</span>
     <button class="cta" id="go">${signedIn ? 'プランに登録する' : 'LINEではじめる'}</button>`;
  $('#go').onclick = async () => {
    if (!signedIn) { await api.login(); return; }
    const r = await api.startCheckout();
    if (r.status === 409) alert('すでにご契約いただいています。');
  };
}

function chips() {
  const e = $('#chips'); e.innerHTML = '';
  for (const [k, v] of Object.entries(S.data)) {
    const b = document.createElement('button');
    b.className = 'chip'; b.textContent = v.name;
    b.setAttribute('aria-pressed', k === S.jcd);
    b.onclick = () => { S.jcd = k; S.rno = S.data[k].races[0].no; render(); };
    e.appendChild(b);
  }
}

function races() {
  const e = $('#races'); e.innerHTML = '';
  // 的中/不的中の件数を数え、凡例として出す
  let win = 0, lose = 0;
  for (const r of S.data[S.jcd].races) {
    const b = document.createElement('button');
    b.className = 'rb' + (r.locked ? ' lk' : '');
    b.setAttribute('aria-pressed', r.no === S.rno);
    const t = document.createElement('span'); t.textContent = r.no + 'R';
    // 締切時刻を添える。終わったレースは色を落として区別する。
    if (r.deadline) {
      const tm = document.createElement('span');
      tm.className = 'tm'; tm.textContent = r.deadline;
      t.appendChild(tm);
    }
    // 終わったレースは的中/不的中で色を分ける。
    // 一つずつ開いて確認する手間をなくすため。
    if (r.result && r.result.combo) {
      b.classList.add('done');
      const picks = (S.strategy === 'value' ? r.value : r.safe) || [];
      const hit = picks.some(p =>
        (p.thirds || []).some(c => `${p.first}-${p.second}-${c}` === r.result.combo));
      b.classList.add(hit ? 'win' : 'lose');
      hit ? win++ : lose++;
      if (hit && r.result.payout) {
        const y = document.createElement('span');
        y.className = 'yen';
        y.textContent = r.result.payout.toLocaleString();
        t.appendChild(y);
      }
    }
    // 1号艇が飛ぶ警告。実測で通常58%が15〜25%まで落ちる。
    if (!r.locked && r.upset && r.upset.warn) b.classList.add('upset');
    const i = document.createElement('i');
    if (!r.locked && !r.pending) {
      if (r.upsetP >= 0.7) i.className = 'hi';
      else if (r.upsetP <= 0.2) i.className = 'lo';
    }
    if (r.pending) b.classList.add('pd');
    b.append(t, i);
    b.onclick = () => { S.rno = r.no; render(); };
    e.appendChild(b);
  }

  const lg = $('#legend');
  if (win + lose === 0) { lg.hidden = true; return; }
  lg.hidden = false;
  lg.innerHTML =
    `<span class="lgi"><i class="sw win"></i>的中 ${win}R</span>
     <span class="lgi"><i class="sw lose"></i>不的中 ${lose}R</span>
     <span class="lgi lgn">${S.strategy === 'value' ? '大きく狙う' : '当てにいく'}の買い目で判定</span>`;
}

const cur = () => S.data[S.jcd].races.find(r => r.no === S.rno);

function verdict() {
  const r = cur(), v = $('#verdict');
  if (r.locked) {
    v.innerHTML = `<div class="locked"><div class="t">このレースは有料プランで見られます</div>
      <div class="s">各競艇場の無料レースでお試しいただけます。</div></div>`;
    return;
  }
  // 新形式(safe/value)が無い古いデータは picks で代用する
  const safe = r.safe || r.picks || [];
  const value = r.value || r.picks || [];
  const picks = S.strategy === 'value' ? value : safe;
  if (!picks.length) { v.innerHTML = ''; return; }

  const pts = picks.length;
  const hitP = picks.reduce((a, p) => a + (p.p || 0), 0);
  // 段階ごとの小計。予算に応じて選べるようにする。
  const tiers = [1, 2, 3].map(t => {
    const g = picks.filter(p => (p.tier || 3) === t);
    return { t, n: g.length, p: g.reduce((a, x) => a + (x.p || 0), 0) };
  }).filter(x => x.n);
  // ◎だけ / ◎○ / 全部 を買った場合の的中期待
  // 実測の回収率(直近42日)。買い方を選ぶ判断材料として示す。
  const ROI = { 2: 83.5, 5: 81.3, 10: 79.6 };
  const cum = [];
  let acc = 0, accN = 0;
  for (const x of tiers) {
    acc += x.p; accN += x.n;
    cum.push({ label: x.t === 1 ? '◎ だけ' : x.t === 2 ? '◎ + ○' : 'すべて',
               n: accN, p: acc, roi: ROI[accN] });
  }

  const up = r.upset && r.upset.warn ? r.upset : null;

  v.innerHTML =
    (up ? `<div class="alert">
        <span class="al">本命が崩れる可能性</span>
        <span class="at">1号艇より <b>${up.topBoat}号艇</b> を上に見ています</span>
        <span class="as">この判定が出たレースでは、1号艇の1着率が
          <b>約20%</b>まで落ちます（通常は約58%）。</span>
      </div>` : '') +
    `<div class="picks">
       <div class="tabs" role="tablist">
         <button class="tab" role="tab" aria-selected="${S.strategy === 'safe'}"
           data-s="safe">当てにいく<span>的中率 重視</span></button>
         <button class="tab" role="tab" aria-selected="${S.strategy === 'value'}"
           data-s="value">大きく狙う<span>配当 重視</span></button>
       </div>

       <div class="budget">
         ${cum.map(c => `<div class="bg">
           <span class="bg-label">${c.label}</span>
           <span class="bg-cost">${c.n}点<i>${(c.n * 100).toLocaleString()}円</i></span>
           <span class="bg-hit">的中 ${(c.p * 100).toFixed(0)}%</span>
           ${c.roi ? `<span class="bg-roi">回収 ${c.roi}%</span>` : ''}
         </div>`).join('')}
       </div>

       ${picks.map((p, i) => `
         ${i === 0 || p.tier !== picks[i - 1].tier
           ? `<div class="tierhead">${p.tier === 1 ? '本命'
               : p.tier === 2 ? '対抗'
               : '押さえ'}</div>` : ''}
         <div class="pk ${p.tier === 1 ? 'honmei' : p.tier === 2 ? 'taiko' : 'osae'}">
           <span class="mk">${p.mark}</span>
           <span class="cb">${fmtCombo(p.text)}
             <span class="cx">確率 ${(p.p * 100).toFixed(1)}%</span></span>
           <span class="pp">${p.payout
             ? `<b class="odds">${(p.payout / 100).toFixed(1)}倍</b>
                <span class="ret">${r.result && r.result.combo
                  ? `100円→${p.payout.toLocaleString()}円`
                  : `現在 / 変動します`}</span>` : ''}
             <span class="pt ${(p.ev || 0) >= 1 ? 'good' : ''}">期待値 ${(p.ev || 0).toFixed(2)}</span></span>
         </div>`).join('')}

       <div class="pf">1点100円で計算しています。点数を増やすと当たりやすくなりますが、
         回収率は下がります。${S.strategy === 'safe'
           ? '' : '配当重視のため当たりにくいぶん、当たれば大きくなります。'}
         <br><span class="tiny">回収率は直近42日・5,779レースの実測値です。</span></div>
     </div>` + resultBlock(r, picks);

  for (const b of v.querySelectorAll('.tab')) {
    b.onclick = () => {
      S.strategy = b.dataset.s;
      try { localStorage.setItem('ck.strategy', S.strategy); } catch { /* 無視 */ }
      render();
    };
  }
}

/** 「1-3-2,5」の艇番に色を付ける。 */
function fmtCombo(text) {
  return esc(text).replace(/\d/g, d => `<b class="bn n${d}">${d}</b>`);
}

/** 結果が出ているレースは答え合わせを出す。 */
function resultBlock(r, picks) {
  if (!r.result || !r.result.combo) return '';
  const hit = (picks || []).some(p =>
    (p.thirds || []).some(c => `${p.first}-${p.second}-${c}` === r.result.combo));
  return `<div class="rz ${hit ? 'hit' : ''}">
      <span>結果 <b>${esc(r.result.combo)}</b>
      ${r.result.payout ? `／ ${r.result.payout.toLocaleString()}円` : ''}</span>
      <span class="rj">${hit ? '的中' : '不的中'}</span>
    </div>`;
}

function cond() {
  const r = cur(), bits = [];
  if (r.locked) { $('#cond').innerHTML = ''; return; }
  if (r.deadline) bits.push(['締切', r.deadline + (r.result && r.result.combo ? '（確定）' : '')]);
  if (r.title) bits.push(['', r.title]);
  if (r.weather) bits.push(['天候', r.weather]);
  if (r.wind) bits.push(['風', r.wind + (r.windSpeed != null ? ` ${r.windSpeed}m` : '')]);
  if (r.wave != null) bits.push(['波', r.wave + 'cm']);
  $('#cond').innerHTML = bits.map(([k, v]) =>
    `<span>${k ? k + ' ' : ''}<b class="v">${v}</b></span>`).join('');
}

function list() {
  const r = cur(), e = $('#list'); e.innerHTML = '';
  if (r.locked || !r.boats) {
    $('#note').textContent = 'レース番号の下の線 — 赤は荒れ予測、緑は堅い予測です。';
    return;
  }
  const rough = r.upsetP >= 0.5;
  let done = false;
  for (const b of r.boats) {
    if (b.actual != null) done = true;
    const d = document.createElement('div');
    d.className = 'row' + (b.boat === 1 ? ' b1 ' + (rough ? 'rough' : 'solid') : '');
    d.innerHTML =
      `<span class="lane n${b.boat}">${b.boat}</span>
       <span><div class="nm">${esc(b.name)}</div><div class="sub">${esc(b.cls || '')}` +
      `${b.win != null ? ' · 勝率' + b.win.toFixed(2) : ''}` +
      `${b.ex != null ? ' · 展示' + b.ex.toFixed(2) : ''}</div></span>` +
      `<span class="res${b.actual === 1 ? ' w' : ''}">${b.actual != null ? b.actual + '着' : '—'}</span>`;
    e.appendChild(d);
  }
  $('#note').textContent = done
    ? '「◯着」は実際の結果です。'
    : 'レース番号の下の線 — 赤は荒れ予測、緑は堅い予測です。';
}

function renderMyPage() {
  const m = S.me;
  if (!m) { $('#mp').innerHTML = '<p>情報を取得できませんでした。</p>'; return; }

  const fmt = t => t ? new Date(t).toLocaleDateString('ja-JP') : '—';
  // 招待は Stripe の期限ではなく planUntil を見る
  const until = m.plan === 'invited' ? (m.planUntil ? fmt(m.planUntil) : '無期限')
    : fmt(m.subscription && m.subscription.currentPeriodEnd);

  const badge = { free: 'free', paid: 'paid', invited: 'invited' }[m.plan] || 'free';

  $('#mp').innerHTML =
    `<div class="prof">
       ${m.photoURL
         ? `<img class="ava" src="${esc(m.photoURL)}" alt="" referrerpolicy="no-referrer">`
         : `<div class="ava ph">${esc((m.displayName || '?').slice(0, 1))}</div>`}
       <div class="pi">
         <div class="pn">${esc(m.displayName || '—')}</div>
         <span class="plan ${badge}">${esc(m.planLabel || '無料プラン')}</span>
       </div>
     </div>
     <div class="mi"><span>${m.plan === 'invited' ? '招待期限' : '次回更新'}</span><b>${until}</b></div>
     <div class="mi"><span>この端末</span><b>${esc(((m.device && m.device.name) || '').slice(0, 24))}</b></div>` +
    `<div class="acts">` +
    // 招待プランには課金導線を出さない。既に全機能が使えるため。
    (m.plan === 'invited'
      ? `<div class="msg ok">招待により、すべての機能をご利用いただけます。</div>`
      : m.plan === 'paid'
        ? `<button class="ghost" id="portal" type="button">解約・カード変更</button>`
        : `<button class="cta" id="buy" type="button">有料プランに登録する</button>`) +
    `</div>
     <div class="acts"><button class="ghost" id="lo" type="button">ログアウト</button></div>`;

  const portal = $('#portal'), buy = $('#buy'), lo = $('#lo');
  if (portal) portal.onclick = () => api.openPortal();
  if (buy) buy.onclick = () => api.startCheckout();
  if (lo) lo.onclick = async () => { await api.logout(); location.reload(); };
}

// XSS対策。選手名などは外部データなので必ず通す。
function esc(s) {
  return String(s).replace(/[&<>"']/g, c =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}
