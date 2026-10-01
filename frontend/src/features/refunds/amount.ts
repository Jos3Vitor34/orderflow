/** Returns an API decimal, undefined for a full refund, or null for invalid input. */
export function normalizeRefundAmount(input: string): string | undefined | null {
  const value = input.trim().replace(',', '.')
  if (!value) return undefined
  if (!/^\d+(?:\.\d{1,2})?$/.test(value)) return null
  const [integer = '', fraction = ''] = value.split('.')
  const whole = integer.replace(/^0+(?=\d)/, '')
  const cents = BigInt(whole) * 100n + BigInt((fraction + '00').slice(0, 2))
  if (cents <= 0n || cents > 999999999999n) return null
  return `${whole}.${(fraction + '00').slice(0, 2)}`
}
