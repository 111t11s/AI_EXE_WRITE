export function resolveApiBaseUrl(value = '') {
  if (!value) return '/api/v1';
  const url = new URL(value);
  if (!['https:', 'http:'].includes(url.protocol) || url.username || url.password || url.search || url.hash) {
    throw new Error('服务器地址必须是 HTTP(S) 地址，不能包含账户、查询参数或片段。');
  }
  return url.href.replace(/\/+$/, '');
}

let sessionToken;
export function getAuthToken() {
  if (sessionToken === undefined) {
    try { sessionToken = sessionStorage.getItem('mojian-session') || ''; }
    catch { sessionToken = ''; }
  }
  return sessionToken;
}

export function setAuthToken(token = '') {
  sessionToken = token;
  try {
    if (token) sessionStorage.setItem('mojian-session', token);
    else sessionStorage.removeItem('mojian-session');
  } catch { /* Keep this tab usable when browser storage is unavailable. */ }
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
