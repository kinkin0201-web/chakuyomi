// チャクヨミ - クライアント側の処理
//
// 認証は Firebase Auth (LINE を OIDC で接続)。
// セッションIDは HttpOnly Cookie に入るため、JSからは触らない。

import { initializeApp } from 'https://www.gstatic.com/firebasejs/10.12.0/firebase-app.js';
import {
  getAuth, signInWithPopup, OAuthProvider, onAuthStateChanged, signOut,
} from 'https://www.gstatic.com/firebasejs/10.12.0/firebase-auth.js';

const CONFIG = {
  apiKey: 'AIzaSyBilmgtcvLJ-CxvlvZ8KG69u0JFsfqB_Po',
  authDomain: 'chakuyomi.firebaseapp.com',
  projectId: 'chakuyomi',
  storageBucket: 'chakuyomi.firebasestorage.app',
  messagingSenderId: '257337643554',
  appId: '1:257337643554:web:497cefbe95eeff3b71b07b',
};

// Cloud Functions のURL
const FN = 'https://%s-wctjqsqooq-an.a.run.app';
const url = (name) => FN.replace('%s', name.toLowerCase());

const app = initializeApp(CONFIG);
const auth = getAuth(app);

// LINE は OIDC プロバイダとして登録する(プロバイダIDは Console で決めた値)
const lineProvider = new OAuthProvider('oidc.line');
// openid が無いと ID トークンにプロフィールが載らない。
// profile を付けると表示名とアイコンURLが取得できる。
lineProvider.addScope('openid');
lineProvider.addScope('profile');

let currentUser = null;

// セッションID。Cookie はクロスサイトで届かないため、
// 取得したIDを保持して x-session-id ヘッダで送る。
// localStorage に置くのは、再読み込み後もログイン状態を保つため。
const SID_KEY = 'chakuyomi.sid';
const getSid = () => { try { return localStorage.getItem(SID_KEY); } catch { return null; } };
const setSid = v => {
  try { v ? localStorage.setItem(SID_KEY, v) : localStorage.removeItem(SID_KEY); }
  catch { /* プライベートモード等で書けない場合は無視 */ }
};

/** 認証付きで API を叩く。Cookie を送るため credentials を必ず付ける。 */
async function call(name, opts = {}) {
  const headers = { ...(opts.headers || {}) };
  if (currentUser) {
    headers['Authorization'] = 'Bearer ' + (await currentUser.getIdToken());
  }
  const sid = getSid();
  if (sid) headers['x-session-id'] = sid;
  const res = await fetch(url(name) + (opts.query || ''), {
    method: opts.method || 'GET',
    headers,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
    credentials: 'include',   // HttpOnly Cookie を往復させる
  });
  let data = null;
  try { data = await res.json(); } catch { /* 本文が無い場合もある */ }
  return { status: res.status, data };
}

/** LINE でログインし、サーバー側にセッションを作る。 */
export async function login() {
  await signInWithPopup(auth, lineProvider);
  // ここで初めてサーバーにセッションを作る(同時ログイン1つを強制)
  const r = await call('sessionLogin', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: { device: navigator.userAgent.slice(0, 120) },
  });
  if (r.data && r.data.sid) setSid(r.data.sid);
  if (r.data && r.data.revoked) {
    // 別端末が使われていた場合は知らせる
    console.info('他の端末のセッションを終了しました');
  }
  return r;
}

export async function logout() {
  await call('sessionLogout', { method: 'POST' });
  setSid(null);
  await signOut(auth);
}

export async function getMe() {
  return call('me');
}

export async function getRaces(date) {
  return call('api', { query: date ? `?date=${date}` : '' });
}

/** 決済ページへ遷移する。 */
export async function startCheckout() {
  const r = await call('createCheckout', { method: 'POST' });
  if (r.data && r.data.url) location.href = r.data.url;
  return r;
}

/** 解約・カード変更のページへ遷移する。 */
export async function openPortal() {
  const r = await call('createPortal', { method: 'POST' });
  if (r.data && r.data.url) location.href = r.data.url;
  return r;
}

export async function logoutOthers() {
  return call('logoutOtherDevices', { method: 'POST' });
}

/** ログイン状態の変化を監視する。 */
export function watchAuth(cb) {
  onAuthStateChanged(auth, (u) => { currentUser = u; cb(u); });
}

export { auth };
