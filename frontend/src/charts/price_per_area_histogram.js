/**
 * priceHistogramRentRenderer / priceHistogramSaleRenderer — price/m²
 * distribution as a bar chart, split by operation.
 *
 * Rent (€/m²/month ~10–25) and sale (€/m² ~2000–6000) sit on wildly
 * different scales and use different bin widths (documented in
 * DATA_GOLD_LAYER.md: sale 250 EUR/m², rent 1 EUR/m²) — sharing one x-axis
 * would make one side unreadable, so each operation gets its own renderer,
 * matching the boxplot_by_neighborhood.js precedent.
 *
 * Design pattern: Strategy + Open/Closed (factory keeps DRY).
 * Consumes: data_basis.price_per_area_histogram (schema v1.0, FEATURE-011).
 */

import { buildLayout } from '../chart_theme.js';
import { sumCountsBy } from '../reaggregate.js';

// Chart grain. The gold layer may emit these rows additionally split by
// district/neighbourhood (FEATURE-016) so they can be scope-filtered; the
// chart itself plots one value per combination of these fields.
const GRAIN = ['operation', 'bin_start_price_m2', 'bin_end_price_m2'];

/** @param {{bin_start_price_m2: number, bin_end_price_m2: number}} row @returns {string} */
function binLabel(row) {
  return `${row.bin_start_price_m2}-${row.bin_end_price_m2}`;
}

/**
 * Factory for a price/m² histogram renderer restricted to one operation.
 *
 * @param {string} id - DOM container id.
 * @param {string} title - Chart title shown above the plot.
 * @param {'rent'|'sale'} operation - Operation to include.
 * @param {string} xAxisLabel - X-axis label text.
 * @returns {{id: string, title: string, render: function}}
 */
function makeHistogramRenderer(id, title, operation, xAxisLabel) {
  return {
    id,
    title,
    /**
     * @param {object|null|undefined} dataBasis - The `data_basis` top-level block.
     * @param {{viewport?: string, colorScheme?: string}} [context]
     * @returns {{data: Array<object>, layout: object}}
     */
    render(dataBasis, context = { viewport: 'desktop', colorScheme: 'light' }) {
      const records = sumCountsBy(dataBasis?.price_per_area_histogram, GRAIN)
        .filter((r) => r.operation === operation)
        .sort((a, b) => a.bin_start_price_m2 - b.bin_start_price_m2);
      const layout = buildLayout({
        viewport: context.viewport,
        colorScheme: context.colorScheme,
        overrides: {
          // Bin labels like "10-11" can be misread by Plotly's automatic axis-type
          // detection as dates (e.g. "Oct 2011"). Force a categorical axis so the
          // labels always render as-is, as discrete bins.
          xaxis: { title: { text: xAxisLabel }, type: 'category' },
          yaxis: { title: { text: 'Listings' } },
        },
      });
      if (records.length === 0) {
        return { data: [], layout: { ...layout, title: { text: this.title } } };
      }
      return {
        data: [
          {
            name: operation,
            x: records.map(binLabel),
            y: records.map((r) => r.count_listings),
            type: 'bar',
            meta: { operation },
          },
        ],
        layout: { ...layout, title: { text: this.title } },
      };
    },
  };
}

export const priceHistogramRentRenderer = makeHistogramRenderer(
  'price-per-area-histogram-rent',
  'Rent price per m² distribution',
  'rent',
  'Rent price per m² per month (€)',
);

export const priceHistogramSaleRenderer = makeHistogramRenderer(
  'price-per-area-histogram-sale',
  'Sale price per m² distribution',
  'sale',
  'Sale price per m² (€)',
);
