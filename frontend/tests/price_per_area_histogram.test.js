import { describe, it, expect } from 'vitest';
import {
  priceHistogramRentRenderer,
  priceHistogramSaleRenderer,
} from '../src/charts/price_per_area_histogram.js';

const dataBasis = {
  price_per_area_histogram: [
    { operation: 'sale', bin_start_price_m2: 2250.0, bin_end_price_m2: 2500.0, count_listings: 9 },
    { operation: 'sale', bin_start_price_m2: 2500.0, bin_end_price_m2: 2750.0, count_listings: 14 },
    { operation: 'rent', bin_start_price_m2: 9.0, bin_end_price_m2: 10.0, count_listings: 5 },
    { operation: 'rent', bin_start_price_m2: 10.0, bin_end_price_m2: 11.0, count_listings: 7 },
  ],
};

describe('price_per_area_histogram renderers', () => {
  it('each has a stable id and title', () => {
    expect(priceHistogramRentRenderer.id).toBe('price-per-area-histogram-rent');
    expect(priceHistogramSaleRenderer.id).toBe('price-per-area-histogram-sale');
  });

  it('rent renderer uses bar traces restricted to rent bins', () => {
    const fig = priceHistogramRentRenderer.render(dataBasis);
    expect(fig.data).toHaveLength(1);
    expect(fig.data[0].type).toBe('bar');
    expect(fig.data[0].x).toEqual(['9-10', '10-11']);
    expect(fig.data[0].y).toEqual([5, 7]);
  });

  it('sale renderer uses bar traces restricted to sale bins (different scale)', () => {
    const fig = priceHistogramSaleRenderer.render(dataBasis);
    expect(fig.data).toHaveLength(1);
    expect(fig.data[0].type).toBe('bar');
    expect(fig.data[0].x).toEqual(['2250-2500', '2500-2750']);
    expect(fig.data[0].y).toEqual([9, 14]);
  });

  it('both renderers return safe empty data/layout for missing data_basis input', () => {
    expect(priceHistogramRentRenderer.render(null).data).toEqual([]);
    expect(priceHistogramSaleRenderer.render(undefined).data).toEqual([]);
    expect(priceHistogramRentRenderer.render({}).data).toEqual([]);
    expect(priceHistogramSaleRenderer.render(null).layout).toBeTypeOf('object');
  });
});

describe('price histogram renderers with district/neighbourhood-dimensioned rows (FEATURE-016)', () => {
  const dimensioned = {
    price_per_area_histogram: [
      { operation: 'sale', district: 'Extramurs', neighborhood: 'Arrancapins', bin_start_price_m2: 3000, bin_end_price_m2: 3250, count_listings: 7 },
      { operation: 'sale', district: 'Ciutat Vella', neighborhood: 'El Carme', bin_start_price_m2: 3000, bin_end_price_m2: 3250, count_listings: 3 },
      { operation: 'sale', district: 'Ciutat Vella', neighborhood: 'El Carme', bin_start_price_m2: 2750, bin_end_price_m2: 3000, count_listings: 2 },
      { operation: 'rent', district: 'Extramurs', neighborhood: 'Arrancapins', bin_start_price_m2: 12, bin_end_price_m2: 13, count_listings: 5 },
      { operation: 'rent', district: 'Ciutat Vella', neighborhood: 'El Carme', bin_start_price_m2: 12, bin_end_price_m2: 13, count_listings: 4 },
    ],
  };

  it('collapses sale rows back to one bar per price bin with summed counts', () => {
    const [trace] = priceHistogramSaleRenderer.render(dimensioned).data;
    expect(trace.x).toEqual(['2750-3000', '3000-3250']);
    expect(trace.y).toEqual([2, 10]);
  });

  it('collapses rent rows back to one bar per price bin with summed counts', () => {
    const [trace] = priceHistogramRentRenderer.render(dimensioned).data;
    expect(trace.x).toEqual(['12-13']);
    expect(trace.y).toEqual([9]);
  });
});
