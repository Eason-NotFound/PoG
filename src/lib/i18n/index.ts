import messages from "./messages.json";
export type Locale = "zh-Hant" | "en";
export const LOCALE_COOKIE = "pog-language";
export function parseLocale(value: unknown): Locale {
  return value === "en" ? "en" : "zh-Hant";
}
const catalog: Record<string, Record<Locale, string>> = messages;
const traditional = new Map(
  Object.values(catalog).map((entry) => [entry["zh-Hant"], entry]),
);
/** Translate presentation text only. Never use this on stored records or hash input. */
export function translate<T>(value: T, locale: Locale): T {
  if (typeof value !== "string") return value;
  const entry =
    (Object.hasOwn(catalog, value) ? catalog[value] : undefined) ||
    traditional.get(value);
  if (entry) return entry[locale] as T;
  const validation = value.match(/^(.+)不能为空，且不能超过 (\d+) 个字符$/);
  if (validation)
    return (
      locale === "en"
        ? `${translate(validation[1], locale)} is required and must not exceed ${validation[2]} characters`
        : `${translate(validation[1], locale)}不能為空，且不能超過 ${validation[2]} 個字元`
    ) as T;
  const recordCount = value.match(/^(\d+) 笔$/);
  if (recordCount)
    return (
      locale === "en"
        ? `${recordCount[1]} ${recordCount[1] === "1" ? "request" : "requests"}`
        : `${recordCount[1]} 筆`
    ) as T;
  const donationCount = value.match(/^我的捐款 · (\d+) 笔$/);
  if (donationCount)
    return (
      locale === "en"
        ? `My donations · ${donationCount[1]}`
        : `我的捐款 · ${donationCount[1]} 筆`
    ) as T;
  if (value.startsWith("累计注资 "))
    return (translate("累计注资 ", locale) + value.slice(5)) as T;
  // Unknown text is user-authored content and must retain its original wording.
  return value;
}
export function formatMoney(value: number, locale: Locale) {
  return new Intl.NumberFormat(locale === "en" ? "en-HK" : "zh-HK", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(value / 100);
}
export function formatDate(value: string, locale: Locale) {
  return new Intl.DateTimeFormat(locale === "en" ? "en-HK" : "zh-HK", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    timeZone: "Asia/Hong_Kong",
  }).format(new Date(value));
}
