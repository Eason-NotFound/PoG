import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import ts from "typescript";
import { pages, roleNames } from "../src/lib/access";
import {
  translate,
  parseLocale,
  formatDate,
  formatMoney,
} from "../src/lib/i18n";
import messages from "../src/lib/i18n/messages.json";

test("both locales cover every page title, navigation label, description and role", () => {
  const strings = [
    ...Object.values(roleNames),
    ...pages.flatMap((p) => [p.label, p.title, p.description]),
  ];
  for (const value of strings) {
    assert.ok(Object.hasOwn(messages, value), value);
    assert.doesNotMatch(translate(value, "en"), /\p{Script=Han}/u, value);
  }
  assert.equal(translate("用户与权限", "zh-Hant"), "用戶與權限");
  assert.equal(translate("登录你的工作区", "zh-Hant"), "登錄你的工作區");
  for (const [key, values] of Object.entries(messages)) {
    assert.ok(values.en.trim() && values["zh-Hant"].trim(), key);
    assert.doesNotMatch(values.en, /\p{Script=Han}/u, key);
  }
});

test("all static interface strings and backend messages have translations", () => {
  for (const file of [
    "src/components/portal-app.tsx",
    "src/components/funds-flow.tsx",
    "src/lib/payments.ts",
    "src/app/api/payment/[...path]/route.ts",
    "src/components/casework.tsx",
    "src/components/login-form.tsx",
    "src/lib/service.ts",
    "src/app/api/[...path]/route.ts",
  ]) {
    const source = ts.createSourceFile(
      file,
      fs.readFileSync(file, "utf8"),
      ts.ScriptTarget.Latest,
      true,
    );
    function visit(node: ts.Node) {
      if (
        (ts.isStringLiteral(node) || ts.isJsxText(node)) &&
        /\p{Script=Han}/u.test(node.text)
      ) {
        const value = ts.isJsxText(node)
          ? node.text.replace(/\s+/g, " ").trim()
          : node.text;
        assert.ok(Object.hasOwn(messages, value), `${file}: ${value}`);
      }
      ts.forEachChild(node, visit);
    }
    visit(source);
  }
});

test("language is restricted to Traditional Chinese and English; records stay intact", () => {
  assert.equal(parseLocale(undefined), "zh-Hant");
  assert.equal(parseLocale("zh-CN"), "zh-Hant");
  assert.equal(parseLocale("en"), "en");
  for (const value of [
    "0x" + "a".repeat(64),
    "PR-001",
    "constructor",
    "用户自填的单据内容",
    "發票.pdf",
  ]) {
    assert.equal(translate(value, "en"), value);
    assert.equal(translate(value, "zh-Hant"), value);
  }
  const record = { content: "输入原文", hash: "a".repeat(64) };
  assert.equal(translate(record, "en"), record);
});

test("dynamic validation messages and dates use the selected language", () => {
  assert.equal(
    translate("项目名称不能为空，且不能超过 100 个字符", "en"),
    "Project name is required and must not exceed 100 characters",
  );
  assert.equal(translate("我的捐款 · 3 笔", "en"), "My donations · 3");
  assert.equal(translate("累计注资 1,000.00", "en"), "Total funding 1,000.00");
  assert.equal(formatMoney(123456, "en"), "1,234.56");
  assert.equal(translate("1 笔", "en"), "1 request");
  assert.equal(translate("0 笔", "en"), "0 requests");
  assert.equal(translate("2 笔", "zh-Hant"), "2 筆");
  const instant = "2026-10-02T01:00:00Z";
  assert.notEqual(formatDate(instant, "en"), formatDate(instant, "zh-Hant"));
});
