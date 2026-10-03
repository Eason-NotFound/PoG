# PoG V2 AI 报告接口（R2 公开说明）

版本：**R2 接口格式已冻结；原 `0.2-candidate` 保留为 wire identifier。** 当前生命周期以 [冻结记录](V2_REPORT_FREEZE_RECORD.md) 和 [冻结清单](V2_REPORT_FREEZE_MANIFEST.json) 为准。固定 V2 合约接口和 ABI 沿用原版本；格式冻结不证明 AI/API 服务、实际签名或独立端到端运行验收。本文为公开说明修订，字节与 R2 历史源不同；新增公共 release 清单应绑定当前 Markdown 身份，原 162 文件与冻结清单仍按历史提交核对。

R2 冻结时的正式依赖基线分列如下；历史精确源身份见 [baseline manifest](V2_REPORT_BASELINES.json)，本说明不覆盖该历史清单：

- `api-v0.1.0-a1` → `1844186df10854cd49ccc0886f5622e71e572d8e`（api_a1）。
- `blockchain-v0.2.0-m2` → `61aa673653dd31188d2627d76cbba3f97fed6137`（contract_abi_m2）。
- `blockchain-v0.3.1-m3.1` → `977ea6223f2ce8a9e0f0159c42c289bab4420656`（local_deployment_m3_1）。

当前 API A2 正式对照提交为 `4c9f1a40ac225d684d00b5abcf8081c43594bfcc`；旧 `9189d8c` 仍按历史候选记录。A2 的 Recipient ReceiptConfirmed 本地链能力不等于 R2 报告协议实现。首次接入目标见 [004 补充规范](V2_A2_INTEGRATION_PROFILE_001.md)；其组件实现、运行验收和业务评分政策分别记录。R2 历史源为 `19657f4459186d06b0608d27362e1522db90ea46`，此前 0.2/0.1 技术版本关系不改写。

完整材料：[技术附录](V2_REPORT_TECHNICAL_APPENDIX.md)、[HTTP／错误／幂等](V2_REPORT_TRANSPORT_CANDIDATE.md)、[机器 Schema](schemas/v2/input.schema.json)、[字节与哈希向量](vectors/v2/README.md)。机器契约与正文语义以冻结源为准；旧说明的待回签/未冻结文字属于历史生命周期，本次公开说明按既有冻结记录解释。

## 业务前提

| 阶段 | 输入与允许状态 | 后续人工审批 |
| --- | --- | --- |
| PrePurchase，stage=0 | 已在 Registry 记录 PO、采购需求及货物需求承诺；预算、规格、报价、供应商与历史／关联交易／报价参照。仅 PORecorded / PreAssessed / ReserveApprovalPending | 单独批准采购预留；不要求尚未产生的 GRN、Invoice 或收货签名 |
| FinalRelease，stage=1 | PO、GRN／交付、Invoice、Registry 已接受的 Recipient 收货承诺，以及供应商与查重资料。仅 ReceiptConfirmed / FinalAssessed / ReleaseApprovalPending | 单独批准最终释放；此时不要求付款回单 |

AI 的 Pass、Review、Reject 都是风险证据，合约没有按结果或风险分数自动拒款的阈值。资金最终由人工分别审批。释放给固定 Foundation 不等于 Vendor 已收款。

## 接口目录与职责

| 名称 | 类型与输入 → 输出 | 作用／状态 |
| --- | --- | --- |
| `PrePurchaseInput` / `FinalReleaseInput` | JSON 输入；`stage` 选择阶段 | API 从可信不可变快照构造，不接受 AI 自行补写事实 |
| `assess(request)` | 逻辑审核调用；阶段输入 → ReportBody 或 ErrorEnvelope | 冻结逻辑契约；HTTP 外层定义见传输稿，真实实现另验 |
| `AIAdapter.assess(request)` | 现有 A1 占位；dict → None，默认抛 DependencyUnavailable | [A1 源文件身份](V2_REPORT_BASELINES.json)未实现候选业务结果 |
| `OperationEnvelope` | 传输 JSON；queued / running / completed / error | completed 仅 report 非空；error 仅 error 非空；待处理中二者均 null |
| `ReportBody` | JSON 正文 → 独立不可变对象 | 唯一进入 reportHash 的对象，不能对整个响应包取哈希 |
| `ErrorEnvelope` | 技术错误 JSON | 没有成功报告或签名包；模型失败不能变成 Pass 或 0 分 |
| `PoG-CJSON-0.2` | 校验对象 → 精确 UTF-8 bytes | 选定自定义 profile；核心编码沿用 0.1，本修订固定图片 null 语义；不宣称 JCS |
| `inputHash` | Ethereum Keccak-256(CJSON 完整输入对象 bytes) → bytes32 | 新增于正文；输入本身不含 inputHash，避免自引用 |
| `reportHash` | Ethereum Keccak-256(CJSON ReportBody bytes) → bytes32 | 与 SHA-256、Python sha3_256、文件 leaf hash、API 幂等 hash 分开 |
| `SigningEnvelope` | 独立 blocked / prepared / signed 数据包 | 签名适配器输出；不进入正文，也不代表链已接收 |

