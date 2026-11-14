/**
 * roomsDistributionRenderer — listing counts per number of rooms, grouped
 * bar chart with one bar series per operation.
 *
 * Room values may be numeric (2, 3) or, occasionally, a categorical string
 * (e.g. '4+') — both are handled deterministically by sorting numerically
 * first and placing any non-numeric labels after, alphabetically.
 *
 * Design pattern: Strategy + Factory. Consumes:
 * data_basis.rooms_distribution (schema v1.0, FEATURE-011).
 */

import { buildLayout } from '../chart_theme.js';
import { sumCountsBy } from '../reaggregate.js';

// Chart grain. The gold layer may emit these rows additionally split by
// district/neighbourhood (FEATURE-016) so they can be scope-filtered; the
// chart itself plots one value per combination of these fields.
const GRAIN = ['operation', 'rooms'];

/**
 * Deterministic comparator: numeric room values sort ascending by value;
 * non-numeric (string) room labels sort alphabetically after all numeric
 * ones.
 *
 * @param {*} a
 * @param {*} b
 * @returns {number}
 */
function compareRooms(a, b) {
  const numA = Number(a);
  const numB = Number(b);
  const aIsNumeric = !Number.isNaN(numA);
  const bIsNumeric = !Number.isNaN(numB);
  if (aIsNumeric && bIsNumeric) return numA - numB;
  if (aIsNumeric) return -1;
  if (bIsNumeric) return 1;
  return String(a).localeCompare(String(b));
}

/**
 * Group rooms_distribution rows by operation into Plotly bar traces.
 *
 * @param {Array<{operation: string, rooms: number|string, count_listings: number}>} rows
 * @returns {Array<object>}
 */
function toTraces(rows) {
  const groups = new Map();
  for (const row of rows) {
    if (!groups.has(row.operation)) {
      groups.set(row.operation, []);
    }
    groups.get(row.operation).push(row);
  }

  return Array.from(groups.entries()).map(([operation, records]) => {
    const sorted = [...records].sort((a, b) => compareRooms(a.rooms, b.rooms));
    return {
      name: operation,
      x: sorted.map((r) => String(r.rooms)),
      y: sorted.map((r) => r.count_listings),
      type: 'bar',
      meta: { operation },
    };
  });
}

export const roomsDistributionRenderer = {
  id: 'rooms-distribution',
  title: 'Rooms distribution',

  /**
   * @param {object|null|undefined} dataBasis - The `data_basis` top-level block.
   * @param {{viewport?: string, colorScheme?: string}} [context]
   * @returns {{data: Array<object>, layout: object}}
   */
  render(dataBasis, context = { viewport: 'desktop', colorScheme: 'light' }) {
    const rows = sumCountsBy(dataBasis?.rooms_distribution, GRAIN);
    const layout = buildLayout({
      viewport: context.viewport,
      colorScheme: context.colorScheme,
      overrides: {
        xaxis: { title: { text: 'Rooms' } },
        yaxis: { title: { text: 'Listings' } },
        barmode: 'group',
      },
    });
    if (rows.length === 0) {
      return { data: [], layout: { ...layout, title: { text: this.title } } };
    }
    return {
      data: toTraces(rows),
      layout: { ...layout, title: { text: this.title } },
    };
  },
};
