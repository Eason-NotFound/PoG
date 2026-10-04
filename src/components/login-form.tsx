"use client";
import { useI18n, LanguageSwitcher } from "@/components/language-provider";
import { useState } from "react";
import { portalA2Login } from "@/lib/portal-a2";
import {
  ArrowRight,
  Fingerprint,
  ShieldCheck,
  HeartHandshake,
} from "lucide-react";
const examples = [
  ["donor", "捐款人", "仅捐款人工作区"],
  ["foundation", "基金会", "捐款人 + 基金会"],
  ["recipient", "受捐机构", "仅受捐机构工作区"],
  ["admin", "管理员", "全部工作区 + 权限管理"],
  ["maintainer", "维护人员", "仅管理员分配的页面"],
  ["donor2", "捐款人 B", "验证个人数据隔离"],
];
export default function LoginForm({
  integration = false,
}: {
  integration?: boolean;
}) {
  const { t } = useI18n();

  const [username, setUsername] = useState("donor"),
    [password, setPassword] = useState(""),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      if (integration) {
        const result = await portalA2Login(username, password);
        window.location.assign(result.redirect);
        return;
      }
      const r = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password }),
      });
      const d = await r.json();
      if (!r.ok) throw new Error(d.error);
      window.location.assign(d.redirect);
    } catch (e) {
      setError(e instanceof Error ? e.message : "登录失败");
      setBusy(false);
    }
  }
  return (
    <main className="login">
      <section className="login-story">
        <div className="brand">
          <span className="brand-mark">PoG</span>
          <div>
            <b>Proof of Giving</b>
            <span>EVERY GIFT, ACCOUNTED FOR.</span>
          </div>
        </div>
        <div className="login-headline">
          <span className="pill">{t("透明公益 · 受控支付")}</span>
          <h1>
            {t("让善意")}
            <br />
            {t("看得见去向。")}
          </h1>
          <p>
            {t("从一份支持，到一次真实的交付。")}
            <br />
            {t("让每一步都有记录，每笔支出都有依据。")}
          </p>
        </div>
        <div className="login-values">
          <span>
            <HeartHandshake />
            {t("多角色协作")}
          </span>
          <span>
            <Fingerprint />
            {t("凭证可追溯")}
          </span>
          <span>
            <ShieldCheck />
            {t("按权限访问")}
          </span>
        </div>
        <div className="demo-disclaimer">
          {t("本地演示环境 · 使用模拟支付和资金状态")}
          <br />
          {t(
            integration
              ? "已连接本地 Anvil 链 · 链上 AI 评估待接入 · 无真实资金"
              : "未连接真实银行、AI 服务或区块链",
          )}
        </div>
      </section>
      <section className="login-panel">
        <div className="login-language">
          <LanguageSwitcher />
        </div>
        <p className="eyebrow">{t("WELCOME TO POG")}</p>
        <h2>{t("登录你的工作区")}</h2>
        <p className="muted">{t("服务器根据账户身份授予工作区和页面权限。")}</p>
        <form onSubmit={submit} className="login-form">
          <label>
            {t("演示账号")}
            <input
              autoComplete="username"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              required
            />
          </label>
          <label>
            {t("密码")}
            <input
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder={t("输入本地演示密码")}
              required
            />
          </label>
          {t(
            error && (
              <p role="alert" className="error">
                {t(error)}
              </p>
            ),
          )}
          <button className="primary" disabled={busy}>
            {t(busy ? "正在登录…" : "进入工作区")}
            <ArrowRight size={17} />
          </button>
        </form>
        <details className="demo-accounts" open>
          <summary>{t("本地演示账号")}</summary>
          <p>
            {t("默认测试密码：")}
            <code>
              {integration ? t("使用本轮受控测试账号密码") : "PoG-demo-2026"}
            </code>
            <br />
            <small>
              {t(
                "可在 .env.local 中设置；已初始化后需重新建演示数据才会改变。",
              )}
            </small>
          </p>
          <div className="account-grid">
            {t(
              examples
                .filter(
                  ([id]) =>
                    !integration ||
                    ["donor", "foundation", "recipient", "admin"].includes(id),
                )
                .map(([id, label, desc]) => (
                  <button
                    key={id}
                    onClick={() => {
                      setUsername(id);
                      setPassword("");
                    }}
                    className={username === id ? "selected" : ""}
                  >
                    <strong>{t(label)}</strong>
                    <small>
                      {t(
                        integration
                          ? id === "admin"
                            ? "仅独立 human_approver，不代其他角色"
                            : "仅本人角色工作区"
                          : desc,
                      )}
                    </small>
                    <code>{id}</code>
                  </button>
                )),
            )}
          </div>
        </details>
        <p className="tiny muted">
          {t("这些公开测试账号仅用于本地演示，不能用于生产部署。")}
        </p>
      </section>
    </main>
  );
}
