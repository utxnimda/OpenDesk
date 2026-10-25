export type StatisticsNumberMode = 'raw' | 'compact';

const rawFormat = new Intl.NumberFormat('zh-CN', { maximumSignificantDigits: 21 });
const outputFormat = new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 0 });
const compactFormat = new Intl.NumberFormat('zh-CN', { maximumSignificantDigits: 4 });
const units = [
  { threshold: 1e16, label: '京' },
  { threshold: 1e12, label: '兆' },
  { threshold: 1e8, label: '亿' },
  { threshold: 1e4, label: '万' },
  { threshold: 1e3, label: '千' },
];

/** Presentation only. Never feed the rounded representation back into totals or formulas. */
export function formatStatisticsNumber(value: number | null | undefined, mode: StatisticsNumberMode = 'raw'): string {
  if (value == null || !Number.isFinite(value)) return '未知';
  if (value === 0) return '0';
  if (mode === 'raw') return rawFormat.format(value);
  const rounded = Number(value.toPrecision(4));
  const magnitude = Math.abs(rounded);
  // Keep values outside the named-unit range readable without inventing units.
  if (magnitude >= 1e20 || magnitude < 0.001) {
    const [coefficient, exponent] = rounded.toExponential(3).split('e');
    return `${compactFormat.format(Number(coefficient))} × 10^${Number(exponent)}`;
  }
  const unit = units.find((entry) => magnitude >= entry.threshold);
  return unit ? `${compactFormat.format(rounded / unit.threshold)} ${unit.label}` : compactFormat.format(rounded);
}

/** Output metrics use whole numbers in full mode; attributes, timestamps,
 * formula steps and percentages keep the precision of formatStatisticsNumber. */
export function formatStatisticsOutputNumber(value: number | null | undefined, mode: StatisticsNumberMode = 'raw'): string {
  if (mode !== 'raw' || value == null || !Number.isFinite(value)) return formatStatisticsNumber(value, mode);
  // Intl can show "-0" after rounding a small negative delta; zero has no sign.
  return Math.abs(value) < 0.5 ? '0' : outputFormat.format(value);
}
