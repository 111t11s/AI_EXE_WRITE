export function resolveApiBaseUrl(value = '') {
  if (!value) return '/api/v1';
  const url = new URL(value);
  if (!['https:', 'http:'].includes(url.protocol) || url.username || url.password || url.search || url.hash) {
    throw new Error('服务器地址必须是 HTTP(S) 地址，不能包含账户、查询参数或片段。');
  }
  return url.href.replace(/\/+$/, '');
}
