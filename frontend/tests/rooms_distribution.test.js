import { describe, it, expect } from 'vitest';
import { roomsDistributionRenderer } from '../src/charts/rooms_distribution.js';

describe('roomsDistributionRenderer', () => {
  it('has a stable id and title', () => {
    expect(roomsDistributionRenderer.id).toBe('rooms-distribution');
    expect(typeof roomsDistributionRenderer.title).toBe('string');
  });

  it('handles numeric room values deterministically, sorted ascending', () => {
    const dataBasis = {
      rooms_distribution: [
        { operation: 'sale', rooms: 3, count_listings: 25 },
        { operation: 'sale', rooms: 2, count_listings: 20 },
        { operation: 'rent', rooms: 2, count_listings: 15 },
        { operation: 'rent', rooms: 3, count_listings: 18 },
      ],
    };
    const fig = roomsDistributionRenderer.render(dataBasis);
    const sale = fig.data.find((t) => t.meta?.operation === 'sale');
    expect(sale.x).toEqual(['2', '3']);
    expect(sale.y).toEqual([20, 25]);
    expect(fig.layout.barmode).toBe('group');
  });

  it('handles string room values deterministically', () => {
    const dataBasis = {
      rooms_distribution: [
        { operation: 'sale', rooms: '4+', count_listings: 5 },
        { operation: 'sale', rooms: 1, count_listings: 10 },
      ],
    };
    const fig = roomsDistributionRenderer.render(dataBasis);
    const sale = fig.data.find((t) => t.meta?.operation === 'sale');
    expect(sale.x).toContain('4+');
    expect(sale.x).toContain('1');
  });

  it('returns safe empty data/layout for missing data_basis input', () => {
    expect(roomsDistributionRenderer.render(null).data).toEqual([]);
    expect(roomsDistributionRenderer.render(undefined).data).toEqual([]);
    expect(roomsDistributionRenderer.render({}).data).toEqual([]);
    expect(roomsDistributionRenderer.render(null).layout).toBeTypeOf('object');
  });
});

describe('roomsDistributionRenderer with district/neighbourhood-dimensioned rows (FEATURE-016)', () => {
  const dimensioned = {
    rooms_distribution: [
      { operation: 'sale', district: 'Extramurs', neighborhood: 'Arrancapins', rooms: 3, count_listings: 4 },
      { operation: 'sale', district: 'Ciutat Vella', neighborhood: 'El Carme', rooms: 3, count_listings: 5 },
      { operation: 'sale', district: 'Ciutat Vella', neighborhood: 'El Carme', rooms: 2, count_listings: 1 },
    ],
  };

  it('collapses the rows back to one bar per room count with summed counts', () => {
    const fig = roomsDistributionRenderer.render(dimensioned);
    const sale = fig.data.find((t) => t.meta?.operation === 'sale');
    expect(sale.x).toEqual(['2', '3']);
    expect(sale.y).toEqual([1, 9]);
  });
});
