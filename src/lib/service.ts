import {
  assertPaymentBook,
  donateTokens,
  reserveTokens,
  releaseTokens,
  trackProject,
} from "./payments";
import {
  actionPages,
  canAct,
  canRead,
  pages,
  type AccessUser,
  type Action,
} from "./access";
import {
  audit,
  id,
  mutate,
  now,
  passwordHash,
  publicUser,
  readDB,
  sha,
  snapshot,
  type Database,
} from "./store";
import type {
  PageData,
  RecordSnapshot,
  Procurement,
  ReviewCase,
} from "./types";
export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}
function requireAccess(test: boolean, message = "没有此操作的权限") {
  if (!test) throw new ApiError(403, message);
}
function textValue(v: unknown, label: string, max = 200) {
  if (typeof v !== "string" || !v.trim() || v.length > max)
    throw new ApiError(400, `${label}不能为空，且不能超过 ${max} 个字符`);
  return v.trim();
}
function money(v: unknown) {
  if (
    typeof v !== "number" ||
    !Number.isSafeInteger(v) ||
    v <= 0 ||
    v > 100_000_000
  )
    throw new ApiError(400, "金额必须是合法的最小单位整数，且大于零");
  return v;
}
function projectVisible(u: AccessUser, p: Database["projects"][number]) {
  return (
    u.role === "admin" ||
    u.role === "maintainer" ||
    p.foundationId === u.orgId ||
    p.recipientId === u.orgId
  );
}
function ownedProjects(db: Database, u: AccessUser) {
  return db.projects.filter((p) => projectVisible(u, p));
}
export function pageData(u: AccessUser, pageId: string): PageData {
  requireAccess(canRead(u, pageId));
  const db = readDB();
  const [portal, slug] = pageId.split(".");
  if (slug === "hash") return {};
  // Page-specific projection: a page grant is never a grant to the entire database.
  if (portal === "donor") {
    const ds =
      u.role === "admin"
        ? db.donations
        : db.donations.filter((d) => d.ownerId === u.id);
    if (slug === "projects") return { projects: db.projects };
    return { projects: db.projects, donations: ds };
  }
  if (portal === "foundation" || portal === "recipient") {
    const projects = ownedProjects(db, u),
      ids = new Set(projects.map((p) => p.id));
    const claims = db.procurements.filter((p) => ids.has(p.projectId));
    if (slug === "vendors") return { vendors: db.vendors };
    if (slug === "ledger")
      return {
        projects,
        ledger: db.ledger.filter((l) => ids.has(l.projectId)),
      };
    if (slug === "projects" || slug === "budget") return { projects };
    if (slug === "reimbursements")
      return {
        reimbursements: db.reimbursements.filter(
          (r) => u.role === "admin" || r.orgId === u.orgId,
        ),
      };
    const reviews = db.reviews.filter((r) => ids.has(r.projectId));
    if (slug === "appeals")
      return {
        projects,
        procurements: claims,
        reviews,
        appeals: db.appeals.filter(
          (a) =>
            ids.has(a.projectId) && (u.role === "admin" || a.orgId === u.orgId),
        ),
      };
    return {
      projects,
      procurements: claims,
      vendors: db.vendors,
      reviews,
      evidence:
        u.role === "maintainer"
          ? []
          : db.evidence
              .filter((e) => claims.some((c) => c.id === e.procurementId))
              .map(({ content, ...metadata }) => metadata),
    };
  }
  if (pageId === "admin.users") return { users: db.users.map(publicUser) };
  if (pageId === "admin.audit" || pageId === "admin.appeals")
    return {
      projects: db.projects,
      procurements: db.procurements,
      reviews: db.reviews,
      ...(pageId === "admin.audit"
        ? {
            logs: db.logs,
            evidence:
              u.role === "maintainer"
                ? []
                : db.evidence.map(({ content, ...metadata }) => metadata),
          }
        : { appeals: db.appeals }),
    };
  if (pageId === "admin.organisations") return { vendors: db.vendors };
  if (pageId === "admin.overview")
    return {
      logs: db.logs.slice(0, 5),
      reviews: db.reviews,
      appeals: db.appeals,
    };
  return {};
}
export function canReadRecord(u: AccessUser, r: RecordSnapshot) {
  if (!u.active) return false;
  if (u.role === "admin") return true;
  if (u.role === "maintainer") return false; // Page maintenance does not expose private financial evidence.
  if (r.kind === "donation") return r.ownerId === u.id;
  const db = readDB();
  return (
    r.orgId === u.orgId ||
    db.projects.some(
      (p) =>
        p.id === r.projectId &&
        (p.foundationId === u.orgId ||
          (r.kind === "file" && p.recipientId === u.orgId)),
    )
  );
}
export function lookupHash(u: AccessUser, pageId: string, hash: string) {
  requireAccess(canRead(u, pageId) && pageId.endsWith(".hash"));
  if (!/^(0x)?[a-fA-F0-9]{64}$/.test(hash))
    throw new ApiError(400, "请输入完整的 64 位十六进制 Hash");
  const normalized = "0x" + hash.replace(/^0x/, "").toLowerCase();
  const db = readDB();
  return db.records.filter((r) => r.hash === normalized && canReadRecord(u, r));
}
export function evidenceFor(u: AccessUser, evidenceId: string) {
  const db = readDB();
  const e = db.evidence.find((e) => e.id === evidenceId);
  const r = e && db.records.find((r) => r.id === e.id);
  if (!e || !r || !canReadRecord(u, r))
    throw new ApiError(404, "未找到可访问记录");
  return e;
}
export function uploadEvidence(u: AccessUser, body: Record<string, unknown>) {
  requireAccess(canAct(u, "deliver") || canAct(u, "submitFoundationProof"));
  const name = textValue(body.name, "文件名", 120);
  const procurementId = textValue(body.procurementId, "采购编号");
  const kind = textValue(body.type, "证据类型");
  if (!["invoice", "dispatch", "grn", "photo", "quotation"].includes(kind))
    throw new ApiError(400, "不支持此证据类型");
  if (
    u.role === "foundation"
      ? !["invoice", "dispatch", "quotation"].includes(kind)
      : !["photo", "grn", "quotation"].includes(kind)
  )
    throw new ApiError(
      403,
      "基金会提交发票与发货证明；受捐机构提交收货照片与验收证明",
    );
  const content = textValue(body.content, "文件内容", 1_500_000);
  if (!/^[A-Za-z0-9+/]*={0,2}$/.test(content))
    throw new ApiError(400, "文件编码不正确");
  const bytes = Buffer.from(content, "base64");
  if (bytes.length === 0 || bytes.length > 1_048_576)
    throw new ApiError(400, "演示文件限制为每个 1 MB");
  const mime =
    bytes.subarray(0, 5).toString() === "%PDF-"
      ? "application/pdf"
      : bytes
            .subarray(0, 8)
            .equals(Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]))
        ? "image/png"
        : bytes[0] === 255 && bytes[1] === 216 && bytes[2] === 255
          ? "image/jpeg"
          : "";
  if (!mime) throw new ApiError(400, "仅支持内容有效的 PDF、PNG、JPEG 文件");
  if (kind === "photo" && !mime.startsWith("image/"))
    throw new ApiError(400, "收货照片必须是 PNG 或 JPEG 图片");
  return mutate((db) => {
    const p = db.procurements.find(
      (p) =>
        p.id === procurementId &&
        db.projects.some(
          (project) =>
            project.id === p.projectId &&
            (u.role === "foundation"
              ? project.foundationId === u.orgId
              : project.recipientId === u.orgId),
        ),
    );
    if (!p) throw new ApiError(404, "未找到可访问采购");
    const latest = latestReview(db, p.id, "delivery");
    const supplement =
      p.status === "payment_review" &&
      latest?.status === "reviewed" &&
      latest.decision !== "approved";
    if (
      kind === "quotation"
        ? p.status !== "needs_info"
        : !(p.status === "reserved" || supplement)
    )
      throw new ApiError(409, "当前阶段不能修改证据");
    if (kind !== "quotation") {
      if (supplement) p.status = "reserved";
      if (u.role === "foundation") p.foundationProof = undefined;
      else p.recipientProof = undefined;
    }
    const e = {
      id: id("FILE"),
      procurementId,
      ownerId: u.id,
      orgId: u.orgId,
      name,
      hash: sha(bytes),
      mime,
      size: bytes.length,
      content,
      type: kind,
      submittedRole: u.role as "foundation" | "recipient",
      createdAt: now(),
    };
    db.evidence.push(e);
    p.evidenceIds.push(e.id);
    db.records.push({
      id: e.id,
      kind: "file",
      hash: e.hash,
      title: name,
      ownerId: u.id,
      orgId: u.orgId,
      projectId: p.projectId,
      createdAt: now(),
      version: 1,
      snapshot: { name, mime, size: e.size, procurementId, type: kind },
    });
    audit(db, u, "上传私有证据", e.id);
    return { id: e.id, name: e.name, hash: e.hash, type: e.type };
  });
}
export function execute(
  u: AccessUser,
  action: Action,
  body: Record<string, unknown>,
  key: string,
) {
  if (!(action in actionPages)) throw new ApiError(404, "操作不存在");
  requireAccess(canAct(u, action));
  if (!/^[a-zA-Z0-9_-]{8,100}$/.test(key))
    throw new ApiError(400, "缺少有效幂等键");
  return mutate((db) => {
    const dedupe = `${u.id}:${action}:${key}`,
      bodyHash = sha(JSON.stringify(body));
    const prior = db.intents[dedupe];
    if (prior) {
      if (prior.bodyHash !== bodyHash)
        throw new ApiError(409, "幂等键已用于不同请求");
      return prior.result;
    }
    let result: Record<string, unknown> = { message: "已保存" };
    if (action === "reviewCase") {
      const review = db.reviews.find((r) => r.id === body.reviewId);
      if (!review) throw new ApiError(404, "审计记录不存在");
      if (review.status !== "pending")
        throw new ApiError(409, "此审计已处理，请刷新列表");
      const c = db.procurements.find((c) => c.id === review.procurementId)!;
      if (c.status === "cancelled" || c.status === "funds_released")
        throw new ApiError(409, "采购已取消或已拨款");
      if (c.hash !== review.sourceHash)
        throw new ApiError(409, "证明版本已更新，请审核最新版本");
      const decision = body.decision;
      if (!["approved", "rejected", "more_info"].includes(String(decision)))
        throw new ApiError(400, "请选择有效审计结论");
      if (
        decision === "approved" &&
        (review.risk >= 80 || c.status === "frozen")
      )
        throw new ApiError(
          409,
          "高风险冻结项目不能直接通过，请要求补件并重新审核",
        );
      if (
        review.stage === "delivery" &&
        (!c.foundationProof || !c.recipientProof)
      )
        throw new ApiError(409, "双方证明尚未齐全");
      review.reason = textValue(body.reason, "审核意见", 1000);
      review.status = "reviewed";
      review.decision = decision as ReviewCase["decision"];
      review.reviewerId = u.id;
      review.reviewedAt = now();
      if (review.stage === "purchase" && decision === "more_info")
        c.status = "needs_info";
      if (
        review.stage === "purchase" &&
        decision === "approved" &&
        c.status === "needs_info"
      )
        c.status = "human_review";
      result = {
        message: "人工审计结论已保存，不会自动拨款或付款",
        id: review.id,
      };
    } else if (action === "foundationAppeal" || action === "recipientAppeal") {
      const review = db.reviews.find((r) => r.id === body.reviewId);
      const project =
        review && db.projects.find((p) => p.id === review.projectId);
      requireAccess(
        !!project &&
          (u.role === "foundation"
            ? project.foundationId === u.orgId
            : project.recipientId === u.orgId),
      );
      if (
        !review ||
        review.status !== "reviewed" ||
        review.decision === "approved"
      )
        throw new ApiError(409, "只能对已处理且未通过的审计提出申诉");
      if (
        db.appeals.some(
          (a) => a.reviewId === review.id && a.status === "pending",
        )
      )
        throw new ApiError(409, "该审计已有未处理申诉");
      const ref = id("APL"),
        reason = textValue(body.reason, "申诉理由", 1500);
      const record = snapshot(db, {
        id: ref,
        kind: "appeal",
        title: "审计申诉",
        ownerId: u.id,
        orgId: u.orgId,
        projectId: review.projectId,
        snapshot: {
          reviewId: review.id,
          procurementId: review.procurementId,
          reason,
          sourceHash: review.sourceHash,
        },
      });
      db.appeals.unshift({
        id: ref,
        reviewId: review.id,
        procurementId: review.procurementId,
        projectId: review.projectId,
        ownerId: u.id,
        orgId: u.orgId,
        reason,
        status: "pending",
        createdAt: now(),
        hash: record.hash,
      });
      result = {
        message: "申诉已提交，等待管理员处理",
        id: ref,
        hash: record.hash,
      };
    } else if (action === "resolveAppeal") {
      const appeal = db.appeals.find((a) => a.id === body.appealId);
      if (!appeal) throw new ApiError(404, "申诉不存在");
      if (appeal.status !== "pending")
        throw new ApiError(409, "申诉已处理，请刷新列表");
      if (!["accepted", "rejected"].includes(String(body.resolution)))
        throw new ApiError(400, "请选择有效处理结果");
      const review = db.reviews.find((r) => r.id === appeal.reviewId)!;
      const c = db.procurements.find((c) => c.id === appeal.procurementId)!;
      if (body.resolution === "accepted") {
        if (
          latestReview(db, c.id, review.stage)?.id !== review.id ||
          c.hash !== review.sourceHash ||
          c.status === "paid"
        )
          throw new ApiError(
            409,
            "已有更新的审计或单据版本，请驳回此旧申诉并说明原因",
          );
        queueReview(db, c, review.stage);
      }
      appeal.response = textValue(body.response, "处理意见", 1500);
      appeal.status = "resolved";
      appeal.resolution = body.resolution as "accepted" | "rejected";
      appeal.reviewerId = u.id;
      appeal.resolvedAt = now();
      result = {
        message: "申诉处理已保存；受理申诉会重新进入人工审计，不自动通过或付款",
        id: appeal.id,
      };
    } else if (action === "createMaintainer") {
      const username = textValue(body.username, "账号", 40),
        name = textValue(body.name, "姓名", 60),
        password = textValue(body.password, "密码", 120);
      if (!/^[a-zA-Z0-9_-]{3,40}$/.test(username) || password.length < 10)
        throw new ApiError(400, "账号需 3–40 位字母/数字，密码至少 10 位");
      if (db.users.some((x) => x.username === username))
        throw new ApiError(409, "账号已经存在");
      const user = {
        id: id("STAFF"),
        username,
        name,
        passwordHash: passwordHash(password),
        role: "maintainer" as const,
        orgId: "platform",
        grants: [],
        active: true,
      };
      db.users.push(user);
      audit(db, u, "创建维护人员", user.id);
      result = {
        message: "维护人员已创建，默认没有页面权限；请继续分配页面",
        user: publicUser(user),
      };
    } else if (action === "updatePermissions") {
      const target = db.users.find((x) => x.id === body.userId);
      if (!target) throw new ApiError(404, "账户不存在");
      if (target.role !== "maintainer")
        throw new ApiError(
          400,
          "固定角色的工作区权限不可随意更改；此处仅配置维护人员",
        );
      if (
        !Array.isArray(body.grants) ||
        body.grants.some(
          (x) => typeof x !== "string" || !pages.some((p) => p.id === x),
        )
      )
        throw new ApiError(400, "页面授权列表不合法");
      target.grants = [...new Set(body.grants as string[])];
      if (typeof body.active === "boolean") target.active = body.active;
      audit(db, u, "更新维护人员页面授权", target.id);
      result = {
        message: "权限已更新；下一次页面/API 请求立即生效",
        user: publicUser(target),
      };
    } else if (action === "createProject") {
      const name = textValue(body.name, "项目名称"),
        description = textValue(body.description, "项目说明", 1000),
        target = money(body.target);
      const ref = id("P");
      const r = snapshot(db, {
        id: ref,
        kind: "rules",
        title: name + " · 规则 v1",
        orgId: u.orgId,
        projectId: ref,
        snapshot: {
          name,
          description,
          target,
          categories: ["教育物资", "设备"],
          version: 1,
        },
      });
      db.projects.push({
        id: ref,
        name,
        description,
        target,
        foundationId: u.orgId,
        recipientId: "org-recipient",
        deposited: 0,
        available: 0,
        reserved: 0,
        paid: 0,
        rulesHash: r.hash,
        categories: ["教育物资", "设备"],
      });
      trackProject(db, db.projects[db.projects.length - 1]);
      result = {
        message: "演示项目已创建，规则 Hash 已保存；未登记真实合约",
        id: ref,
        hash: r.hash,
      };
    } else if (action === "donate") {
      const p = db.projects.find((p) => p.id === body.projectId);
      if (!p) throw new ApiError(404, "项目不存在");
      if (p.paymentTracked) {
        result = donateTokens(db, u, p, money(body.amount));
      } else {
        const amount = money(body.amount),
          ref = id("DON");
        const r = snapshot(db, {
          id: ref,
          kind: "donation",
          title: p.name,
          ownerId: u.id,
          projectId: p.id,
          amount,
          unit: "HKD",
          snapshot: {
            amount,
            projectId: p.id,
            ownerId: u.id,
            mode: "simulation",
          },
        });
        db.donations.push({
          id: ref,
          ownerId: u.id,
          projectId: p.id,
          amount,
          allocated: 0,
          hash: r.hash,
          createdAt: r.createdAt,
        });
        result = {
          message: "模拟捐款已确认，未扣款；资金池注资另行处理",
          id: ref,
          hash: r.hash,
        };
      }
    } else if (action === "deposit") {
      const p = db.projects.find(
        (p) => p.id === body.projectId && p.foundationId === u.orgId,
      );
      if (!p) throw new ApiError(404, "未找到可管理项目");
      if (p.paymentTracked)
        throw new ApiError(409, "资金链项目需要由 Donor 使用模拟币捐款注资");
      const amount = money(body.amount);
      p.deposited += amount;
      p.available += amount;
      db.ledger.unshift({
        id: id("LED"),
        projectId: p.id,
        kind: "模拟注资",
        amount,
        reference: "DEMO",
        at: now(),
      });
      result = { message: "演示资金池已更新；没有执行真实链上交易" };
    } else if (
      action === "createProcurement" ||
      action === "foundationProcurement"
    ) {
      const p = db.projects.find(
        (p) =>
          p.id === body.projectId &&
          (action === "foundationProcurement"
            ? p.foundationId === u.orgId
            : p.recipientId === u.orgId),
      );
      if (!p) throw new ApiError(404, "未找到本机构项目");
      if (
        p.paymentTracked &&
        (db.payment!.projects[p.id].state !== "Active" ||
          db.payment!.projects[p.id].paused)
      )
        throw new ApiError(409, "项目已暂停或关闭，不能新增采购");
      const vendor = db.vendors.find(
        (v) => v.id === body.vendorId && v.verified,
      );
      if (!vendor) throw new ApiError(400, "需要选择已认证供应商");
      const name = textValue(body.name, "采购名称"),
        quantity = money(body.quantity),
        unitPrice = money(body.unitPrice),
        amount = money(quantity * unitPrice);
      if (quantity > 10000) throw new ApiError(400, "采购数量超出范围");
      const ref = id("PR");
      const r = snapshot(db, {
        id: ref,
        kind: "procurement",
        title: name,
        orgId: p.recipientId,
        projectId: p.id,
        amount,
        unit: "mHKD",
        snapshot: {
          name,
          quantity,
          unitPrice,
          amount,
          vendorId: vendor.id,
          projectId: p.id,
        },
      });
      db.procurements.push({
        id: ref,
        projectId: p.id,
        recipientId: p.recipientId,
        vendorId: vendor.id,
        name,
        quantity,
        unitPrice,
        amount,
        status: "human_review",
        risk: 18,
        hash: r.hash,
        createdAt: r.createdAt,
        note: "AI 结果为固定演示数据，真实服务未接入。",
        evidenceIds: [],
      });
      queueReview(db, db.procurements[db.procurements.length - 1], "purchase");
      result = {
        message: "采购已提交；演示 AI 初审已完成，等待人工审核",
        id: ref,
        hash: r.hash,
      };
    } else if (action === "reimbursement") {
      const name = textValue(body.name, "报销事由"),
        amount = money(body.amount);
      const ref = id("RE");
      db.reimbursements.push({
        id: ref,
        orgId: u.orgId,
        name,
        amount,
        status: "草稿 · 支付待接入",
        createdAt: now(),
      });
      const r = snapshot(db, {
        id: ref,
        kind: "reimbursement",
        title: name,
        orgId: u.orgId,
        amount,
        unit: "HKD",
        snapshot: { name, amount, mode: "draft" },
      });
      result = {
        message: "报销草稿已保存；尚未提交审核或执行付款",
        id: ref,
        hash: r.hash,
      };
    } else {
      const c = db.procurements.find((p) => p.id === body.procurementId);
      if (!c) throw new ApiError(404, "采购不存在");
      const p = db.projects.find((p) => p.id === c.projectId)!;
      requireAccess(
        action === "deliver" || action === "resubmitProcurement"
          ? p.recipientId === u.orgId
          : p.foundationId === u.orgId,
        "无法操作其他机构的采购",
      );
      if (
        !["resubmitProcurement", "resubmitFoundationProcurement"].includes(
          action,
        ) &&
        (c.status === "frozen" || c.risk >= 80)
      )
        throw new ApiError(409, "采购已冻结，不能预留或付款");
      if (action === "requestInfo") {
        const pending = latestReview(db, c.id, "purchase");
        if (pending?.status === "pending")
          Object.assign(pending, {
            status: "reviewed",
            decision: "more_info",
            reason: textValue(body.note, "补件原因", 500),
            reviewerId: u.id,
            reviewedAt: now(),
          });
        if (c.status !== "human_review")
          throw new ApiError(409, "仅待初审采购可要求补充");
        c.note = textValue(body.note, "补件原因", 500);
        c.status = "needs_info";
        result = { message: "补件要求已保存" };
      }
      if (
        action === "resubmitProcurement" ||
        action === "resubmitFoundationProcurement"
      ) {
        if (c.status !== "needs_info")
          throw new ApiError(409, "当前采购不在补件阶段");
        const note = textValue(body.note, "补充说明", 500);
        const r = snapshot(db, {
          id: c.id,
          kind: "procurement",
          title: c.name,
          orgId: c.recipientId,
          projectId: c.projectId,
          amount: c.amount,
          unit: "mHKD",
          snapshot: {
            name: c.name,
            quantity: c.quantity,
            unitPrice: c.unitPrice,
            amount: c.amount,
            vendorId: c.vendorId,
            evidenceIds: c.evidenceIds,
            note,
            previousHash: c.hash,
          },
        });
        c.hash = r.hash;
        c.note = note;
        c.status = "human_review";
        c.risk = 18; // Explicit fixed demo AI re-review, not a real AI result.
        queueReview(db, c, "purchase");
        result = {
          message: "补件新版本已保存，旧 Hash 保留；重新进入演示人工审核",
          hash: r.hash,
        };
      }
      if (action === "approvePurchase") {
        requireApprovedReview(db, c, "purchase");
        if (c.status !== "human_review")
          throw new ApiError(409, "采购已处理或状态已变化");
        if (p.paymentTracked) {
          reserveTokens(db, u, p, c);
        } else {
          if (p.available < c.amount) throw new ApiError(409, "可用预算不足");
          c.status = "reserved";
          p.available -= c.amount;
          p.reserved += c.amount;
          db.ledger.unshift({
            id: id("LED"),
            projectId: p.id,
            kind: "模拟预留",
            amount: c.amount,
            reference: c.id,
            at: now(),
          });
        }
        result = {
          message: "演示审批完成，预算已预留；未执行钱包签名或链上交易",
        };
      }
      if (action === "deliver" || action === "submitFoundationProof") {
        if (c.status !== "reserved")
          throw new ApiError(409, "当前阶段不能提交证明");
        const foundation = action === "submitFoundationProof";
        const es = db.evidence.filter(
          (e) => c.evidenceIds.includes(e.id) && e.orgId === u.orgId,
        );
        if (
          foundation
            ? !es.some(
                (e) => e.type === "invoice" && e.submittedRole === "foundation",
              )
            : !es.some(
                (e) => e.type === "photo" && e.submittedRole === "recipient",
              )
        )
          throw new ApiError(
            400,
            foundation ? "请先上传基金会采购发票" : "请先上传受捐机构收货照片",
          );
        const note = textValue(body.note, "证明说明", 1000);
        const submission = {
          fileIds: es.map((e) => e.id),
          note,
          submittedAt: now(),
          actorId: u.id,
        };
        if (foundation) c.foundationProof = submission;
        else {
          const quantity = money(body.quantity);
          if (quantity !== c.quantity)
            throw new ApiError(
              400,
              "验收数量与采购数量不一致；当前演示不支持部分交付",
            );
          c.recipientProof = { ...submission, quantity };
        }
        const record = snapshot(db, {
          id: c.id,
          kind: "procurement",
          title: c.name,
          orgId: c.recipientId,
          projectId: c.projectId,
          amount: c.amount,
          unit: "mHKD",
          snapshot: {
            name: c.name,
            quantity: c.quantity,
            amount: c.amount,
            vendorId: c.vendorId,
            foundationProof: c.foundationProof,
            recipientProof: c.recipientProof,
            previousHash: c.hash,
          },
        });
        c.hash = record.hash;
        if (c.foundationProof && c.recipientProof) {
          c.status = "payment_review";
          c.finalRisk = 12;
          queueReview(db, c, "delivery");
        }
        result = {
          message:
            c.status === "payment_review"
              ? "双方证明已齐全，演示 AI 审核完成，等待管理员人工审计"
              : "证明已提交，等待另一方补齐材料",
          hash: c.hash,
        };
      }
      if (action === "approvePayment") {
        requireApprovedReview(db, c, "delivery");
        if (
          c.status !== "payment_review" ||
          c.finalRisk === undefined ||
          c.finalRisk >= 80
        )
          throw new ApiError(409, "最终验收或 AI 终审条件未满足");
        if (!db.vendors.some((v) => v.id === c.vendorId && v.verified))
          throw new ApiError(409, "供应商未通过认证");
        if (p.reserved < c.amount) throw new ApiError(409, "预留不足");
        if (p.paymentTracked) {
          releaseTokens(db, u, p, c);
          result = {
            message: "模拟币已从项目池拨付 Foundation；可在资金链中兑付 HKD",
          };
        } else {
          c.status = "paid";
          p.reserved -= c.amount;
          p.paid += c.amount;
          db.ledger.unshift({
            id: id("LED"),
            projectId: p.id,
            kind: "模拟供应商付款",
            amount: c.amount,
            reference: c.id,
            at: now(),
          });
          result = {
            message:
              "演示供应商付款完成；无真实转账。新支出分配等待 Allocation 服务接入",
          };
        }
      }
    }
    for (const p of db.projects)
      if (
        !p.paymentTracked &&
        (p.deposited !== p.available + p.reserved + p.paid ||
          p.available < 0 ||
          p.reserved < 0)
      )
        throw new ApiError(409, "资金状态不一致，操作已取消");
    assertPaymentBook(db);
    if (action !== "updatePermissions")
      audit(
        db,
        u,
        action,
        String(result.id || body.procurementId || body.projectId || ""),
      );
    db.intents[dedupe] = { bodyHash, result };
    return result;
  });
}

function latestReview(
  db: Database,
  procurementId: string,
  stage: ReviewCase["stage"],
) {
  return db.reviews
    .filter((r) => r.procurementId === procurementId && r.stage === stage)
    .at(-1);
}
function queueReview(db: Database, c: Procurement, stage: ReviewCase["stage"]) {
  db.reviews.push({
    id: id("AUD"),
    procurementId: c.id,
    projectId: c.projectId,
    stage,
    status: "pending",
    sourceHash: c.hash,
    evidenceIds: [...c.evidenceIds],
    risk: stage === "purchase" ? c.risk : (c.finalRisk ?? 100),
    createdAt: now(),
  });
}
function requireApprovedReview(
  db: Database,
  c: Procurement,
  stage: ReviewCase["stage"],
) {
  const r = latestReview(db, c.id, stage);
  if (
    !r ||
    r.status !== "reviewed" ||
    r.decision !== "approved" ||
    r.sourceHash !== c.hash
  )
    throw new ApiError(409, "请先完成当前版本的管理员人工审计");
}
