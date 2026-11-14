import { describe, it, expect } from 'vitest';
import { sizeHistogramRenderer } from '../src/charts/size_histogram.js';

const dataBasis = {
  size_histogram_10sqm: [
    { operation: 'sale', bin_start_m2: 100, bin_end_m2: 110, count_listings: 15 },
    { operation: 'sale', bin_start_m2: 110, bin_end_m2: 120, count_listings: 20 },
    { operation: 'rent', bin_start_m2: 100, bin_end_m2: 110, count_listings: 10 },
    { operation: 'rent', bin_start_m2: 110, bin_end_m2: 120, count_listings: 12 },
  ],
};

describe('sizeHistogramRenderer', () => {
  it('has a stable id and title', () => {
    expect(sizeHistogramRenderer.id).toBe('size-histogram');
    expect(typeof sizeHistogramRenderer.title).toBe('string');
  });

  it('uses bar traces with operation-specific grouping', () => {
    const fig = sizeHistogramRenderer.render(dataBasis);
    expect(fig.data.every((t) => t.type === 'bar')).toBe(true);
    const operations = new Set(fig.data.map((t) => t.meta?.operation));
    expect(operations).toEqual(new Set(['sale', 'rent']));
    expect(fig.layout.barmode).toBe('group');
  });

  it('labels bins deterministically as "start-end"', () => {
    const fig = sizeHistogramRenderer.render(dataBasis);
    const sale = fig.data.find((t) => t.meta?.operation === 'sale');
    expect(sale.x).toEqual(['100-110', '110-120']);
    expect(sale.y).toEqual([15, 20]);
  });

  it('returns safe empty data/layout for missing data_basis input', () => {
    expect(sizeHistogramRenderer.render(null).data).toEqual([]);
    expect(sizeHistogramRenderer.render(undefined).data).toEqual([]);
    expect(sizeHistogramRenderer.render({}).data).toEqual([]);
    expect(sizeHistogramRenderer.render(null).layout).toBeTypeOf('object');
  });
});

describe('sizeHistogramRenderer with district/neighbourhood-dimensioned rows (FEATURE-016)', () => {
  const dimensioned = {
    size_histogram_10sqm: [
      { operation: 'sale', district: 'Extramurs', neighborhood: 'Arrancapins', bin_start_m2: 100, bin_end_m2: 110, count_listings: 9 },
      { operation: 'sale', district: 'Ciutat Vella', neighborhood: 'El Carme', bin_start_m2: 100, bin_end_m2: 110, count_listings: 6 },
      { operation: 'sale', district: 'Ciutat Vella', neighborhood: 'El Carme', bin_start_m2: 110, bin_end_m2: 120, count_listings: 20 },
      { operation: 'rent', district: 'Extramurs', neighborhood: 'La Roqueta', bin_start_m2: 100, bin_end_m2: 110, count_listings: 10 },
    ],
  };

  it('collapses the rows back to one bar per bin with summed counts', () => {
    const fig = sizeHistogramRenderer.render(dimensioned);
    const sale = fig.data.find((t) => t.meta?.operation === 'sale');
    expect(sale.x).toEqual(['100-110', '110-120']);
    expect(sale.y).toEqual([15, 20]);
    const rent = fig.data.find((t) => t.meta?.operation === 'rent');
    expect(rent.x).toEqual(['100-110']);
    expect(rent.y).toEqual([10]);
  });
});
