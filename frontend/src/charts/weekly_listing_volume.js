/**
 * weeklyListingVolumeRenderer — weekly collected-listing counts, split into
 * separate sale/rent traces over `snapshot_date`.
 *
 * Design pattern: Strategy + Factory (mirrors price_time_series.js), applied
 * to the unscoped `data_basis` block instead of a `general`/`relevant`
 * population block.
 *
 * Consumes: data_basis.weekly_listing_volume (schema v1.0, FEATURE-011).
 */

import { buildLayout } from '../chart_theme.js';
import { sumCountsBy } from '../reaggregate.js';

// Chart grain. The gold layer may emit these rows additionally split by
// district/neighbourhood (FEATURE-016) so they can be scope-filtered; the
// chart itself plots one value per combination of these fields.
const GRAIN = ['operation', 'snapshot_date'];

/**
 * Group weekly_listing_volume rows by operation into Plotly scatter traces.
 *
 * @param {Array<{operation: string, snapshot_date: string, count_listings: number}>} rows
 * @returns {Array<object>} One trace per operation, sorted by snapshot_date.
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
    const sorted = [...records].sort((a, b) => a.snapshot_date.localeCompare(b.snapshot_date));
    return {
      name: operation,
      x: sorted.map((r) => r.snapshot_date),
      y: sorted.map((r) => r.count_listings),
      type: 'scatter',
      mode: sorted.length <= 1 ? 'lines+markers' : 'lines',
      meta: { operation },
    };
  });
}

export const weeklyListingVolumeRenderer = {
  id: 'weekly-listing-volume',
  title: 'Weekly collected listing volume',

  /**
   * @param {object|null|undefined} dataBasis - The `data_basis` top-level block.
   * @param {{viewport?: string, colorScheme?: string}} [context]
   * @returns {{data: Array<object>, layout: object}}
   */
  render(dataBasis, context = { viewport: 'desktop', colorScheme: 'light' }) {
    const rows = sumCountsBy(dataBasis?.weekly_listing_volume, GRAIN);
    const layout = buildLayout({
      viewport: context.viewport,
      colorScheme: context.colorScheme,
      overrides: {
        xaxis: { title: { text: 'Date' } },
        yaxis: { title: { text: 'Listings collected' } },
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
