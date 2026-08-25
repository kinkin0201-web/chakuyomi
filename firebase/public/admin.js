// 会員管理画面。開発者(Custom Claims の admin)のみが使える。
import { initializeApp } from 'https://www.gstatic.com/firebasejs/10.12.0/firebase-app.js';
import {
  getAuth, signInWithPopup, OAuthProvider, onAuthStateChanged, signOut,
} from 'https://www.gstatic.com/firebasejs/10.12.0/firebase-auth.js';

const CONFIG = {
  apiKey: 'AIzaSyBilmgtcvLJ-CxvlvZ8KG69u0JFsfqB_Po',
  authDomain: 'chakuyomi.firebaseapp.com',
  projectId: 'chakuyomi',
  appId: '1:257337643554:web:497cefbe95eeff3b71b07b',
};
const ADMIN_URL = 'https://admin-wctjqsqooq-an.a.run.app';

const auth = getAuth(initializeApp(CONFIG));
const provider = new OAuthProvider('oidc.line');
provider.addScope('openid');
provider.addScope('profile');
const $ = s => document.querySelector(s);
let user = null, cursor = null;

async function callAdmin(action, params = {}, body = null) {
  const q = new URLSearchParams({ action, ...params });
  const res = await fetch(`${ADMIN_URL}?${q}`, {
    method: body ? 'POST' : 'GET',
    headers: {
      'Authorization': 'Bearer ' + (await user.getIdToken()),
      ...(body ? { 'Content-Type': 'application/json' } : {}),
    },
    body: body ? JSON.stringify({ action, ...body }) : undefined,
    credentials: 'include',
  });
  return { status: res.status, data: await res.json().catch(() => null) };
}

onAuthStateChanged(auth, async (u) => {
  user = u;
  $('#signin').hidden = !!u;
  $('#signout').hidden = !u;
  if (!u) { $('#gate').hidden = false; $('#main').hidden = true; return; }

  // 権限は必ずサーバーに確認する。画面側の判定は信用しない。
  const r = await callAdmin('stats');
  if (r.status === 403) {
    $('#gate').innerHTML = '<p>このアカウントには管理権限がありません。</p>';
    $('#gate').hidden = false; $('#main').hidden = true; return;
  }
  $('#gate').hidden = true; $('#main').hidden = false;
  renderStats(r.data && r.data.stats);
  cursor = null; await load(true);
});

$('#signin').onclick = () => signInWithPopup(auth, provider).catch(e => alert(e.code || e.message));
$('#signout').onclick = () => signOut(auth);
$('#reload').onclick = () => { cursor = null; load(true); };
$('#q').oninput = filter;

function renderStats(s) {
  const items = s ? [
    ['会員数', s.users ?? '—'],
    ['有料', s.paid ?? '—'],
    ['今月の新規', s.newThisMonth ?? '—'],
    ['解約率', s.churn != null ? (s.churn * 100).toFixed(1) + '%' : '—'],
  ] : [['集計', '未生成']];
  $('#stats').innerHTML = items.map(([k, v]) =>
    `<div class="stat"><div class="k">${k}</div><div class="v">${v}</div></div>`).join('');
}

async function load(reset) {
  const r = await callAdmin('list', cursor ? { cursor, limit: 20 } : { limit: 20 });
  if (r.status !== 200 || !r.data || !r.data.ok) return;
  if (reset) $('#rows').innerHTML = '';
  for (const u of r.data.users) $('#rows').appendChild(row(u));
  cursor = r.data.nextCursor;
  $('#more').hidden = !cursor;
}
$('#more').onclick = () => load(false);

