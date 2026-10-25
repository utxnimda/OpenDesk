import { describe, expect, it } from 'vitest';
import { formatStatisticsNumber, formatStatisticsOutputNumber } from '../../apps/features/tower_assistant/frontend/statisticsNumber';

describe('statistics number presentation', () => {
  it('preserves full numeric precision in raw mode, including small values', () => {
    expect(formatStatisticsNumber(12345.678901)).toBe('12,345.678901');
    expect(formatStatisticsNumber(0.0000123456789)).toBe('0.0000123456789');
    expect(formatStatisticsNumber(-0)).toBe('0');
  });

  it('rounds output metrics to whole numbers only in full mode, without negative zero', () => {
    for (const [input, output] of [
      [12345.678901, '12,346'], [12345.49, '12,345'], [12345.5, '12,346'],
      [0.49, '0'], [0.5, '1'], [-0.49, '0'], [-0.5, '-1'], [-0, '0'],
      [12, '12'], [100000000.2, '100,000,000'], [0.0000123456789, '0'],
    ] as const) expect(formatStatisticsOutputNumber(input, 'raw')).toBe(output);
    // An output-only formatter must not change detail precision or its source.
    const value = 12345.678901;
    expect(formatStatisticsOutputNumber(value)).toBe('12,346');
    expect(formatStatisticsNumber(value)).toBe('12,345.678901');
    expect(value).toBe(12345.678901);
  });

  it('keeps compact output at four significant digits and unknown quantities explicit', () => {
    for (const value of [1234.56, 12345, 123456789, 1234567890123, 12345678901234568, 0.123456, 1.23456e-8, 1.23456e24]) {
      expect(formatStatisticsOutputNumber(value, 'compact')).toBe(formatStatisticsNumber(value, 'compact'));
    }
    for (const value of [undefined, null, NaN, Infinity, -Infinity]) {
      expect(formatStatisticsOutputNumber(value)).toBe('未知');
      expect(formatStatisticsOutputNumber(value, 'compact')).toBe('未知');
    }
  });

  it('uses four significant digits with the explicit Chinese unit scale', () => {
    for (const [input, output] of [
      [1234.56, '1.235 千'], [12345, '1.235 万'], [123456789, '1.235 亿'],
      [1234567890123, '1.235 兆'], [12345678901234568, '1.235 京'],
      [1000000, '100 万'], [-12345, '-1.235 万'], [0.123456, '0.1235'],
    ] as const) expect(formatStatisticsNumber(input, 'compact')).toBe(output);
  });

  it('promotes rounded values at unit boundaries and handles zero and extreme values', () => {
    expect(formatStatisticsNumber(999.99, 'compact')).toBe('1 千');
    expect(formatStatisticsNumber(9999.9, 'compact')).toBe('1 万');
    expect(formatStatisticsNumber(99999999, 'compact')).toBe('1 亿');
    expect(formatStatisticsNumber(0, 'compact')).toBe('0');
    expect(formatStatisticsNumber(1.23456e24, 'compact')).toBe('1.235 × 10^24');
    expect(formatStatisticsNumber(1.23456e-8, 'compact')).toBe('1.235 × 10^-8');
  });

  it('keeps missing or invalid quantities unknown in either display mode', () => {
    for (const value of [undefined, null, NaN, Infinity, -Infinity]) {
      expect(formatStatisticsNumber(value, 'raw')).toBe('未知');
      expect(formatStatisticsNumber(value, 'compact')).toBe('未知');
    }
  });
});
