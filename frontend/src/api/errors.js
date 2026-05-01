/**
 * User-facing messages for axios errors (502 gateways, timeouts, FastAPI detail, etc.).
 */

function parseFastApiDetail(detail) {
  if (detail == null) return null;
  if (typeof detail === 'string' && detail.trim()) return detail.trim();
  if (Array.isArray(detail)) {
    const parts = detail.map((x) => {
      if (typeof x === 'object' && x != null && 'msg' in x) return String(x.msg);
      try {
        return JSON.stringify(x);
      } catch {
        return String(x);
      }
    });
    const joined = parts.filter(Boolean).join('; ');
    return joined || null;
  }
  if (typeof detail === 'object' && detail !== null && 'message' in detail) {
    return String(detail.message);
  }
  return null;
}

function isProbablyHtml(s) {
  const t = (s || '').trim();
  return t.startsWith('<!') || t.startsWith('<html') || t.startsWith('<HTML');
}

const MSG_502 =
  'Bad gateway (502): the app could not reach the API process. The server may be restarting, still starting, or it crashed—check Logs in Databricks Apps, wait a minute, then refresh.';
const MSG_503 =
  'Service unavailable (503): the app is temporarily busy or rolling out. Try again in a few seconds.';
const MSG_504 =
  'Gateway timeout (504): the request took too long. Retry after a shorter operation completes.';
const MSG_TIMEOUT =
  'The request timed out. The app may be cold-starting or the work took longer than the limit. Try again in a moment.';
const MSG_NETWORK =
  'Network error: the browser could not reach the app. Check your connection and reload the page.';

/**
 * @param {unknown} err — typically an AxiosError from the API client
 * @returns {string}
 */
export function humanizeApiError(err) {
  if (err == null) return 'Something went wrong.';
  if (typeof err === 'string') return err;

  const noResponse = !err.response;
  if (noResponse) {
    const code = err.code;
    if (code === 'ECONNABORTED' || (err.message && String(err.message).toLowerCase().includes('timeout'))) {
      return MSG_TIMEOUT;
    }
    if (err.message === 'Network Error') return MSG_NETWORK;
    return err.message || 'Could not reach the server. Try refreshing the page.';
  }

  const status = err.response.status;
  const data = err.response.data;
  const fromDetail = parseFastApiDetail(data?.detail);

  if (typeof data === 'string' && isProbablyHtml(data)) {
    if (status === 502) return MSG_502;
    if (status === 503) return MSG_503;
    if (status === 504) return MSG_504;
    return `The server returned an error (${status}). Try again or refresh the page.`;
  }

  if (status === 502) return fromDetail || MSG_502;
  if (status === 503) return fromDetail || MSG_503;
  if (status === 504) return fromDetail || MSG_504;
  if (status === 413) return fromDetail || 'Request too large (413). Reduce the payload and try again.';
  if (status === 429) return fromDetail || 'Too many requests (429). Wait a few seconds and try again.';
  if (status === 401) return fromDetail || 'Unauthorized (401). Refresh the page or sign in again.';
  if (status === 403) return fromDetail || 'Forbidden (403): you may not have permission for this operation.';
  if (status === 404) return fromDetail || 'Not found (404): the resource may have been removed.';
  if (status === 409) return fromDetail || 'Conflict (409): the resource already exists or is in an incompatible state.';

  if (fromDetail) return fromDetail;

  if (typeof data === 'string') {
    const t = data.trim();
    if (t.length > 0 && t.length < 800 && !isProbablyHtml(t)) return t;
  }

  if (typeof data === 'object' && data !== null && data.message) {
    return String(data.message);
  }

  if (status >= 500) {
    return `Server error (${status}). If this persists, check Databricks Apps logs and workspace status.`;
  }

  return `Request failed (${status}).`;
}
