/* Every call the viewer makes. One place, so a route change breaks in one file. */

async function request(url, options = {}) {
  const res = await fetch(url, options);
  let body = null;
  try { body = await res.json(); } catch { /* empty or non-JSON */ }
  if (!res.ok) {
    throw new Error(body?.detail ?? `${res.status} ${res.statusText}`);
  }
  return body;
}

const json = (url, payload) => request(url, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(payload ?? {}),
});

export const api = {
  providers:  ()                  => request('/api/providers'),
  session:    ()                  => request('/api/session'),
  ledgers:    (id)                => request(`/api/session/${id}/ledgers`),
  generate:   (id, seed)          => json(`/api/session/${id}/generate`, { seed }),
  run:        (id, analyst)       => json(`/api/session/${id}/run`, { analyst }),
  score:      (id)                => request(`/api/session/${id}/score`),

  upload(id, files) {
    const form = new FormData();
    for (const f of files) form.append('files', f);
    return request(`/api/session/${id}/upload`, { method: 'POST', body: form });
  },
};
