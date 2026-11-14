/**
 * reaggregate.js — pure helper that sums listing counts back to a chart's
 * own grain.
 *
 * Design intent: the gold layer emits the Data Basis count datasets at
 * (district, neighbourhood, <chart grain>) so the dashboard can filter them
 * by scope. A chart, however, plots one value per <chart grain>. After
 * filters.js has narrowed the rows, each renderer calls sumCountsBy() with
 * its own key fields to add the surviving rows back together.
 *
 * Single Responsibility: filters.js decides WHICH rows survive; this module
 * only adds them up; the renderer knows its grain. No DOM, no fetch.
 *
 * The helper is deliberately shape-agnostic: rows that already sit at the
 * requested grain (gold documents written before the district dimension
 * existed) pass through with unchanged values, so one frontend build works
 * against both shapes.
 */

const COUNT_FIELD = 'count_listings';

/**
 * Build a collision-free grouping key from a row's key-field values.
 *
 * JSON.stringify keeps the value type in the key, so the number 4 and the
 * string '4' (both valid `rooms` values) never fall into the same group.
 *
 * @param {object} row
 * @param {string[]} keyFields
 * @returns {string}
 */
function groupKey(row, keyFields) {
  return JSON.stringify(keyFields.map((field) => row?.[field] ?? null));
}

/**
 * Group rows by `keyFields` and sum their `count_listings`.
 *
 * @param {Array<object>|null|undefined} rows - Count rows, at the chart
 *   grain or finer (e.g. additionally split by district/neighbourhood).
 * @param {string[]} keyFields - Fields that define the chart grain.
 * @returns {Array<object>} One new row per distinct key combination,
 *   carrying exactly `keyFields` plus `count_listings`, in first-seen order.
 *   A missing or non-numeric count contributes 0. The input is not mutated.
 */
export function sumCountsBy(rows, keyFields) {
  const groups = new Map();
  for (const row of rows ?? []) {
    const key = groupKey(row, keyFields);
    if (!groups.has(key)) {
      const collapsed = {};
      for (const field of keyFields) collapsed[field] = row?.[field];
      collapsed[COUNT_FIELD] = 0;
      groups.set(key, collapsed);
    }
    const count = Number(row?.[COUNT_FIELD]);
    // Guard against NaN: one malformed row must not blank a whole bar.
    groups.get(key)[COUNT_FIELD] += Number.isFinite(count) ? count : 0;
  }
  return [...groups.values()];
}
