// Countries a visitor's test call can ring (visitor calls spec T2). The backend checks the code too.
export const PHONE_COUNTRIES = [
  { code: '91', name: 'India', min: 10, max: 10 },
  { code: '81', name: 'Japan', min: 9, max: 10 },
  { code: '82', name: 'South Korea', min: 9, max: 10 },
  { code: '852', name: 'Hong Kong', min: 8, max: 8 },
  { code: '886', name: 'Taiwan', min: 9, max: 9 },
  { code: '65', name: 'Singapore', min: 8, max: 8 },
  { code: '60', name: 'Malaysia', min: 9, max: 10 },
  { code: '62', name: 'Indonesia', min: 9, max: 12 },
  { code: '63', name: 'Philippines', min: 10, max: 10 },
  { code: '66', name: 'Thailand', min: 9, max: 9 },
  { code: '84', name: 'Vietnam', min: 9, max: 10 },
  { code: '61', name: 'Australia', min: 9, max: 9 },
  { code: '64', name: 'New Zealand', min: 8, max: 10 },
]

/** The number in E.164, or null if it does not look like a number in that country. */
export function toE164(code: string, national: string): string | null {
  const country = PHONE_COUNTRIES.find((c) => c.code === code)
  if (!country) return null
  let digits = national.replace(/[\s\-().]/g, '')
  if (!/^\d+$/.test(digits)) return null
  if (digits.startsWith('0')) digits = digits.slice(1) // the trunk prefix used inside the country
  if (digits.length < country.min || digits.length > country.max) return null
  return `+${code}${digits}`
}

export function lastFour(e164: string): string {
  return e164.slice(-4)
}
