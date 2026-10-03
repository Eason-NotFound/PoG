# PoG V2 AI/API 接口冻结记录

状态：**APPROVED_INTERFACE_FREEZE；接口格式已冻结**。AI 技术负责人于 2026-10-03 17:29:22（UTC+8）作出决定，固定 R2 源提交 19657f4459186d06b0608d27362e1522db90ea46。机器身份及逐文件摘要见 [冻结清单](V2_REPORT_FREEZE_MANIFEST.json)。

## 冻结范围与版本

本次冻结输入、报告、错误、操作与签名对象的结构，PoG-CJSON-0.2 编码及哈希规则，HTTP 传输、EvidenceSnapshot、职责和固定 V2 typed-data 对接约束。负责人已审核确认，无需 API 再次回签；不声称 API 已审核批准这个 R2 或已接收材料。

R2 中 candidate/CHANGE_REQUIRED、等待 API 回签及未获授权等描述保留为其历史交付状态。本记录与机器清单确定当前接口生命周期；它们不改写旧审核事实，也不改变原技术门禁。原 JSON schemaVersion 字符串、Schema $id 和 golden bytes 均保持不变；其中 0.2-candidate 后缀作为既有 wire identifier，不表示当前记录仍待接口冻结。

| 冻结对象 | 精确内容 |
| --- | --- |
| R2 原源文件 | 19657f4459186d06b0608d27362e1522db90ea46；162份 docs/ai 文件按路径、Git blob、byteLength及SHA-256绑定 |
| 正文规范 | [主稿](V2_REPORT_CANDIDATE.md)、[技术附录](V2_REPORT_TECHNICAL_APPENDIX.md)、[传输](V2_REPORT_TRANSPORT_CANDIDATE.md)、[签名协调](V2_SIGNING_COORDINATION_CANDIDATE.md) |
| 机器契约 | 七份 JSON Schema 和 [OpenAPI3.1](V2_REPORT_OPENAPI_CANDIDATE.json)，源与嵌入组件原样保留 |
| Wire identifiers | pog.ai.input/report/error/operation/signing-envelope/signing-request 的 /0.2-candidate 字符串及原URN不改名 |
| 固定验证材料 | [golden manifest](vectors/v2/manifest.json)、两阶段示例、源JSON、canonical UTF8/hex/hash、原依赖bytes和Python/TypeScript参考工具；不重生成 |
| API A1 | api-v0.1.0-a1 → 1844186df10854cd49ccc0886f5622e71e572d8e |
| Contract/ABI M2 | blockchain-v0.2.0-m2 → 61aa673653dd31188d2627d76cbba3f97fed6137 |
| Local deployment M3.1 | blockchain-v0.3.1-m3.1 → 977ea6223f2ce8a9e0f0159c42c289bab4420656 |

三项正式基线的11个指定原始源文件身份逐项固定。A2 9189d8c286af2314e136d1de843da5cb5be6d407 是未验收候选，仅有 local synthetic ai_pre，没有真实AI报告endpoint，不升级为正式依赖。接口冻结不把A1/A2占位代码提升为已实现服务。

## 固定技术约束

唯一编码 profile 为 PoG-CJSON-0.2：UTF-8、无BOM、无末尾换行；ASCII对象键递归排序，数组顺序保留且满足各自语义约束；严格拒绝重复键、非法Unicode、浮点/指数/-0/NaN/Infinity，并遵守整数宽度与limits。接收方不以排序、归一化或默认值“修复”原对象。

inputHash 为完整规范输入bytes的Ethereum Keccak-256；reportHash仅取独立ReportBody规范bytes的Ethereum Keccak-256；payloadHash为同一输入bytes的SHA-256。transport、签名包与认证信息不进入reportHash。evidenceHash是Registry按固定ABI计算的聚合证据；receiptDigest是已接受RecipientReceipt的EIP-712摘要，不以文件leaf替代。

