"use client";
import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { Languages } from "lucide-react";
import { LOCALE_COOKIE, parseLocale, translate, type Locale } from "@/lib/i18n";
type LanguageContext = {
  locale: Locale;
  setLocale: (locale: Locale) => void;
  t: <T>(value: T) => T;
};
const Context = createContext<LanguageContext | null>(null);
export function LanguageProvider({
  initialLocale,
  children,
}: {
  initialLocale: Locale;
  children: ReactNode;
}) {
  const [locale, setLanguage] = useState(initialLocale);
  useEffect(() => {
    document.documentElement.lang = locale;
  }, [locale]);
  const value = useMemo<LanguageContext>(
    () => ({
      locale,
      setLocale(next) {
        const language = parseLocale(next);
        document.cookie = `${LOCALE_COOKIE}=${language}; Path=/; Max-Age=31536000; SameSite=Lax${location.protocol === "https:" ? "; Secure" : ""}`;
        setLanguage(language);
      },
      t: <T,>(text: T) => translate(text, locale),
    }),
    [locale],
  );
  return <Context.Provider value={value}>{children}</Context.Provider>;
}
export function useI18n() {
  const context = useContext(Context);
  if (!context) throw new Error("LanguageProvider is required");
  return context;
}
export function LanguageSwitcher() {
  const { locale, setLocale, t } = useI18n();
  return (
    <label className="language-switcher">
      <Languages size={16} aria-hidden="true" />
      <span className="sr-only">{t("语言")}</span>
      <select
        aria-label="語言 / Language"
        value={locale}
        onChange={(e) => setLocale(parseLocale(e.target.value))}
      >
        <option value="zh-Hant" lang="zh-Hant">
          繁體中文
        </option>
        <option value="en" lang="en">
          English
        </option>
      </select>
    </label>
  );
}
