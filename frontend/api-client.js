export function resolveApiBaseUrl(value = '') {
  if (!value) return '/api/v1';
  const url = new URL(value);
  if (!['https:', 'http:'].includes(url.protocol) || url.username || url.password || url.search || url.hash) {
    throw new Error('服务器地址必须是 HTTP(S) 地址，不能包含账户、查询参数或片段。');
  }
  return url.href.replace(/\/+$/, '');
}

let sessionToken;
const sessionKey = 'mojian-session';
export function getAuthToken() {
  if (sessionToken === undefined) {
    try { sessionToken = localStorage.getItem(sessionKey) || ''; }
    catch { sessionToken = ''; }
    if (!sessionToken) {
      try { sessionToken = sessionStorage.getItem(sessionKey) || ''; }
      catch { /* No previous tab session to migrate. */ }
    }
    if (sessionToken) setAuthToken(sessionToken);
  }
  return sessionToken;
}

export function setAuthToken(token = '') {
  sessionToken = token;
  try {
    if (token) localStorage.setItem(sessionKey, token);
    else localStorage.removeItem(sessionKey);
  } catch { /* Keep this tab usable when browser storage is unavailable. */ }
  try { sessionStorage.removeItem(sessionKey); }
  catch { /* Legacy tab storage may be unavailable too. */ }
}

export async function authenticatedFetch(url, options = {}) {
  const token = getAuthToken();
  const headers = new Headers(options.headers);
  if (token) headers.set('Authorization', `Bearer ${token}`);
  const response = await fetch(url, {...options, headers});
  if (response.status === 401 && token && getAuthToken() === token) {
    setAuthToken();
    globalThis.dispatchEvent(new Event('aitxt:unauthorized'));
  }
  return response;
}
