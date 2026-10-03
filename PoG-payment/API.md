# Payment API v1

浏览器使用前端 `/api/payment` 前缀；独立 UI 使用 `4010/api`。表内相对路径两者一致。浏览器身份来自登录 Cookie，不能传 userId、role 或钱包。内部代理的 `X-Payment-Secret` 只允许服务端使用。

所有 `POST /operations/*` 需要 `Idempotency-Key`（8–100 位字母/数字/下划线/短横线）和 JSON。金额参数为十进制字符串，最多两位小数；例如 `"60.00"`。返回金额中 `hkdCents` 是整数分，`tokens/tokenAtomic/units` 是六位小数原子单位字符串。

| Method | Path | 身份 | JSON 请求体 |
| --- | --- | --- | --- |
| GET | `/state` | 已配置账号 | 无 |
| POST | `/operations/exchange` | Donor | `{ "amount": "60" }` |
| POST | `/operations/donate` | Donor | `{ "projectId": "P-001", "amount": "60" }` |
| POST | `/operations/cashout` | Donor | `{ "amount": "16.80" }` |
| POST | `/operations/claim` | 原 Donor | `{ "projectId": "P-001" }` |
| POST | `/operations/import-release` | Foundation，anvil | `{ "projectId": "P-001", "txHash": "0x…", "logIndex": 2 }` |
| POST | `/operations/redeem` | 所属 Foundation | `{ "releaseId": "返回的UUID", "amount": "72" }` |
| POST | `/operations/pause` | 所属 Foundation/admin | `{ "projectId": "P-001", "reason": "等待核账" }` |
| POST | `/operations/resume` | 所属 Foundation/admin | `{ "projectId": "P-001", "reason": "问题解决" }` |
| POST | `/operations/closing` | 所属 Foundation | `{ "projectId": "P-001", "reason": "结束项目" }` |
| POST | `/operations/return` | 所属 Foundation | `{ "releaseId": "返回的UUID", "amount": "12" }` |
| POST | `/operations/close` | admin | `{ "projectId": "P-001" }` |
| POST | `/operations/register-project` | admin，anvil | `{ "id": "P-001", "name": "公益项目", "chainProjectId": "0x…64位hex" }` |
| POST | `/operations/demo-release` | admin，demo | `{ "projectId": "P-001", "procurementId": "PO-72", "amount": "72" }` |
| POST | `/operations/demo-reconcile` | admin，demo | `{ "releaseId": "返回的UUID", "reference": "EXTERNAL-72" }` |
| POST | `/reconcile/{operationId}` | 本人/admin | `{}`；广播丢Hash时仅admin可填 `{ "txHash": "0x…" }` |

`demo-release` 模拟人工批准并给 Foundation 测试币账本入账；不是实际链上事件。`demo-reconcile` 仅登记 Foundation 外部处理后的模拟凭证，不执行付款或扣款。`close` 在 anvil 模式调用 `executeClose`，链上必须已有足够的有效人工批准。

返回示例：

```json
{
  "id": "operation-uuid",
  "kind": "exchange",
  "status": "completed",
  "userId": "donor",
  "cents": 6000,
  "units": "60000000",
  "steps": []
}
```

anvil 模式 `steps` 记录计划交易、nonce、txHash、状态和确认块；demo 模式 `steps=[]`。凭证存在 `GET /state` 的 `ledger` 中，`receiptHash` 为凭证内容的 SHA-256，不是链上交易 Hash。只有 `completed` 表示记账完成。

| HTTP/status | 客户端处理 |
| --- | --- |
| 200 + completed | 成功；再次同键提交仍返回原操作 |
| 200 + failed | 明确失败，未记账；展示 error，用户处理原因后显式新建操作 |
| 202 + pending | 交易已提交但未确认；查询同一操作 |
| 202 + needs_reconciliation | 结果不确定或回执不匹配；保留原键和冻结额，核对原交易 |
| 400/401/403 | 请求错误/未登录/无权限 |
| 409 | 状态冲突、重复业务事件、余额不足、不同参数复用幂等键 |
| 502/503 | RPC/代理异常；不能凭HTTP失败推断链上未执行 |

直接服务的 `/api/login`、`/api/logout` 仅供独立 UI；现有前端继续使用其 `/api/auth/login`，不透传 Payment 登录接口。`GET /health` 只表示服务存活，不表示本地链当前可用。
