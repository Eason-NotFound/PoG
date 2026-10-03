export type Role =
  "donor" | "foundation" | "recipient" | "admin" | "maintainer";
export type Portal = "donor" | "foundation" | "recipient" | "admin";
export const roleNames: Record<Role, string> = {
  donor: "捐款人",
  foundation: "基金会",
  recipient: "受捐机构",
  admin: "管理员",
  maintainer: "页面维护人员",
};
export type PageDef = {
  id: string;
  portal: Portal;
  slug: string;
  label: string;
  title: string;
  description: string;
  icon: string;
};
const define = (
  portal: Portal,
  slug: string,
  label: string,
  title: string,
  description: string,
  icon: string,
): PageDef => ({
  id: `${portal}.${slug}`,
  portal,
  slug,
  label,
  title,
  description,
  icon,
});
export const pages: PageDef[] = [
  ...(["donor", "foundation", "recipient", "admin"] as Portal[]).map((portal) =>
    define(
      portal,
      "funds",
      "透明资金链",
      "看见每一笔善款的去向",
      "同一份账本，展示兑换、锁款、拨付、兑付与退款。",
      "Route",
    ),
  ),
  define(
    "foundation",
    "evidence",
    "采购与发货证明",
    "提交基金会采购证明",
    "上传发票及发货证明，与受捐机构的收货照片共同组成审计材料。",
    "Upload",
  ),
  define(
    "foundation",
    "appeals",
    "申诉记录",
    "提交和跟踪申诉",
    "对人工审计结果提出申诉，查看管理员处理进度。",
    "ClipboardCheck",
  ),
  define(
    "recipient",
    "appeals",
    "申诉记录",
    "提交和跟踪申诉",
    "对人工审计结果提出申诉，查看管理员处理进度。",
    "ClipboardCheck",
  ),
  define(
    "admin",
    "appeals",
    "申诉记录",
    "处理审计申诉",
    "区分未处理与已处理申诉，记录处理意见。",
    "ClipboardCheck",
  ),

  define(
    "donor",
    "overview",
    "我的总览",
    "每一份善意，都有去向",
    "查看你支持的项目，以及每一笔捐款的使用情况。",
    "LayoutDashboard",
  ),
  define(
    "donor",
    "projects",
    "探索项目",
    "支持你关心的改变",
    "从用途、预算和公开进展了解公益项目。",
    "Compass",
  ),
  define(
    "donor",
    "donations",
    "我的捐款",
    "你的支持，有据可查",
    "查看捐款记录、凭证及支付确认状态。",
    "HeartHandshake",
  ),
  define(
    "donor",
    "allocations",
    "资金去向",
    "从捐款到实际交付",
    "只在供应商实际收款后生成最终支出分配。",
    "Route",
  ),
  define(
    "donor",
    "hash",
    "证明与查询",
    "用 Hash 找到原始记录",
    "查询有权访问的单据版本、文件和证明。",
    "Fingerprint",
  ),
  define(
    "foundation",
    "overview",
    "工作台",
    "项目资金与审批",
    "聚焦待办，掌握预算预留与实际付款。",
    "LayoutDashboard",
  ),
  define(
    "foundation",
    "projects",
    "项目与预算",
    "让资金按规则使用",
    "项目规则、预算类别和资金池分开管理。",
    "FolderKanban",
  ),
  define(
    "foundation",
    "review",
    "采购审批",
    "采购前人工审核",
    "查看采购和 AI 报告，独立作出批准决定。",
    "ClipboardCheck",
  ),
  define(
    "foundation",
    "payment",
    "交付与付款",
    "核验交付，再批准付款",
    "采购批准与付款批准是两次独立的审批。",
    "BadgeCheck",
  ),
  define(
    "foundation",
    "vendors",
    "供应商",
    "认证供应商目录",
    "收款对象与采购单绑定，不能临时替换。",
    "Store",
  ),
  define(
    "foundation",
    "ledger",
    "账本与证明",
    "每一次变更都有记录",
    "查看项目注资、预算预留、释放和供应商付款。",
    "BookOpen",
  ),
  define(
    "foundation",
    "hash",
    "Hash 查询",
    "凭证与记录查询",
    "定位已提交单据及其不可变版本。",
    "Fingerprint",
  ),
  define(
    "recipient",
    "overview",
    "我的工作台",
    "把需求变成可核验的交付",
    "提交采购、补充证据，并跟踪审批进度。",
    "LayoutDashboard",
  ),
  define(
    "recipient",
    "budget",
    "项目预算",
    "了解可申请的预算",
    "按已发布的项目规则准备采购需求。",
    "Wallet",
  ),
  define(
    "recipient",
    "procurements",
    "采购申请",
    "准备好下一次采购",
    "填写物品、供应商和报价，提交采购前审核。",
    "ShoppingBag",
  ),
  define(
    "recipient",
    "delivery",
    "收货照片与验收",
    "记录真实的交付",
    "上传收到物资的照片和验收说明；采购发票由基金会提交。",
    "PackageCheck",
  ),
  define(
    "recipient",
    "reimbursements",
    "小额报销",
    "每笔垫付，单独核对",
    "保存报销草稿；正式支付适配仍待接入。",
    "Receipt",
  ),
  define(
    "recipient",
    "hash",
    "Hash 查询",
    "我的凭证与记录",
    "查看本机构有权访问的证据与单据。",
    "Fingerprint",
  ),
  define(
    "admin",
    "overview",
    "系统总览",
    "运行状态与业务同步",
    "掌握待处理事项与各模块接入状态。",
    "LayoutDashboard",
  ),
  define(
    "admin",
    "users",
    "用户与权限",
    "让每个人访问正确的页面",
    "角色决定工作区，维护人员按具体页面授权。",
    "ShieldCheck",
  ),
  define(
    "admin",
    "organisations",
    "机构与供应商",
    "机构认证与角色绑定",
    "查看 Foundation、Recipient、Vendor 的认证资料。",
    "Building2",
  ),
  define(
    "admin",
    "audit",
    "审计记录",
    "AI 审核后的人工审计",
    "查看待审计与已审计项目，核对双方证明并记录人工结论。",
    "ScrollText",
  ),
  define(
    "admin",
    "connections",
    "系统连接",
    "清楚知道哪些已经接通",
    "区分本地演示能力与待接入的真实服务。",
    "Cable",
  ),
  define(
    "admin",
    "hash",
    "Hash 查询",
    "跨工作区记录查询",
    "管理员可审计全平台业务记录。",
    "Fingerprint",
  ),
];
pages.sort(
  (a, b) =>
    ["donor", "foundation", "recipient", "admin"].indexOf(a.portal) -
      ["donor", "foundation", "recipient", "admin"].indexOf(b.portal) ||
    Number(b.slug === "overview") - Number(a.slug === "overview"),
);
export type AccessUser = {
  id: string;
  role: Role;
  name: string;
  orgId: string;
  grants: string[];
  active: boolean;
};
export const portalOrder: Portal[] = [
  "donor",
  "foundation",
  "recipient",
  "admin",
];
export function canRead(user: AccessUser, pageId: string) {
  if (!user.active || !pages.some((p) => p.id === pageId)) return false;
  if (user.role === "admin") return true;
  if (user.role === "maintainer") return user.grants.includes(pageId);
  const portal = pages.find((p) => p.id === pageId)!.portal;
  return (
    portal === user.role || (user.role === "foundation" && portal === "donor")
  );
}
export function allowedPages(user: AccessUser) {
  return pages.filter((p) => canRead(user, p.id));
}
export function firstPath(user: AccessUser) {
  const accessible = allowedPages(user);
  const p = accessible.find((p) => p.portal === user.role) || accessible[0];
  return p ? `/${p.portal}/${p.slug}` : "/no-access";
}
export function pageByPath(portal: string, slug: string) {
  return pages.find((p) => p.portal === portal && p.slug === slug);
}
export const actionPages = {
  resubmitFoundationProcurement: "foundation.evidence",
  foundationProcurement: "foundation.evidence",
  submitFoundationProof: "foundation.evidence",
  reviewCase: "admin.audit",
  resolveAppeal: "admin.appeals",
  foundationAppeal: "foundation.appeals",
  recipientAppeal: "recipient.appeals",
  donate: "donor.projects",
  createProject: "foundation.projects",
  deposit: "foundation.projects",
  createProcurement: "recipient.procurements",
  resubmitProcurement: "recipient.procurements",
  deliver: "recipient.delivery",
  reimbursement: "recipient.reimbursements",
  approvePurchase: "foundation.review",
  requestInfo: "foundation.review",
  approvePayment: "foundation.payment",
  updatePermissions: "admin.users",
  createMaintainer: "admin.users",
} as const;
export type Action = keyof typeof actionPages;
export function canAct(user: AccessUser, action: Action) {
  if (!canRead(user, actionPages[action]) || user.role === "maintainer")
    return false;
  if (
    [
      "updatePermissions",
      "createMaintainer",
      "reviewCase",
      "resolveAppeal",
    ].includes(action)
  )
    return user.role === "admin";
  // Admin has all-page visibility, but no implicit financial approval power.
  if (user.role === "admin") return false;
  return true;
}
