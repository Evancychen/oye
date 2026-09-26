// Answer checking: case, accents and punctuation don't matter.
export function normalize(s) {
  let t = String(s ?? '')
    .normalize('NFD').replace(/[\u0300-\u036f]/g, '')   // strip accents (é -> e, ñ -> n)
    .toLowerCase()
    .replace(/[¿?¡!.,;:"'“”‘’«»()\[\]$€\-–—_/\\]/g, ' ')  // punctuation and currency signs
    .replace(/\s+/g, ' ').trim();
  t = t.replace(/^son /, '').replace(/ (pesos?|mxn)$/, '').trim();    // "son 69 pesos" -> "69"
  t = t.replace(/\s+/g, ' ');
  // "10:30", "10.30" and "10 30" all become "10 30"; "08 45" -> "8 45"
  t = t.replace(/\b0+(\d)/g, '$1');
  return t;
}

export function isCorrect(card, given) {
  const g = normalize(given);
  if (!g) return false;
  const ok = [card.answer, ...(card.accepted_answers || [])].filter(v => v != null && v !== '');
  const digitsOnly = (x) => x.replace(/ /g, '');
  return ok.some(a => {
    const n = normalize(a);
    return n === g || (/^[\d ]+$/.test(n) && /^[\d ]+$/.test(g) && digitsOnly(n) === digitsOnly(g));
  });
}
