# PoG V2 R2 公共技术材料导航

状态：**R2 接口格式已冻结。** 当前生命周期及固定源身份见 [冻结记录](V2_REPORT_FREEZE_RECORD.md) 与 [冻结清单](V2_REPORT_FREEZE_MANIFEST.json)。公开技术材料包含字段规范、合成样例、机器契约与可复算参考；格式冻结不代表模型、签名服务或整链运行验收。

API A2 正式对照提交为 `4c9f1a40ac225d684d00b5abcf8081c43594bfcc`。其 Recipient ReceiptConfirmed 本地链范围不等于 R2 报告服务或 FinalRelease/付款链路。首次接入目标及 EOA/可信页码/共享 nonce 约束见 [004 接入补充规范](V2_A2_INTEGRATION_PROFILE_001.md)。

本文为公开说明修订，字节与原 R2 文件不同。原 162 文件与冻结清单保留为历史源身份；新增公共 release 清单应单独绑定修订后的 Markdown，不声明其与历史 SHA-256 相同。

## 阅读顺序与路径

| 位置 | 作用 |
| --- | --- |
| [主稿](V2_REPORT_CANDIDATE.md) | 两阶段输入/输出、字段类型、风险与人工资金边界 |
| [技术附录](V2_REPORT_TECHNICAL_APPENDIX.md) | PoG-CJSON-0.2、精确字节、hash 与固定 V2 对接规则 |
| [传输](V2_REPORT_TRANSPORT_CANDIDATE.md)、[OpenAPI](V2_REPORT_OPENAPI_CANDIDATE.json) | HTTP/认证/异步/幂等/完整结果与错误契约 |
| [签名协调](V2_SIGNING_COORDINATION_CANDIDATE.md) | 组件职责、prepared 持久材料、nonce 锁与恢复规则 |
| [Schema](schemas/v2/input.schema.json) 及 schemas/v2/ | 七份机器契约 |
| [历史基线](V2_REPORT_BASELINES.json) | A1、M2、M3.1 精确 commit、原路径、Git blob/长度/SHA-256 |
| [接口澄清](V2_REPORT_API_RESPONSE_R2.md)、[验证说明](V2_REPORT_VALIDATION.md) | 技术项对照及每项实际验证范围 |
| [参考工具](tools/README.md)、[golden manifest](vectors/v2/manifest.json) | Python/TypeScript 编码与独立复算入口 |

路径相对本文件；原基线源码按基线清单中的 commit 和原路径核对。文件存在和源身份一致只能证明材料可定位，不能证明当前服务实现或链上事实。

## 两阶段合成材料

[PrePurchase](examples/v2/prepurchase.candidate.json)、[FinalRelease](examples/v2/final-release.candidate.json)包含完整输入/报告与 blocked 签名包。原合成 PDF 由各自 hashMaterial.sourceFiles 的相对路径定位；hashMaterial 给出 canonical 文件、byteLength 与 Ethereum Keccak-256。

[golden manifest](vectors/v2/manifest.json)绑定对象→canonical bytes/hex/length/hash、反例和依赖原 bytes，各路径相对 manifest 目录。完整已评分 synthetic 正例只验证字段与字节；它不证明真实模型评分、可信 Registry 或生产签名资格。真正 TypeScript 必须按固定版本严格编译后运行；CJS 不能替代 TS 证据。复算命令见 [工具说明](tools/README.md)。

## 发布与验收身份

原 R2 的机器 Schema、OpenAPI、wire IDs、canonical/golden bytes、参考工具源码和合约 ABI 保持不变。公开说明修订必须用新的 release 身份清单记录历史源 SHA、当前 Markdown SHA/长度及发布 commit 的外部关联；现有冻结清单仅按其声明的历史源提交核对。

实际模型、R2 报告 HTTP adapter/存储、独立 signer、报告相关 Solidity helper、耐久并发恢复及整链运行使用各自精确提交的证据，状态分开记录。原格式冻结不增加生产密钥、资金或部署权限，也不批准当前未审的业务评分政策。