冻结契约采用异步 POST 202、GET 200 轮询与 Bearer service authentication；API 生成 operationId 与 aiRequestId，幂等保存七天、未决延长、超时复用同一操作。完整 [OpenAPI 3.1](V2_REPORT_OPENAPI_CANDIDATE.json) 定义 R2 报告服务目标；A1 未实现该端点，API A2 的 local-only synthetic ai_pre fixture 不等于真实 AI 报告服务。真实 HTTP adapter、完整报告/原 bytes/错误存储、版本化 migration 与服务联调仍是具体实现项，格式冻结不将其宣称完成。

AI 模型只生成报告、不持钥。API 组负责可信输入、校验、EIP-712 构造和 operation/nonce/deadline 管理；AI 组控制独立 signing component 的 AI 密钥并复核全部门禁；API 组 relayer 仅提交已有签名，不持 AI/Human/Recipient 私钥。职责与持久化协议见 [签名协调候选](V2_SIGNING_COORDINATION_CANDIDATE.md)。

## 输入字段

所有固定键必须存在；未知键拒绝。金额与 uint64/uint256 使用规范非负十进制 string，不能转浮点。hash 为 `0x` 加 64 位小写 hex，address 为 `0x` 加 40 位小写 hex。

| 字段 | 类型 | 用途 |
| --- | --- | --- |
| schemaVersion | 固定 string `pog.ai.input/0.2-candidate` | 输入版本 |
| stage | integer 0 / 1 | 阶段选择 |
| claimAlias | string / null | 链下关联，不替代链上采购 ID |
| projectPolicy | object | 周期、预算、类别、单价上限、6 decimals |
| vendorContext | object | 身份及账户／关联交易快照；缺少为 null |
| comparisonContext | object | 报价、跨项目查重及采购历史的不可变快照 ID |
| registrySnapshot | 阶段 object | 实际项目／采购／角色／状态／证据／区块记录 |
| documents | EvidenceRef[] | 文件引用；与可信 DocumentVersion 逐项匹配 |
| evidenceSnapshot | object，0.2 新增 | API 所有的不可覆盖证据集、部署上下文、文件版本、承诺映射及外部快照双哈希 |
| procurementData | object，仅 stage=0 | requestVersion、category、description、quantity、quoteAmountAtomic、poVersion |
| deliveryData | object，仅 stage=1 | PO/GRN/Invoice 版本与数量、inspectionVersion、receiptAccepted |

`evidenceVersion` 按部署实例＋Registry＋procurementId＋stage 分配，不能从某个文件的 version 推导。任何纳入输入的文件、依赖或链快照变化都建立新快照版本；原版本不可覆盖。政策／Vendor／查重数据也受 inputHash 绑定。快照 ID 必须能授权解析到永久保留的原始不可变 bytes，只有一个可变 ID 不足以审计。

可信 `DocumentVersion` 包含 documentId、documentVersionId、原始 version、category、contentType、sizeBytes、原文件 SHA-256 与 Keccak、页集合。可信 PDF 的 pages 必须为非空、升序且去重的 1-based 页列表；图片使用 `pages=null`、Locator.page=null。正文 PDF EvidenceRef 的 [] 仅表示没有引用特定页面，不能表示图片；不得混用 null 与 []。

`evidenceRole` 显式映射文件版本／版本集合到 poHash、requestHash、goodsRequestHash、invoiceHash、goodsHash。单文件可核对原文件 Keccak；集合必须引用可解析且已批准的 commitment recipe，不能按文件名／category 猜测。**receiptDigest 来自 Registry 已接受的 RecipientReceipt 摘要，不是 Receipt 文件的 leafKeccak256。** 具体结构与验证步骤见附录和 Schema。

## ReportBody 字段

版本为 `pog.ai.report/0.2-candidate`；原 15 个字段保持，新增 `inputHash`。嵌套结构详见[正文 Schema](schemas/v2/report-body.schema.json)。

| 字段 | 类型 | 用途 |
| --- | --- | --- |
| schemaVersion | 固定 string | 正文格式 |
| stage | integer 0 / 1 | 审核阶段 |
| projectId、procurementId | bytes32 string | 必须匹配快照与链上采购归属 |
| evidenceVersion | uint64 string | API 证据集版本 |
| evidenceHash | bytes32 string | 当前 Registry 聚合证据 |
| inputHash | bytes32 string | 完整可信输入的 Keccak 摘要，不加入链上 AssessmentInput 字段 |
| executionMode | synthetic_fixture / rules_only / model_and_rules | 执行来源，不虚报模型运行 |
| versions | object | 服务、规则、评分、抽取、模型与提示词的确切版本 |
| outcome | integer：Pass=0 / Review=1 / Reject=2 | 风险结论 |
| riskScoreBps | integer 0..10000 / null | null 未评分，禁止签名且不能补 0 |
| completeness | complete / incomplete | 资料完整性 |
| summary | string | 可复核的说明 |
| evidenceRefs | EvidenceRef[] | 可信版本、双哈希和页码 |
| findings | Finding[] | reasonCode、说明、证据定位 |
| missingInputs | MissingInput[] | 适用阶段与待补资料 |

