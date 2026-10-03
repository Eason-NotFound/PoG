# 本地演示 API 与对接边界

所有接口位于同源 /api。真实 FastAPI 可保持返回协议并逐步替换本地适配层。现在没有直接连接 PostgreSQL 或真实区块链。

## 公共协议

- 成功返回 JSON；失败为 { error: string }，HTTP 400/401/403/404/409/413/415/429/500。
- 登录写入 HttpOnly Cookie；浏览器同源请求自动附带。
- POST 使用 Content-Type: application/json 和同源 Origin。
- /actions/* 需要 Idempotency-Key；同一用户＋动作＋键重复返回原结果，不重复执行；相同键不同内容为 409。
- 业务金额为正整数展示最小单位；演示中 HKD/mHKD 均以两位展示小数换算。真实 token decimals 对接时必须独立转换。

| 方法 / 地址 | Input | Output |
|---|---|---|
| POST /api/auth/login | username, password | user, redirect；设置 Cookie |
| POST /api/auth/logout | {} | ok；删除会话 |
| GET /api/session | Cookie | 服务端当前 user 与 grants |
| GET /api/page?id=pageId | 已注册 pageId | user, pageId, page-specific data, updatedAt |
| POST /api/actions/donate | projectId, amount | donationId、内容 Hash；不自动注资 |
| POST /api/actions/createProject | name, description, target | 项目 ID、rulesHash |
| POST /api/actions/deposit | projectId, amount | 模拟资金池更新 |
| POST /api/actions/createProcurement | projectId, vendorId, name, quantity, unitPrice | procurementId、内容 Hash |
| POST /api/actions/approvePurchase | procurementId | 当前版本管理员采购前审计通过后，模拟批准并预留 |
| POST /api/actions/requestInfo | procurementId, note | 保存补件要求 |
| POST /api/actions/resubmitProcurement | procurementId, note | 保留原单据，生成新版本 Hash，重新进入初审 |
| POST /api/evidence | procurementId, type, name, content(Base64) | 文件 ID、fileHash；基金会 type=invoice/dispatch（采购补件阶段也可 quotation）；受捐机构 type=photo/grn/quotation |
| POST /api/actions/deliver | procurementId, quantity, note | 需受捐机构已上传收货照片；双方提交齐全后进入 AI 演示与人工审计 |
| POST /api/actions/approvePayment | procurementId | 当前版本交付审计通过后模拟采购付款，不能重复 |
| POST /api/actions/reimbursement | name, amount | 报销草稿 ID 和 Hash |
| POST /api/actions/createMaintainer | name, username, password | 维护人员，默认无 grants |
| POST /api/actions/updatePermissions | userId, grants:string[], active | 更新后的公开 user |
| GET /api/records/by-hash?page=portal.hash&hash=… | 内容/文件 Hash | 授权记录列表 |
| GET /api/records/:id/export | 记录 ID | JSON 凭证文件；有权限才可下载 |
| GET /api/evidence/:id | 附件 ID | 附件下载；有权限才可下载 |

## 接入真实团队模块前

1. 把 store 替换为 PostgreSQL repository；以数据库事务、唯一约束和 Outbox 实现并发一致性。
2. 把模拟 AI 状态替换为任务 ID → 真实报告 → Oracle 签名校验；前后两阶段不混用。
3. 把 approvePurchase/approvePayment 的模拟变更拆为准备 EIP-712 意图、钱包签名、API 验签、Relayer、事件确认。
4. UI 在签名后显示“处理中”；现行 V2 的 BudgetReserved 事件表示预算已预留，FundsReleasedToFoundation 事件表示资金已释放给 Foundation，MockPaymentConfirmed 事件才表示后续模拟供应商结算已确认。这三种状态分别处理，Registry 的 FundsReleased 状态不表示 Vendor 已收款；不再使用旧 PaymentExecuted 草案作为 V2 事件。
5. 文件 Base64 本地存储替换为私有对象存储预签名上传，保留内容 Hash、版本、大小、MIME、所有权和 ACL；补充正式文件扫描。
6. 跨端事件通过轮询/SSE 读已确认的数据库状态；不要依赖浏览器内模拟状态。
7. Allocation 以真实付款＋资金映射为依据计算，再锚定 root；不使用当前种子分配冒充真实 proof。
8. 保留当前权限矩阵与每次请求重新授权，不能仅由前端过滤菜单。

当前 V2 ABI 和签名字段已在 packages/contract-abis/v2 及 docs/M2_V2_INTERFACE_IMPLEMENTED.md 发布。接线前仍需核对实际 deployment JSON、chainId、Registry/实例、roles、token decimals、nonce/有效期及证据来源；AI 报告字节规范待各负责人确认。design 文档属于历史 UI 拟案，不能覆盖现行 V2。具体边界见 PORTAL_V2_BOUNDARY.md。


## 人工审计与双方证明 API

界面称「凭证编号 / Record ID」，JSON `hash` 字段保持不变。页面 DTO 的 `evidence` 只包含元数据，不含 Base64 文件内容。

| 方法 / 地址 | Input | Output / 前提 |
|---|---|---|
| POST /api/actions/foundationProcurement | projectId, vendorId, name, quantity, unitPrice | 本基金会新采购 id、hash；进入待采购前人工审计 |
| POST /api/actions/submitFoundationProof | procurementId, note | 必须有本基金会上传的 invoice；返回当前单据 hash |
| POST /api/actions/reviewCase | reviewId, decision: approved/rejected/more_info, reason | 管理员保存结论，保留版本、AI 分数、附件列表、审核人和时间；不变更资金 |
| POST /api/actions/foundationAppeal | reviewId, reason | 本机构申诉 id、hash |
| POST /api/actions/recipientAppeal | reviewId, reason | 本机构申诉 id、hash |
| POST /api/actions/resolveAppeal | appealId, resolution: accepted/rejected, response | 管理员完成申诉；accepted 创建待人工审计记录 |

证明提交会生成不可变单据新版本；基金会发票和受捐机构图片均齐全才生成交付审计。修改版本后不能沿用旧审计批准。历史版本及文件原字节保持不变。高风险冻结记录不能直接批准，可要求补件后进行新的演示 AI 审核与人工审计。

`POST /api/actions/resubmitFoundationProcurement`：基金会提交 `procurementId, note`，仅在本项目采购需要补件时允许；保留旧版，生成新凭证编号并重新进入人工审计。
