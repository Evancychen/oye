// Reports are fetched at runtime; cache successful responses for offline reading.
async function reportJson(path) {
  let cache;
  try { cache = await caches.open('oye-content'); } catch {}
  try {
    const response = await fetch(path, { cache: 'no-store' });
    if (!response.ok) {
      if (response.status === 404) { await cache?.delete(path); return null; }
      throw new Error('Report unavailable');
    }
    const data = await response.clone().json();
    await cache?.put(path, response);
    return data;
  } catch {
    try { return await (await cache?.match(path))?.json() || null; } catch { return null; }
  }
}
export async function loadReportsIndex() {
  const index = await reportJson('content/reports/index.json');
  return Array.isArray(index) ? index : [];
}
export function loadReport(entry) {
  return reportJson(`content/reports/report-${entry.added}.json`);
}