function row(u) {
  const tr = document.createElement('tr');
  tr.dataset.uid = u.uid;
  const until = u.currentPeriodEnd
    ? new Date(u.currentPeriodEnd).toLocaleDateString('ja-JP') : '—';
  // 一覧では planOverride を優先して表示する(招待が分かるように)
  const pk = u.planOverride === 'invited' ? 'invited'
    : (u.planOverride === 'free' ? 'free' : (u.plan || 'free'));
  const pl = { invited: '招待', paid: '有料', free: '無料' }[pk] || '無料';
  tr.innerHTML =
    `<td class="m">
       <div class="uc">${u.photoURL
         ? `<img class="av" src="${esc(u.photoURL)}" alt="" referrerpolicy="no-referrer">`
         : '<span class="av ph"></span>'}
         <span>${esc(u.displayName || '(名前なし)')}<br>
         <span style="color:var(--faint);font-size:.72rem">${esc(u.uid)}</span></span>
       </div></td>
     <td><span class="pill ${pk}">${pl}</span></td>
     <td class="m">${until}</td>
     <td>${u.suspended ? '<span class="pill sus">停止中</span>' : ''}</td>
     <td><button class="ghost" type="button">操作</button></td>`;
  tr.querySelector('button').onclick = () => openSheet(u.uid);
  return tr;
}

async function openSheet(uid) {
  const r = await callAdmin('user', { uid });
  const u = (r.data && r.data.user) || {};
  const sub = (r.data && r.data.subscription) || {};
  const until = sub.currentPeriodEnd
    ? new Date(sub.currentPeriodEnd).toLocaleString('ja-JP') : '—';
  const manual = !!sub.grantedBy;

  $('#sheet').innerHTML = `
    <h2>会員の操作</h2>
    <div class="msg">
      ID: ${esc(uid)}<br>
      名前: ${esc(u.displayName || '—')}<br>
      現在: ${u.planOverride === 'invited' ? '招待プラン'
        : (sub.status === 'active' ? '有料プラン（Stripe）' : '無料プラン')}<br>
      招待期限: ${u.planUntil ? new Date(u.planUntil).toLocaleString('ja-JP') : '（無期限/未設定）'}<br>
      Stripe: ${sub.status || '未契約'} ${sub.currentPeriodEnd ? '/ ' + until : ''}
    </div>
    <label>プラン
      <select id="plan">
        <option value="free">無料プラン</option>
        <option value="invited">招待プラン（課金なしで全機能）</option>
      </select></label>
    <label>招待の期限（日数・空欄なら無期限）
      <input id="days" type="number" min="1" max="3650" placeholder="無期限"></label>
    <label>理由（記録に残ります）
      <input id="reason" type="text" placeholder="例: 関係者、モニター"></label>
    <button class="cta" id="save" type="button">プランを保存する</button>
    ${u.suspended
      ? '<button class="ghost" id="unsus" type="button">停止を解除する</button>'
      : '<button class="danger cta" id="sus" type="button">アカウントを停止する</button>'}
    <div id="res"></div>
    <button class="ghost" value="close">閉じる</button>`;

  const act = async (action, body, okMsg) => {
    const r = await callAdmin(action, { uid }, { uid, ...body });
    $('#res').innerHTML = `<div class="msg">${r.data && r.data.ok
      ? okMsg : esc((r.data && (r.data.error || r.data.msg)) || '失敗しました')}</div>`;
    if (r.data && r.data.ok) { cursor = null; load(true); }
  };

  // 現在のプランを初期選択にする
  $('#plan').value = u.planOverride === 'invited' ? 'invited' : 'free';
  if (u.planUntil) {
    const rest = Math.ceil((u.planUntil - Date.now()) / 864e5);
    if (rest > 0) $('#days').value = rest;
  }
  $('#save').onclick = () => act('setPlan', {
    plan: $('#plan').value,
    days: $('#days').value || null,
    reason: $('#reason').value,
  }, 'プランを保存しました');
  const sus = $('#sus'); if (sus) sus.onclick = () => {
    if (confirm('このアカウントを停止しますか？ログイン中の端末も切断されます。'))
      act('suspend', { reason: $('#reason').value }, '停止しました');
  };
  const un = $('#unsus'); if (un) un.onclick = () => act('unsuspend', {}, '解除しました');

  $('#dlg').showModal();
}

function filter() {
  const q = $('#q').value.trim().toLowerCase();
  for (const tr of $('#rows').children) {
    tr.hidden = q && !tr.dataset.uid.toLowerCase().includes(q);
  }
}

function esc(s) {
  return String(s).replace(/[&<>"']/g, c =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}
