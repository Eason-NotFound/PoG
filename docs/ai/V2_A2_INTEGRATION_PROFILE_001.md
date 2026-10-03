# V2 / A2 首次接入补充约束 001

状态：**APPROVED_INTERFACE_ALIGNMENT_PROFILE**。本稿固定 R2 与正式 A2 的首次接入能力边界、可信输入、页绑定及签名协调映射。接口格式冻结有效；报告服务接入实现仍待完成，业务评分政策为 **NOT_APPROVED**，本稿的运行验证为 **NOT_RUN**。

本稿只明确首次接入能力与实现映射，不修改 R2 的字段、状态 enum、错误闭集、OpenAPI、canonical bytes 或 V2 ABI。本稿的映射约束与原 R2 冻结记录分别保留身份；接口批准不表示正式 A2 已实现 R2 报告服务。

## 1. 精确基线与生命周期

| 对象 | 身份 |
| --- | --- |
| R2 技术源 | `19657f4459186d06b0608d27362e1522db90ea46` |
| 公共冻结记录 | `567629ecfcb7fa50c46123d3235806390bbb047b`，parent 为 R2；仅新增冻结主记录与清单 |
| 冻结 profile / wire | `PoG-CJSON-0.2`；原 `pog.ai.*/0.2-candidate` 与 URN 保留 |
| A1 正式依赖 | `api-v0.1.0-a1` → `1844186df10854cd49ccc0886f5622e71e572d8e` |
| M2 合约/ABI | `blockchain-v0.2.0-m2` → `61aa673653dd31188d2627d76cbba3f97fed6137` |
| M3.1 部署规范 | `blockchain-v0.3.1-m3.1` → `977ea6223f2ce8a9e0f0159c42c289bab4420656` |
| A2 正式接口基线 | `api-v0.2.0-a2` → `4c9f1a40ac225d684d00b5abcf8081c43594bfcc` |
| A2 发布源 | `7d685af2ff80c608ac54b33f1e46a0f0a0cd6094`；下表引用的 API 接口与 migration 文件在正式基线中相同 |
| A2 migration head | `c31003a20004`；真实报告存储与共享 nonce 协调必须使用新的 successor migration，保留已发布 003/004 |

以 [公共冻结记录](V2_REPORT_FREEZE_RECORD.md) 和 [冻结清单](V2_REPORT_FREEZE_MANIFEST.json) 判断当前接口生命周期。原材料中的 CHANGE_REQUIRED、apiFrozen=false 属于历史状态；当前格式冻结由公共记录确定。接口格式、接入映射、业务政策及运行验收分别记录。

## 2. 首次签名路径的能力范围

首次 A2 HTTP 接入能力为 **65-byte EOA**。API、AI signing component 与 relayer 必须核对所用 signer 适用于该路径；不能仅由签名长度推断 signer 类型。其他 R2 合法签名类型在此路径暂不支持，不表示其 R2 格式非法。

合约钱包/ERC-1271、该路径不支持的签名长度或 signer 验证方式必须在调用 EOA recovery 前拒绝并留审计记录；不得将 ERC-1271 signer 当 EOA 恢复，不得回退或截断签名。扩展 ERC-1271 接入能力需另列实现与验证范围。原 R2 SigningEnvelope 的通用 bytes 格式保持不变，不能把全局 Schema 改成强制 65 bytes。

API 的 AIAssessment 仍沿用十字段、`primaryType=AIAssessment`、`name=PoGRegistryV2`、`version=2` 与实际 chainId/Registry。assessmentId 用 V2 的 abi.encode 公式，reportHash 对独立规范 ReportBody bytes 取 Ethereum Keccak-256；helper digest 本身不是签名或交易成功证明。

## 3. 可信输入入口

API 接入入口按以下步骤实施，输入完整对象与可信资源校验结果分开判断：