可评审的业务资料缺失输出 `incomplete + Review + null`，列出 missingInputs，并阻止签名。已声明文件读取失败、哈希不符、链快照不可信、模型／规则失败属于技术错误。完整且已评分的 Review／Reject 可以在全部生产门禁满足后签署风险报告；其签名仍不能替代人工资金决策。

正文禁止 reportHash、assessmentId、domain、signer、nonce、deadline、signature；签名适配器复算正文，并校验 stage、procurementId、evidenceHash、outcome、riskScoreBps 与签名结构一致，projectId 与链采购一致。合约不读取正文，这些校验属于链下责任。

## 固定 V2 合约接口

| 方法 | 类型 | 输入 → Solidity 输出 |
| --- | --- | --- |
| submitAIAssessment | external nonpayable 交易 | AssessmentInput, bytes signature → bytes32 assessmentId |
| computePreEvidenceHash | public view | bytes32 procurementId → bytes32 |
| computeFinalEvidenceHash | public view | bytes32 procurementId → bytes32 |
| computeAssessmentId | public pure | AssessmentInput → bytes32 |
| assessmentDigest | external view | AssessmentInput → bytes32 digest，不产生签名 |
| aiNonces | view getter | address signer → uint256 |

`AssessmentInput` 是 Solidity 元组名称；**EIP-712 primaryType 必须为 AIAssessment**，精确类型如下：

```text
AIAssessment(uint8 stage,bytes32 procurementId,bytes32 assessmentId,uint8 outcome,uint16 riskScoreBps,bytes32 evidenceHash,bytes32 reportHash,address signer,uint256 nonce,uint64 deadline)
```

| 顺序 | 字段 | ABI / EIP-712 类型 | JSON |
| --- | --- | --- | --- |
| 1 | stage | uint8 | integer 0 / 1 |
| 2 | procurementId | bytes32 | 小写 hex string |
| 3 | assessmentId | bytes32 | 小写 hex string |
| 4 | outcome | uint8 | integer 0 / 1 / 2 |
| 5 | riskScoreBps | uint16 | integer 0..10000，不能 null |
| 6 | evidenceHash | bytes32 | 小写 hex string |
| 7 | reportHash | bytes32 | 小写 hex string |
| 8 | signer | address | 小写 hex string |
| 9 | nonce | uint256 | 十进制 string |
| 10 | deadline | uint64 | 秒时间十进制 string |

domain 固定 `name=PoGRegistryV2`、`version="2"`，绑定实际 chainId 与 Registry；assessmentId 按 `keccak256(abi.encode(...))`，不含 assessmentId 自身，禁止 encodePacked。完整公式与准确 revert 预期见附录。

EIP-712 signer nonce 取 PoGRegistryV2.aiNonces(signer)，跨采购／跨阶段共享；relayer 的 EVM transaction nonce 独立。锁范围固定为 deployment namespace＋verifyingContract＋nonceFamily=aiNonces＋signer。prepared 必须已经原子持久化 EIP-712 nonce、deadline、完整 typed data、digest 和唯一未解决操作记录；此时尚未消耗链上的 nonce。未知交易保留锁并查原 tx／回执／当前记录，不能盲重签或本地 nonce+1。成功更新评估使旧人工审批不能复用。

生产 `synthetic_fixture` 永远 blocked；模型失败不能 fallback 为可签的 rules_only。纯规则模式生成并签署 AI 风险报告的部署策略条件是：已批准、版本化的执行与评分政策；不能批准资金。离线／隔离协议 fixture 是独立测试 scope，不构成生产例外。R2 实际签署、报告相关 helper 与链提交使用各自精确提交的专项证据；接口格式冻结不证明这些运行步骤通过。

## 格式冻结与实现范围

R2 固定机器 Schema、传输、可信快照／inputHash／evidenceRole 绑定，以及可复算字节向量。合成示例和向量不证明真实模型评分或链上事实；它们的源对象、canonical bytes、长度、hash 和 wire identifiers 不因公开说明修订而变化。

[历史验证说明](V2_REPORT_VALIDATION.md)限定原 R2 的离线检查；[接口澄清](V2_REPORT_API_RESPONSE_R2.md)列出技术要求与实现边界。API A2 原本地链测试只证明其实际测试范围，不能替代 R2 报告相关的模型、签名、helper 或整链验证。

004 的首次接入目标保留原六状态与共享 aiNonces 锁，规定原文件双哈希加可信页码集合及首次 65-byte EOA HTTP 能力，不新增 pageHash 或改变全局签名 Schema。组件审核与实现验收不作为重新冻结原格式的闸门；未知签名/交易结果仍须保锁调查。

业务评分政策、rules_only 启用、approved commitment recipe、服务认证 scope 及部署确认政策仍由各自版本化部署记录约束。资料缺失、未评分、模型失败、hash 不符和未知 executionMode 继续 fail closed。PrePurchase 与 FinalRelease 的人工资金审批保持分别决策。
