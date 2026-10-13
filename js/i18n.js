// Loaded before UI modules: every lookup is synchronous once the catalogs are ready.
async function catalog(language) {
  const response = await fetch(new URL(`../strings/${encodeURIComponent(language)}.json`, import.meta.url));
  if (!response.ok) throw new Error(`Catalog: HTTP ${response.status}`);
  const data = await response.json();
  if (!data || typeof data !== 'object' || Array.isArray(data)) throw new Error('Invalid catalog');
  return data;
}

let requested = 'en';
try { requested = localStorage.getItem('oye-lang') || 'en'; } catch {}
const english = await catalog('en');
const catalogs = new Map([['en', english]]);
// Warm Spanish so a Home tap can update the complete UI in one render.
const spanish = catalog('es').then(data => { catalogs.set('es', data); return data; }).catch(() => null);
let current = english;
export let language = 'en';
if (requested !== 'en') {
  try {
    const data = requested === 'es' ? await spanish : await catalog(requested);
    if (data) { current = data; language = requested; }
  } catch {}
}

// Return plain text; callers escape at the HTML boundary. Replacement is a single
// pass, so braces or dollar signs in a user's answer remain literal.
export function t(key, vars = {}) {
  const value = Object.hasOwn(current, key) && typeof current[key] === 'string'
    ? current[key] : english[key];
  return String(value ?? key).replace(/\{(\w+)\}/g, (placeholder, name) =>
    Object.hasOwn(vars, name) ? String(vars[name]) : placeholder);
}

let changeId = 0;
export async function setLanguage(next) {
  if (!['en', 'es'].includes(next)) return false;
  const id = ++changeId;
  let data = catalogs.get(next);
  if (!data) {
    try { data = await spanish || await catalog(next); } catch { return false; }
    catalogs.set(next, data);
  }
  if (id !== changeId) return false;
  current = data;
  language = next;
  try { localStorage.setItem('oye-lang', next); } catch {}
  translateDocument();
  return true;
}

function translateDocument() {
  document.documentElement.lang = language;
  for (const element of document.querySelectorAll('[data-i18n]')) {
    element.textContent = t(element.dataset.i18n);
  }
  for (const element of document.querySelectorAll('[data-i18n-content]')) {
    element.setAttribute('content', t(element.dataset.i18nContent));
  }
}
translateDocument();