1. 验证 service principal 与 deployment/project/procurement 的授权范围；严格解析请求并执行原 R2 token、Schema、上界与排序检查。调用者自填 owner/verified 不能建立可信事实。
2. 解析 API 保存的不可变 snapshotId/evidenceVersion、DocumentVersion、外部 snapshot 与登记承诺。由这些可信记录及可核验的链上快照重建完整预期输入；若接收完整输入对象，逐字段、类型、数组次序及 nullable 严格比对。不得用请求值更新可信记录后再声称比对通过。
3. 获取授权的原文件 byte stream，复核长度、MIME、原文件 SHA-256/Keccak、版本与可信允许页码；复核外部快照的原 bytes/双摘要。读取失败与资料尚未提供按原错误/业务规则分别表达。
4. 核对 stage、projectId、procurementId、deployment、evidenceVersion、各 evidenceRole/commitment、accepted receiptDigest、canonical block 及当前阶段事实。来源不可核验、过时或绑定矛盾时，按原 R2 错误协议拒绝处理/签署，不能仅修改报告字段掩盖矛盾。
5. 保存原不可变输入、完整报告对象、精确规范 bytes、inputHash/reportHash/payloadHash、operation/aiRequest 与每次结果/错误的审计关联。签名器独立复算，临签/临提交重新核对实际链条件；合法 Schema 不能替代这些检查。

首次接入明确采用 **原文件双哈希 + 可信页码集合** 的页绑定。图片 pages=null；可信 PDF pages 为非空升序 1-based 集合，正文只可引用其子集。R2 没有独立 pageHash：不能把重渲染页图字节、提取文本或其它内容的摘要冒充原文件摘要。独立页内容哈希若成为新需求，须另建字段/recipe/向量的版本提案，本稿不增加它。

## 4. 共享 nonce 的持久协调

未决锁范围保持原 R2：`nonceScope.deployment={runId,instanceId,chainId,registry}` + verifyingContract=Registry + nonceFamily=aiNonces + signer。API 部署目录先归并指向同一个物理链/Registry 状态的别名；采购、stage、ai_pre/ai_final 或 nonce 数值都不能拆出第二把未决锁。

持久索引/等价跨进程原子协议保证该范围同一时间最多一个未解决请求，不能只对 `(scope, nonce, kind)` 做唯一性。必须先取得耐久锁，再读实际 aiNonces，原子保存完整 SigningRequest、deadline、typed data、digest 及原 input/BODY 审计引用；持久化失败不调用签名器。Relayer EVM transaction nonce 独立协调，不推进 EIP-712 nonce。

R2 的六状态不变；首次接入采用以下内部活动到 R2 的映射：

| 内部活动/事实 | R2 SigningRequest 状态 | 未决锁与处理 |
| --- | --- | --- |
| 已准备，尚未调用/获得签名 | prepared | 持有锁；signature=null、txHash=null，完整确定请求已提交持久化 |
| signing 调用正在执行 | prepared | 原请求保持不变，签名调用活动单独审计；不新增 signing enum |
| 获得并验证确定签名 | signed | 原子保存签名与验证证明，保持原锁 |
| 签名调用超时/返回结果不明 | requires_attention | 保持原锁与请求，查询原调用，不再次盲签 |
| 已发送交易，结果未核实 | submitted | 保存发送记录/txHash；保持原锁 |
| 交易超时、断连、重组或事实矛盾 | requires_attention | 保持原锁，调查原 tx/receipt/事件/current assessment/aiNonces/canonical block |
| 链录入已按部署确认政策核实 | recorded | 核对成功 receipt、AIAssessmentRecorded、当前评估引用、canonical block 与 aiNonces 消费；留证后释放未决锁，签名后来过期不改变已接受事实 |
| 已证明旧签名不可能再被该实例接受 | invalidated | 留存失效证明与原因后释放；不能仅由本地取消/timeout 推断 |

若实现采用数据库耐久 lease，lease 只协调工作进程；lease/activity 存入内部协调及审计记录，不增加冻结 SigningRequest 字段。lease 到期、进程崩溃或重启均不能释放 aiNonces 未决锁、分发第二份 typed data 或盲目再次调用 signer。恢复者读取原 signingRequestId/digest；迟到结果只能绑定原请求，无法确定来源时保锁调查。

expired 是内部期限观察，不新增 R2 enum，也不自动等同现有 A2 的 expired。先查清原请求是否已接受：已接受的按 recorded 核对；未知提交或接受结果保持 requires_attention。对尚未接受的请求，只有在已固定的部署确认政策下核实 canonical 链上 `block.timestamp > deadline`，查清历史提交/接受事实并保留能证明旧签名不再可接受的证据后，才可按 invalidated 处理。等于 deadline 仍有效；确认政策未批准、仅本地时钟过期、暂时区块过期或 nonce 值变化均不足以自动回收/重签。

