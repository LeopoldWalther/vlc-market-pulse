import { describe, it, expect } from 'vitest';
import { sumCountsBy } from '../src/reaggregate.js';

const SIZE_KEYS = ['operation', 'bin_start_m2', 'bin_end_m2'];

describe('sumCountsBy', () => {
  it('returns rows already at the requested grain with identical keys and counts', () => {
    const rows = [
      { operation: 'sale', bin_start_m2: 100, bin_end_m2: 110, count_listings: 15 },
      { operation: 'rent', bin_start_m2: 100, bin_end_m2: 110, count_listings: 10 },
    ];
    expect(sumCountsBy(rows, SIZE_KEYS)).toEqual(rows);
  });

  it('collapses rows split across districts and neighbourhoods into one row per key', () => {
    const rows = [
      { operation: 'sale', district: 'Extramurs', neighborhood: 'Arrancapins', bin_start_m2: 100, bin_end_m2: 110, count_listings: 4 },
      { operation: 'sale', district: 'Extramurs', neighborhood: 'La Roqueta', bin_start_m2: 100, bin_end_m2: 110, count_listings: 6 },
      { operation: 'sale', district: 'Ciutat Vella', neighborhood: 'El Carme', bin_start_m2: 110, bin_end_m2: 120, count_listings: 3 },
      { operation: 'rent', district: 'Ciutat Vella', neighborhood: 'El Carme', bin_start_m2: 100, bin_end_m2: 110, count_listings: 2 },
    ];
    expect(sumCountsBy(rows, SIZE_KEYS)).toEqual([
      { operation: 'sale', bin_start_m2: 100, bin_end_m2: 110, count_listings: 10 },
      { operation: 'sale', bin_start_m2: 110, bin_end_m2: 120, count_listings: 3 },
      { operation: 'rent', bin_start_m2: 100, bin_end_m2: 110, count_listings: 2 },
    ]);
  });

  it('preserves the grand total of count_listings', () => {
    const rows = [
      { operation: 'sale', district: 'A', rooms: 3, count_listings: 7 },
      { operation: 'sale', district: 'B', rooms: 3, count_listings: 5 },
      { operation: 'sale', district: 'B', rooms: 4, count_listings: 1 },
    ];
    const total = (list) => list.reduce((sum, r) => sum + r.count_listings, 0);
    expect(total(sumCountsBy(rows, ['operation', 'rooms']))).toBe(total(rows));
  });

  it('keeps numeric and string key values apart', () => {
    const rows = [
      { operation: 'sale', rooms: 4, count_listings: 1 },
      { operation: 'sale', rooms: '4', count_listings: 2 },
    ];
    expect(sumCountsBy(rows, ['operation', 'rooms'])).toHaveLength(2);
  });

  it('treats a missing or non-numeric count as 0', () => {
    const rows = [
      { operation: 'sale', rooms: 2, count_listings: 3 },
      { operation: 'sale', rooms: 2 },
      { operation: 'sale', rooms: 2, count_listings: 'n/a' },
    ];
    expect(sumCountsBy(rows, ['operation', 'rooms'])).toEqual([
      { operation: 'sale', rooms: 2, count_listings: 3 },
    ]);
  });

  it('returns an empty array for empty, null or undefined input', () => {
    expect(sumCountsBy([], SIZE_KEYS)).toEqual([]);
    expect(sumCountsBy(null, SIZE_KEYS)).toEqual([]);
    expect(sumCountsBy(undefined, SIZE_KEYS)).toEqual([]);
  });

  it('does not mutate its input rows', () => {
    const rows = [
      { operation: 'sale', district: 'A', rooms: 3, count_listings: 7 },
      { operation: 'sale', district: 'B', rooms: 3, count_listings: 5 },
    ];
    const snapshot = JSON.parse(JSON.stringify(rows));
    sumCountsBy(rows, ['operation', 'rooms']);
    expect(rows).toEqual(snapshot);
  });
});
