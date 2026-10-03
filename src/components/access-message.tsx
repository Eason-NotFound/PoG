"use client";
import Link from "next/link";
import { LanguageSwitcher, useI18n } from "./language-provider";
export default function AccessMessage({
  kind,
  name,
  href,
}: {
  kind: "unassigned" | "denied" | "missing";
  name?: string;
  href: string;
}) {
  const { t } = useI18n();
  const title =
    kind === "unassigned"
      ? "暂未分配页面"
      : kind === "missing"
        ? "页面不存在"
        : "此页面不在你的权限范围内";
  return (
    <main className="access-denied">
      <LanguageSwitcher />
      <span className="brand-mark">PoG</span>
      <h1>{t(title)}</h1>
      <p>
        {kind === "unassigned" ? (
          t("你的账户已登录，请管理员为你分配负责的页面。")
        ) : kind === "missing" ? (
          t("找不到这个页面，请返回工作区。")
        ) : (
          <>
            {t("当前账户：")}
            {name}
            {t("。请返回已授权工作区，或联系管理员调整页面授权。")}
          </>
        )}
      </p>
      <Link className="button primary" href={href}>
        {t(kind === "unassigned" ? "返回登录页" : "返回我的工作区")}
      </Link>
    </main>
  );
}