后续确定需要新请求时，先按上述规则解决旧未决请求，再重新读实际 nonce，保存新 requestId/deadline/typed data/digest 与原记录关联。不得覆盖旧记录、本地 nonce+1 或把 failed transaction 当作签名失效。正式 A2 的唯一索引包含 `(namespace_id, contract_address, signer_wallet, nonce_text, kind)`，advisory lock 也按 kind 切分；加入 ai_final 后不能将 ai_pre/ai_final 分成两把 aiNonces 锁。A2 当前 migration head 为 `c31003a20004`；003/004 保持原样，真实报告存储与上述共享锁域须通过新的 successor migration 实现。

## 5. 评分与资金门禁

| 条件 | 签名资格 |
| --- | --- |
| incomplete 或 riskScoreBps=null | blocked，不能补 0 或样例分数 |
| model failure/timeout、hash 不符、未知执行模式 | 不产生可签报告；禁止静默降级 rules_only |
| synthetic_fixture | 生产永久 blocked；协议测试限定独立 fixture/harness scope |
| 业务评分/执行政策未批准 | 不进入生产 prepared/signed；生产评分调用按原协议返回 SCORING_POLICY_UNCONFIRMED，诊断未评分报告保持 blocked |
| complete 且已评分的 Pass/Review/Reject | 仅在全部可信、已批准版本化政策及实际链门禁满足时有风险签名资格；不代表人工资金批准 |

当前业务评分政策仍未批准，本稿没有风险公式/权重，也不把样例分数批准为真实业务评分。AI 模型不持钥；AI 组的独立 signing component 控制 AI 密钥并复算全部门禁；API 提供可信输入、构造/持久协调与 relayer；relayer 不持 AI/Human/Recipient 密钥。

MockHKD 的三个结果分别记录：Escrow 向 Foundation 的 token release、模拟 Vendor 付款证据登记、人类 reconciliation/settlement confirmation。后两项不证明真实 HKD 银行到账。评估签名及 token release 不能代替人类资金审批或供应商模拟付款核账。

## 6. 接口名称、类型与接入能力

### 6.1 正式 A2 已有 HTTP 接口

以下接口以 `4c9f1a40ac225d684d00b5abcf8081c43594bfcc` 为准。成功 POST 包括终态幂等重放均返回 202；失败保留实际错误 HTTP 状态。

| 名称 | 方法与路径 | 输入 → 成功输出 | 状态与作用 |
| --- | --- | --- | --- |
| `create_signing_request` | POST `/v2/procurements/{procurement_id}/signing-requests` | `SigningRequestCreate` → 内联 `{operation, signingRequest}` | 202；准备并持久化固定签名材料 |
| `get_signing_request` | GET `/v2/signing-requests/{request_id}` | request UUID → 内联 signing-request 对象 | 200；指定 signer 读取材料 |
| `sign_demo` | POST `/v2/signing-requests/{request_id}/sign-demo` | `DemoSignRequest` → 内联 `{operation, signingRequest}` | 202；仅启用本地 demo 且 confirm=true |
| `submit_signature` | POST `/v2/signing-requests/{request_id}/submit` | `SignatureSubmit` → 内联 `{operation, signingRequest}` | 202；验证 65-byte EOA 签名并排队提交 |
| `get_operation` | GET `/v2/operations/{operation_id}` | operation UUID → `OperationResponse` | 200；查询操作、步骤和链确认事实 |

`SigningRequestCreate.kind` 仅有 ai_pre/reserve/receipt；可选 reserveAmountAtomic、receiptEvidenceDocumentVersionId，deadlineTtlSeconds 为 60–3600。`SignatureSubmit.signature` 为 0x + 130 位十六进制，执行 low-s 与 v=27/28 检查。

A2 没有 `SigningRequestResponse` 类。其内联 signing-request 含 id/procurementId/kind/status/signer/nonce/deadline/policyEpoch/digest/typedData/synthetic，不返回 signature。该对象不等于 R2 `SigningRequest` 或 `SigningEnvelope`。A2 的 mutation operation 为内联 legacy 对象；`OperationResponse` 与 `{error:{code,message,operationId,details}}` 分别是 legacy 查询/错误容器，不等于 R2 `OperationEnvelope`/`ErrorEnvelope`。

现有 ai_pre 仅为 local-only synthetic fixture：stage=0、outcome=Pass、riskScoreBps=100，reportHash 为 Ethereum Keccak-256(`"POG_A2_SYNTHETIC_PRE_FIXTURE_V1:" + procurement.business_id`)；没有规范 ReportBody。正式 A2 没有真实 AI 报告 HTTP endpoint、FinalRelease 报告路径或独立 AI signing component 对接。legacy 幂等使用 namespace + principal + operation kind + key 及其 payload SHA-256，不能据此宣称已实现 R2 的快照字节、作用域或保存期限协议。

