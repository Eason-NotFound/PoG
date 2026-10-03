# PoG V2 R2 接口澄清与实现边界

状态：**R2 接口格式已冻结；实际服务、签名与整链运行按各自提交验收。** 当前格式生命周期以 [冻结记录](V2_REPORT_FREEZE_RECORD.md) 和 [冻结清单](V2_REPORT_FREEZE_MANIFEST.json) 为准。`0.2-candidate` 保留为原 wire identifier；接口不再等待一次重复回签。

API A2 正式对照提交为 `4c9f1a40ac225d684d00b5abcf8081c43594bfcc`。A2 的本地链能力和 Recipient ReceiptConfirmed 范围，不证明 R2 报告 HTTP adapter、完整报告存储、AI signing component 或 FinalRelease/付款链路已经实现。004 的首次接入目标见 [V2/A2 接入补充规范](V2_A2_INTEGRATION_PROFILE_001.md)；该规范的组件审核、运行验证和业务评分政策分别记录。

本文是 R2 历史源的公开说明修订，字节已改变。原 162 文件身份与冻结清单仍绑定历史提交 `19657f4459186d06b0608d27362e1522db90ea46`；本文件的发布身份应由新增公共 release 清单绑定，不沿用原文件 SHA-256。

## 主要技术项

| 项目 | 固定要求与相对路径依据 | 实现／验收边界 |
| --- | --- | --- |
| 三版本历史基线 | [V2_REPORT_BASELINES.json](V2_REPORT_BASELINES.json)固定 API A1 1844186、M2 61aa673、M3.1 977ea62 的完整 SHA 与源文件身份；旧 A2 9189d8c 仍是历史候选，不改写为正式依赖 | A2 正式对照版本另列；不覆盖历史清单 |
| 可复核技术材料 | 附录、两阶段完整对象、原合成文件、canonical bytes/hex/hash、Python/TS 源码及指定基线身份全部有明确相对路径 | 材料可复算不等于服务实现或运行验收 |
| 两种 nonce 与 prepared | [签名协调](V2_SIGNING_COORDINATION_CANDIDATE.md)、[SigningRequest](schemas/v2/signing-request.schema.json)固定 aiNonces lock family、完整 namespace/Registry/signer；prepared 原子保存 nonce/deadline/typed data/digest，不代表已消耗链 nonce | R2 耐久协调、共享锁和恢复实现按精确提交专项验证 |
| 组件责任 | API 提供可信快照、构造、耐久协调与 relayer；AI 独立 signing component 持 AI 密钥并复核门禁；模型无私钥，relayer 无业务角色私钥 | 004 补充首次 EOA 路径及内部 signing/expiry 活动映射，组件实现另验 |
| HTTP 与传输 | [传输](V2_REPORT_TRANSPORT_CANDIDATE.md)、[OpenAPI](V2_REPORT_OPENAPI_CANDIDATE.json)：async 202/200 polling、Bearer 服务身份、API IDs、7 天幂等、同 operation 重试、120s/2 attempts、完整 completed/error | 冻结契约不证明 A2 已实现真实报告 endpoint 或存储 migration |
| canonical profile 与双语言 | 唯一 PoG-CJSON-0.2；[附录第6节](V2_REPORT_TECHNICAL_APPENDIX.md)固定 UTF-8/无 BOM/排序/转义/null/数字/重复 key；[向量](vectors/v2/README.md)由独立 Python 与真正 strict TypeScript 复算 | 格式冻结；各验证结果只适用于其实际源版本 |
| EvidenceSnapshot 映射 | 五个 A1 category 映射、accepted RecipientReceipt digest；区分三种 hash；图片 pages=null，可信 PDF DocumentVersion 为非空 1-based，BODY PDF [] 仅为空引用 | 文件、链和外部依赖的真实可信性属于运行门禁 |
| 纯规则风险签名 | “纯规则模式生成并签署 AI 风险报告的部署策略条件”；Pass=0/Review=1/Reject=2 可在完整已评分时记录风险，人工分别决策资金 | 业务评分/执行政策仍未批准；冻结接口不批准权重或资金动作 |

完整且已评分的 synthetic BODY 是协议字节正例，**不是可签生产正例**。真实模型可信输入、R2 实际签名、报告相关 Solidity helper、当前链 nonce/evidence 和完整链路必须有各自精确提交的验证证据；本材料不将它们宣称通过，也不以 API 原链测试代替。

## 技术项对照

| 编号 | 固定规范落点 | 实现／部署待验证项 |
| --- | --- | --- |
| R01 附录/示例/向量 | 附录、examples/v2、vectors/v2、相对路径和源身份 | 完整可信可签正例及实际运行证据 |
| R02 canonical/reportHash | PoG-CJSON-0.2、原始 token 检查、规范 bytes/hex/hash、Python/TS | 按固定源独立复算；不重复设置格式冻结闸门 |
| R03 HTTP/异步/认证/幂等 | 唯一 202+polling、服务身份、IDs、TTL、重试和 OpenAPI | 真实 adapter、服务认证及 operation 持久实现 |
| R04 BODY/Error tagged union | 7 份 Schema、Operation completed/error 互斥，SigningRequest 不入 BODY | 真实 HTTP 状态与完整结果/错误存储 |
| R05 可信 DocumentVersion | 原版本/MIME/大小/双 hash/图片 null/PDF 页列表，BODY 引用绑定 | 生产文件读取和可信来源验证 |
| R06 evidenceRole | category→role 和三种 scheme，receiptDigest 非文件 leaf | approved manifest recipe 与真实 accepted receipt 的业务链证据 |
| R07 aggregate evidenceVersion | namespace+采购+stage 单调版本/不可覆盖，依赖变化建立新版本 | 版本分配与不可变保存实现 |
| R08 完整输入 inputHash | 全输入 PoG-CJSON bytes 的 Keccak，依赖双 hash 绑定，无新增合约字段 | 审计存储、来源解析与完整重建比对 |
| R09 错误/重试/人工处理 | 错误闭集、retryable/HTTP、同操作重试、未决调查规则 | 超时/崩溃/reorg 和跨进程耐久恢复 |
| R10 executionMode/fallback/gates | synthetic 永禁生产、未知模式 fail closed、不静默降级、独立 signer 复核 | 部署/业务评分政策及实际组件门禁 |

004 的首次接入目标是：原文件双哈希加可信页码集合、不新增 pageHash；65-byte EOA HTTP 能力，不缩窄全局 SigningEnvelope；跨采购与阶段共用 aiNonces 未决锁；signing/expired 属内部活动，不新增 R2 六状态。未知签名或交易结果保留锁；只有已证明 recorded/invalidated 才释放。接入规范目标、格式冻结、实现验收和业务政策批准互不替代。