图片pages=null；可信PDF pages为非空、升序的1-based列表，BODY PDF[]仅表示未选择特定页。purchase_order/request/goods_request/invoice/goods_evidence分别绑定poHash/requestHash/goodsRequestHash/invoiceHash/goodsHash。

HTTP固定异步POST202、GET200 polling与受控Bearer服务身份；API生成operationId和aiRequestId，adapter核对metadata回显。幂等、七天恢复保留、同operation重试、120秒/最多两次attempt和错误闭集均按R2。

固定十字段AIAssessment、V2合约与ABI不变。primaryType=AIAssessment，domain为PoGRegistryV2/version 2与实际chainId/Registry。assessmentId使用固定abi.encode公式，排除自身。

prepared前原子持久化EIP-712 aiNonces、deadline、完整typed data/digest和唯一未决锁。锁为完整deployment namespace+verifyingContract+nonceFamily aiNonces+signer；同物理链/Registry的namespace别名归并。relayer EVM transaction nonce独立；未知交易保留锁、调查原交易，不盲重签或本地nonce+1。

API负责可信输入、报告复核、typed data、耐久协调与relayer；AI组控制独立AI signing component及AI密钥。模型无私钥，relayer无AI/Human/Recipient私钥。资料不全、未评分、模型失败、hash不符或未知执行模式fail closed；完整已评分的Review/Reject可作为风险证据，人工分别决定资金动作。

## 审核证据与实现验收

| 负责人实际离线复核 | 结果 |
| --- | --- |
| Python vectors | 75 PASS / 0 FAIL / 0 SKIP |
| 严格编译并实际运行TypeScript vectors | 75 PASS / 0 FAIL / 0 SKIP；known-answer 2 PASS |
| Schema meta／bundle对象／定向shape／错误码 | 7／8／72／25，全部通过 |
| 包、Git、工作区和manifest源身份 | 162份docs、11份基线blob一致 |
| 静态接口阻碍 | 0；十字段/domain/ID/evidence/aiNonces及prepared职责无阻塞 |

决定时间UTC为 2026-10-03T09:29:22Z。决定记录SHA-256为 d4e2d25db1b7ab41ce86dc965673baeac799a5fd1ec753502e634741fe62eb94；对应负责人离线/静态复核记录SHA-256为 d56d34f09dd5a65f507fb4d58874c73b9e16b00290ebc20f06fce132189db90d。摘要用于证据身份核对，不表示密钥签名或链上确认。

**签名/helper和整链测试的NOT_RUN属于实现验收状态，不再作为接口格式冻结前置闸门。** 冻结格式不等于实现、部署或独立端到端验收通过：

| 实现／部署事项 | 当前记录 |
| --- | --- |
| 真实签名、Solidity helpers、隔离可签正例 | NOT_RUN |
| HTTP／adapter／存储／migration及整个本地链路 | NOT_RUN |
| prepared耐久并发、崩溃和reorg恢复 | NOT_RUN |
| 独立端到端验收 | NOT_RUN |
| 真实模型可信业务正例 | NOT_RUN |
| 部署/评分政策及生产部署 | NOT_APPROVED |

后续按固定提交分别验证实际domain/evidence/state/allowlist/nonce，签名与helpers，以及receipt/AIAssessmentRecorded/当前引用/nonce消费。PrePurchase与FinalRelease分别记录覆盖，FinalRelease必须已有accepted RecipientReceipt。链下缺资料/未评分门禁不能宣称由合约读取正文识别；协议harness不能冒充真实模型评分或生产批准。

具体评分政策、rules_only启用、approved manifest recipe、服务身份scope及链确认政策分别按部署记录确认。生产synthetic永禁签、模型失败不降级成可签rules_only、人工资金决策等原门禁继续适用。

本次仅新增冻结记录和机器清单，保留原R2、合同/ABI、wire IDs与goldens。后续影响bytes、字段或含义的修改另建版本与新向量，不覆盖已冻结源或移动旧标签。此记录不授权生产密钥、真实资金、共享链reset、GPU任务、自动push或main合并。