### 6.2 冻结 R2 报告与签名接口

| 名称 | 类型/路径 | 输入输出与作用 | 正式 A2 支持 |
| --- | --- | --- | --- |
| `PrePurchaseInput` | JSON 输入，stage=0 | 完整可信采购前输入；POST `/v2/ai/assessments` 的 oneOf 分支 | 待实现 |
| `FinalReleaseInput` | JSON 输入，stage=1 | 完整可信最终审核输入；同一 POST 的另一 oneOf 分支 | 待实现 |
| `OperationEnvelope` | JSON 传输对象 | POST `/v2/ai/assessments` 成功始终 202；GET `/v2/ai/operations/{operationId}` 成功 200；携带稳定 operationId/aiRequestId/payloadHash、状态及 report/error | 待实现 |
| `ReportBody` | JSON 报告正文 | completed 的 report；包含结果、完整性、nullable 分数、inputHash、证据、版本、原因和待补资料；规范正文进入 reportHash | 待实现 |
| `ErrorEnvelope` | JSON 错误对象 | 接受前按原错误 HTTP 表返回；接受后在 GET 200 的 error 状态内返回；不能补成成功报告 | 待实现 |
| `SigningRequest` | 内部持久 JSON 对象 | 固定 nonce/deadline/typed data/digest/报告关联及六状态协调；R2 未定义独立 HTTP endpoint | 待实现 |
| `SigningEnvelope` | 独立 JSON 签名包 | blocked/prepared/signed 投影，保存域、评估结构、签名或阻止理由；不进入 reportHash；R2 未定义独立 HTTP endpoint | 待实现 |

R2 采用 Bearer service principal，API 生成稳定 operationId 和 aiRequestId，查询再次验证资源授权。POST 接受及恢复（包括已 completed/error 的同键同输入重放）均为 202；GET 各已接受状态均为 200。queued/running 的 report/error 均为 null，completed 只有 report，error 只有 ErrorEnvelope。HTTP 接受或报告完成不代表签名完成或上链。

R2 inputBytes 为 PoG-CJSON-0.2 的完整可信输入 bytes：payloadHash 使用 SHA-256，ReportBody.inputHash 使用 Ethereum Keccak-256；reportHash 为规范 ReportBody bytes 的 Ethereum Keccak-256。Idempotency-Key 在 service principal + POST + route + deployment 的范围内固定 inputBytes 与原 operation；同键不同输入返回 409。timeout 后使用同键同输入/原 operation，至少保留到全部未决事项解决后 7 天，并保留 deployment 生命周期的 key tombstone。完整细节沿用原传输规范。

## 7. 运行组件职责与验收边界

| 组件 | 职责与能力边界 |
| --- | --- |
| AI 模型 | 生成报告与证据推理；不接触任何签名私钥 |
| API | 授权、可信快照、报告校验与存储、EIP-712 构造、operation/nonce/deadline 持久协调及交易提交协调 |
| 独立 AI signing component | AI 组控制 AI 密钥；独立复算正文、执行模式、政策与链门禁后签名；失败时 fail closed |
| Relayer | 只提交已有合法签名，独立管理 EVM transaction nonce；不持 AI/Human/Recipient 私钥 |
| Human Approver / Recipient | 分别承担资金审批/收货承诺；AI 风险签名不替代这些授权 |
| 付款核账 | 分别保存 token release、模拟供应商付款证据、人类结算确认 |

缺资料、未评分、模型失败、hash 不符、未知 execution mode 均禁止签名。接口与映射批准不批准样例评分、真实模型结果、数据库迁移运行或交易；真实服务、共享锁及存储实现完成后，须在准确集成版本上验证组件及链路事实。

依据：[R2 主稿](V2_REPORT_CANDIDATE.md)、[传输/可信来源](V2_REPORT_TRANSPORT_CANDIDATE.md)、[技术附录](V2_REPORT_TECHNICAL_APPENDIX.md)、[签名协调](V2_SIGNING_COORDINATION_CANDIDATE.md)、[SigningRequest](schemas/v2/signing-request.schema.json)、[SigningEnvelope](schemas/v2/signing-envelope.schema.json)。
