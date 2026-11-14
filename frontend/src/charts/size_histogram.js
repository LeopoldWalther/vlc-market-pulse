/**
 * sizeHistogramRenderer — 10 m² size-bin listing counts, grouped bar chart
 * with one bar series per operation (rent/sale share the same m² scale so a
 * single grouped-bar chart is readable, unlike price/m², see
 * price_per_area_histogram.js).
 *
 * Design pattern: Strategy + Factory. Consumes:
 * data_basis.size_histogram_10sqm (schema v1.0, FEATURE-011).
 */

import { buildLayout } from '../chart_theme.js';
import { sumCountsBy } from '../reaggregate.js';

// Chart grain. The gold layer may emit these rows additionally split by
// district/neighbourhood (FEATURE-016) so they can be scope-filtered; the
// chart itself plots one value per combination of these fields.
const GRAIN = ['operation', 'bin_start_m2', 'bin_end_m2'];

/** @param {{bin_start_m2: number, bin_end_m2: number}} row @returns {string} */
function binLabel(row) {
  return `${row.bin_start_m2}-${row.bin_end_m2}`;
}

/**
 * Group size_histogram_10sqm rows by operation into Plotly bar traces,
 * ordered by bin_start_m2.
 *
 * @param {Array<{operation: string, bin_start_m2: number, bin_end_m2: number, count_listings: number}>} rows
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
    const sorted = [...records].sort((a, b) => a.bin_start_m2 - b.bin_start_m2);
    return {
      name: operation,
      x: sorted.map(binLabel),
      y: sorted.map((r) => r.count_listings),
      type: 'bar',
      meta: { operation },
    };
  });
}

export const sizeHistogramRenderer = {
  id: 'size-histogram',
  title: 'Listing size distribution (m²)',

  /**
   * @param {object|null|undefined} dataBasis - The `data_basis` top-level block.
   * @param {{viewport?: string, colorScheme?: string}} [context]
   * @returns {{data: Array<object>, layout: object}}
   */
  render(dataBasis, context = { viewport: 'desktop', colorScheme: 'light' }) {
    const rows = sumCountsBy(dataBasis?.size_histogram_10sqm, GRAIN);
    const layout = buildLayout({
      viewport: context.viewport,
      colorScheme: context.colorScheme,
      overrides: {
        // See price_per_area_histogram.js: force category axis so bin labels
        // like "100-110" are never mis-detected as dates.
        xaxis: { title: { text: 'Size bin (m²)' }, type: 'category' },
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
