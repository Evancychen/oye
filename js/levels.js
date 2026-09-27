// Oye v2 Levels: level/topic helpers and stars (stored locally, best score counts).
// Spec: levels-spec-v1.md ("Game layer") and design/levels-v2-spec.md.
const KEY = 'oye.stars.v1';

/** All topic codes from content-format-v1.md, in the order of the design (grid first, then "Coming soon"). */
export const TOPICS = [
  { code: 'numbers_prices', name: 'Numbers & prices' },
  { code: 'time_schedules', name: 'Time & schedules' },
  { code: 'directions', name: 'Directions & places' },
  { code: 'verbs_past', name: 'Past tense' },
  { code: 'verbs_present', name: 'Present tense' },
  { code: 'verbs_commands', name: 'Commands' },
  { code: 'verbs_future', name: 'Future & conditional' },
  { code: 'weather_plans', name: 'Weather & plans' },
  { code: 'food_ordering', name: 'Food & ordering' },
  { code: 'shopping', name: 'Shopping & sizes' },
  { code: 'health_pharmacy', name: 'Health & pharmacy' },
  { code: 'phone_reservations', name: 'Calls & bookings' },
  { code: 'home_errands', name: 'Home & errands' },
  { code: 'small_talk', name: 'Small talk' },
];
export const topicName = (code) => (TOPICS.find((t) => t.code === code) || {}).name || String(code || '').replace(/_/g, ' ');

/** A card's level. Missing (all v1 cards) means Easy. */
export const levelOf = (card) => {
  const l = card && card.level;
  return l === 'medium' || l === 'hard' ? l : 'easy';
};
export const isEasy = (card) => levelOf(card) === 'easy';

/**
 * Stars for a finished session (spec: 1 = finished, 2 = 70%+, 3 = 90%+ without hints).
 * answers: [{ ok, hint }]. Medium: a right answer after a hint counts as half.
 */
export function starsFor(answers, level = 'easy') {
  const n = answers.length;
  const right = answers.filter((a) => a.ok).length;
  const hints = answers.filter((a) => a.hint).length;
  const points = answers.reduce((s, a) => s + (a.ok ? (a.hint && level === 'medium' ? 0.5 : 1) : 0), 0);
  const pct = n ? points / n : 0;
  const stars = !n ? 0 : pct >= 0.9 && hints === 0 ? 3 : pct >= 0.7 ? 2 : 1;
  return { stars, right, n, hints, pct, need2: Math.ceil(0.7 * n - 1e-9), need3: Math.ceil(0.9 * n - 1e-9) };
}

export function load() {
  let s = null;
  try { s = JSON.parse(localStorage.getItem(KEY) || 'null'); } catch {}
  s = s && typeof s === 'object' ? s : {};
  s.topics ||= {}; s.missions ||= {}; s.easy = Number(s.easy) || 0; s.log ||= [];
  return s;
}
function save(s) {
  if (s.log.length > 500) s.log = s.log.slice(-500);
  try { localStorage.setItem(KEY, JSON.stringify(s)); } catch {}
}

/** Save a finished session's stars. Medium/Hard keep the best per topic/mission; Easy adds up. */
export function record(level, key, stars) {
  const s = load();
  let prev = 0;
  if (level === 'medium') { prev = s.topics[key] || 0; s.topics[key] = Math.max(prev, stars); }
  else if (level === 'hard') { prev = s.missions[key] || 0; s.missions[key] = Math.max(prev, stars); }
  else s.easy += stars;
  s.log.push({ at: new Date().toISOString(), level, key: key || null, stars });
  save(s);
  return { prev, best: Math.max(prev, stars), newBest: level !== 'easy' && stars > prev };
}

const sum = (o) => Object.values(o).reduce((a, b) => a + (Number(b) || 0), 0);
export function totals(s = load()) {
  const medium = sum(s.topics), hard = sum(s.missions);
  return { medium, hard, easy: s.easy, all: medium + hard + s.easy };
}
