import type { Metadata } from "next";
import "./globals.css";
import { cookies } from "next/headers";
import { LOCALE_COOKIE, parseLocale } from "@/lib/i18n";
import { LanguageProvider } from "@/components/language-provider";
export const metadata: Metadata = {
  title: "PoG · Proof of Giving",
  description: "Transparent giving and controlled payments · PoG",
};
export default async function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const locale = parseLocale((await cookies()).get(LOCALE_COOKIE)?.value);
  return (
    <html lang={locale}>
      <body>
        <LanguageProvider initialLocale={locale}>{children}</LanguageProvider>
      </body>
    </html>
  );
}
