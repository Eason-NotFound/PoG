# 0.2 两阶段合成格式示例

这两份 bundle 供审核 Schema、输入绑定和精确字节，**不是真实生产请求或链上接受证据**。API 规范与完整报告哈希规范均未冻结。

- `inputCandidate`：完整阶段输入，链快照 `verified=false`。
- `reportCandidate`：incomplete / Review / null，仅离线说明缺资料报告格式。
- `signingEnvelopeCandidate`：blocked，无签名／domain／assessment。
- `operationCandidate`：展示 completed tagged union 的结构。因来源为合成且链未核验，生产 API 不得将这一 bundle 受理为已成功审核；应按生产门禁处理不可信链输入。
- `hashMaterial`：可复算 inputHash、reportHash、规范长度与原文件定位。只有独立 `reportCandidate` 对象进入 reportHash；不能哈希整个 bundle。

`files/*.pdf` 是公开的一页测试 PDF，明确写有 SYNTHETIC。其原 bytes 的 SHA-256 和 Ethereum Keccak-256 已计算，并写入 DocumentVersion；不是税务发票、真实交付或已接受的收货证明。文件名称仅用于公开示例定位，不是业务服务器私有路径。

当前采购前证据 hash 按既有 V2 abi.encode 公式离线复算。最终阶段的 receiptDigest 按虚构公开域和虚构 RecipientReceipt 字段离线计算，**没有 Recipient 签名、接受交易、回执、事件或 canonical block**。因此 sourceRecordId=`synthetic-unaccepted-receipt-proof-001` 不能被生产 signer 当作合法已接受收货记录。其余链承诺使用对应测试 PDF 的原文件 Keccak；没有改变 V2 合约或定义新 Merkle 算法。

同目录 `*.input.cjson`、`*.report.cjson` 是精确 UTF-8 bytes，无 BOM、无尾换行。报告和输入哈希是对这些 bytes 计算，不是 pretty JSON 的 bytes。向量工具和实际命令见[验证说明](../../V2_REPORT_VALIDATION.md)；签名／helper／交易仍 NOT_RUN。
