# PoG V2 AI 报告接口候选：技术附录

版本：**0.2-candidate；API 与完整报告哈希规范未冻结。** 用户将 API 规范交 API 负责人确认后再通知接口负责人。合约接口兼容已收到通过意见；固定 V2 源码和 ABI 保持原样。本附录是候选修订及离线校验依据，不是生产接口实现或独立验收。

## 0. 类型与调用

PrePurchaseInput / FinalReleaseInput 对应第 3 节阶段输入；ReportBody 是第 4 节唯一正文；ErrorEnvelope 是技术失败；SigningEnvelope 是独立签名包。逻辑 `assess(request)` 输出报告或错误；传输使用 BODY 外的 OperationEnvelope，互斥、null、HTTP、认证、幂等与错误矩阵见[传输候选](V2_REPORT_TRANSPORT_CANDIDATE.md)。现有 A1 AIAdapter.assess 仍是 dict → None 的 unavailable 占位，本次未改服务源码。

[机器 Schema](schemas/v2/input.schema.json)固定字段与 JSON 类型；原始重复键／数字 token、uint string 上界、数组顺序、跨对象一致、可信来源及签名门禁需要额外语义验证。Schema 校验通过不等于可签或阶段允许。

## 1. 精确版本与范围

- `api-v0.1.0-a1` → `1844186df10854cd49ccc0886f5622e71e572d8e`（api_a1）。
- `blockchain-v0.2.0-m2` → `61aa673653dd31188d2627d76cbba3f97fed6137`（contract_abi_m2）。
- `blockchain-v0.3.1-m3.1` → `977ea6223f2ce8a9e0f0159c42c289bab4420656`（local_deployment_m3_1）。
- 三者的源文件 Git blob／SHA-256 见 [baseline manifest](V2_REPORT_BASELINES.json)。A2 `9189d8c` 未验收，只含 local synthetic fixture，不能作正式 AI 服务依赖。
- 依据：docs/M2_V2_INTERFACE_IMPLEMENTED.md、contracts/src/PoGRegistryV2.sol、contracts/src/interfaces/IPoGRegistryV2.sol、contracts/src/ProcurementEscrowV2.sol 及 packages/contract-abis/v2。
- 合约负责人转交的 81 项旧合约测试 PASS 没有本候选精确命令／日志，不能算 AI 候选端到端通过。本轮验证记录见[验证说明](V2_REPORT_VALIDATION.md)。
- 仅规范、Schema、合成例子及离线参考工具／向量。未运行真实模型、真实 signer、Solidity helper 或链上交易，未接入共享演示链。

## 2. 业务约束

PrePurchase=0；FinalRelease=1。旧 FinalEvidence 不作为枚举别名。projectId / procurementId 为链上 bytes32；claimAlias 不替代它们。PO 必须已经承诺；PrePurchase 不能将 optional PO 的 CPU demo 输入当生产前提。FinalRelease 需要 RecipientReceipt 已被 Registry 接受，不需要此时尚未产生的付款回单。

AI 提供风险证据；Reserve 与 Release 人工审批分开。Registry 可记录 Pass / Review / Reject，Escrow 不读取正文或模型，也没有 outcome／分数自动拒款阈值。资金释放到固定 Foundation，不证明 Vendor 已收款。

## 3. 可信完整输入 AIInputSnapshot

`PrePurchaseInput` / `FinalReleaseInput` 是完整可持久化的 AIInputSnapshot，不是链上 AssessmentInput。固定 schemaVersion=`pog.ai.input/0.2-candidate`。共通键：schemaVersion、stage、claimAlias、projectPolicy、vendorContext、comparisonContext、registrySnapshot、documents、evidenceSnapshot；stage=0 加 procurementData，stage=1 加 deliveryData。所有键存在，未知键拒绝。

| 组 | 固定字段／类型 |
| --- | --- |
| projectPolicy | version、periodStart、periodEnd、category string；budgetAtomic、unitPriceLimitAtomic uint256 string；assetDecimals integer=6；日期有效 YYYY-MM-DD，开始不晚于结束 |
| vendorContext | vendorId、identityStatus string；accountHistorySnapshot、relatedPartySnapshot string/null，非空值为外部不可变快照 ID |
| comparisonContext | quoteComparatorsSnapshot、crossProjectDuplicateSnapshot、procurementHistorySnapshot string/null，非空值同样为外部快照 ID |
| registrySnapshot 共通 | verified boolean；blockNumber uint256 string/null、blockHash bytes32/null；projectId、procurementId bytes32；foundation、recipient、vendor、asset address；state 精确 V2 名；currentEvidenceHash bytes32 |
| documents | EvidenceRef[]，第 4 节六字段；必须从 DocumentVersion 选择，不接受 AI 重写文件元数据 |
| claimAlias | string/null，仅业务关联 |
| evidenceSnapshot | 下表结构，API 所有、不可覆盖；包含完整部署、可信文件版本与依赖承诺 |

生产 registrySnapshot.verified=true 要求 chain adapter 验证实例指纹、角色和 canonical block；blockNumber、blockHash 不得 null。字段写 true 不是验证证明；必须保留可信来源审计记录，并在签名／提交前重新核对当前链。离线合成示例 false 不可用于生产审核成功或签名。

### 3.1 阶段字段

PrePurchase 允许 PORecorded / PreAssessed / ReserveApprovalPending。registrySnapshot 加 budgetCap(uint256 string)、poHash、requestHash、goodsRequestHash(bytes32)。procurementData 固定 requestVersion(uint64 string)、category(string)、description(string)、quantity(uint256 string)、quoteAmountAtomic(uint256 string)、poVersion(uint64 string)。0.2 数量暂限整件，分割数量仍待业务／API 确认，不 round。

FinalRelease 允许 ReceiptConfirmed / FinalAssessed / ReleaseApprovalPending。registrySnapshot 加 reservedAmount、invoiceAmount(uint256 string)、poHash、invoiceHash、goodsHash、receiptDigest(bytes32)。deliveryData 固定 poVersion、grnVersion、invoiceVersion(uint64 string)、poQuantity、grnQuantity、invoiceQuantity(uint256 string)、inspectionVersion(uint64 string/null)、receiptAccepted(boolean)。生产必须 receiptAccepted=true 并核对 Registry 已接受记录，而不是只看布尔值。

阶段所需业务资料沿审阅版；类别／税务真实性／检测／跨项目查重规则要有版本化政策和依赖来源，不能凭 OCR 或图像相似宣称已核验真实性。

### 3.2 EvidenceSnapshot

固定键：

| 字段 | 类型与用途 |
| --- | --- |
| snapshotId | 非空 ASCII 机器 ID；永久解析不可变快照 |
| evidenceVersion | uint64 string；报告回显 |
| createdAt | UTC 秒级 YYYY-MM-DDTHH:mm:ssZ，有效日期时间 |
| owner | 固定 api；不是请求者自行上传的可信证明 |
| deployment | runId、instanceId(ASCII ID)、chainId(uint256 string)、registry(address) |
| documentVersions | DocumentVersion[] |
| commitments | Commitment[] |
| externalSnapshots | ExternalSnapshot[] |

资源范围为 `deployment.instanceId + deployment.registry + registrySnapshot.procurementId + stage`，同时核对 chainId、runId、projectId。evidenceVersion 由 API 按该范围单调递增分配；任何文件选择／版本／双哈希／页集合、政策与外部快照、链区块／状态／证据或输入业务字段变化都建立新版本。同一不可变输入的重试复用原版本；不能在原 snapshotId/version 下覆盖事实。更新依赖不会自动改变合约原有不可变证据；矛盾材料需走原业务纠正流程。

DocumentVersion 固定：evidenceId、documentId、documentVersionId(ASCII ID)，version(uint64 string)，kind(第 4 节 enum)，category(string)，contentType(application/pdf | image/jpeg | image/png)，sizeBytes(integer 1..10485760)，contentSha256、leafKeccak256(bytes32)，pages(integer 1..2147483647[] / null)。hash 均对原文件 bytes 计算；SHA-256 用于审计，Keccak 为叶承诺。A1 的 64 hex 无 0x 表示须显式转换并验证 32 bytes，不能将 SHA 值当 Keccak。MIME／大小／页码通过可信文件服务确定，AI 不能替换 metadata，也不能获得本机服务器路径。

DocumentVersion PDF pages 为非空、递增且去重的有效 1-based 页集合；JPEG/PNG pages=null，Locator.page=null。BODY EvidenceRef 对 PDF 的 pages=[] 只表示未引用特定页面，不表示无分页；图片必须 null。输入 documents 与 DocumentVersion 的 MIME、页集合和元数据按原快照匹配，正文可选择 PDF 页子集。文件读取通过授权 bytes stream 或短期下载授权；URL／access token／本机路径不进入快照或正文，不进入 inputHash。

ExternalSnapshot 固定：snapshotId、provider、version(string)、contentSha256、contentKeccak256(bytes32)。哈希绑定服务保留的精确不可变原 bytes；vendorContext / comparisonContext 的非 null ID 必须解析到此列表。授权原 bytes 与版本须保留至审计期结束；读取后逐项复核双哈希。空结果也需要权威快照及哈希，null 不表示无重复或无关联交易。原始依赖 JSON 自身可使用服务既定格式，contentKeccak256 对其原 bytes 取值，不冒充 CJSON 报告。

### 3.3 EvidenceRole → 链上承诺

Commitment 固定：evidenceRole(enum)、commitmentHash(bytes32)、scheme(enum)、documentVersionIds(ASCII ID[])、sourceRecordId(ASCII ID)、recipeVersion(string/null)。sourceRecordId 解析到 API 保留的不可变承诺登记／链上接受记录；不能根据文件名、kind 或 category 猜测字段。

| evidenceRole | stage | 来源与复核 |
| --- | --- | --- |
| poHash | 0、1 | 当前 PO 的固定版本或已登记集合 |
| requestHash | 0 | 当前采购需求版本或集合 |
| goodsRequestHash | 0 | 当前货物规格／需求版本或集合 |
| invoiceHash | 1 | 当前最终 Invoice 版本或集合 |
| goodsHash | 1 | 当前交付材料版本／GRN／照片／检测集合 |
| receiptDigest | 1 | Registry 验签并接受的完整 RecipientReceipt EIP-712 digest |

每阶段必须各有一条适用 role；commitmentHash 与 registrySnapshot 同名值相等。
scheme=raw_file_keccak256：恰好一个 documentVersionId，commitmentHash 必须等于该原文件 leafKeccak256，recipeVersion=null。
scheme=registered_manifest：documentVersionIds 非空；recipeVersion 非空，解析至已批准的不可变组合算法及精确 manifest bytes。适配器复算并与链值比较；无批准 recipe 或无法复算即阻止签名／技术失败。0.2 不新造 Merkle 或改变已承诺材料算法。
scheme=recipient_receipt_digest：仅 receiptDigest，documentVersionIds=[]，recipeVersion=PoGRegistryV2/2；sourceRecordId 解析 Registry 接受的 typed receipt、域、tx、receipt、事件与 canonical block，并复核保存 digest。Receipt 文件的 leafKeccak256、receiptEvidenceHash、receiptDigest 三者不能混同。该收货确认的提交期限后来经过，不抹去已接受的历史事实。

### 3.3.1 category 与链上 evidenceRole 精确映射

| 文件／记录 | A1 文件 category | evidenceRole |
| --- | --- | --- |
| PO | purchase_order | poHash |
| request | request | requestHash |
| goods request | goods_request | goodsRequestHash |
| invoice | invoice | invoiceHash |
| goods evidence / GRN | goods_evidence | goodsHash |
| Registry 已接受的 RecipientReceipt typed digest | 非文件映射 | receiptDigest |

category 只限定被选为承诺来源的 DocumentVersion，不能据 category 自动把每份文件都当链上承诺。receipt_evidence 文件仍是辅助证据，其 Keccak 不能替代 receiptDigest。报价是候选辅助资料；quote 不是 A1 当前上传白名单，接入方式属于后续实现，不伪称已实现类别。

evidenceHash 是 Registry 的固定 ABI 聚合；inputHash 是 AI 实际读取完整输入快照；reportHash 是规范化正文。三者用途不同，不能互换。

### 3.4 inputHash

`inputHash = EthereumKeccak256(PoG-CJSON-0.2(完整 PrePurchaseInput 或 FinalReleaseInput))`。完整输入没有 inputHash 字段；所有第 3 节字段都包括在内，没有字段删减。原文件／依赖原 bytes 间接受 DocumentVersion / ExternalSnapshot 双哈希绑定。排除项仅指本来不在输入对象中的 transport IDs、认证、下载凭据、operation 状态、签名包与 inputHash 自身；不能排除 projectPolicy 或历史快照。

API 持久化输入对象、精确 canonical bytes、inputHash、快照身份、依赖原 bytes 或可授权永久解析的内容记录。AI、API、signer 各自复算，任何不一致拒绝，不能为了“匹配”修改输入。CJSON 上限与排序同第 6 节；snapshot 数组另按 documentVersions:(evidenceId,version 数值)、commitments:evidenceRole ASCII、documentVersionIds:ASCII、externalSnapshots:snapshotId ASCII 升序，相关 ID／role 唯一。

## 4. ReportBody

固定 schemaVersion=`pog.ai.report/0.2-candidate`。全部 16 键：schemaVersion、stage、projectId、procurementId、evidenceVersion、evidenceHash、inputHash、executionMode、versions、outcome、riskScoreBps、completeness、summary、evidenceRefs、findings、missingInputs。除明确 nullable 外禁止 null；未知键拒绝。

stage integer 0/1；outcome integer Pass=0 / Review=1 / Reject=2；riskScoreBps integer 0..10000/null；completeness complete/incomplete；executionMode synthetic_fixture/rules_only/model_and_rules。projectId/procurementId/evidenceHash/inputHash bytes32，evidenceVersion uint64 string。summary 是 Unicode string，不包含秘密、私有路径或个人账户详情。

versions 固定八键：serviceVersion、ruleSetVersion、extractionSchemaVersion 非空 string；scorePolicyVersion、modelProvider、modelId、modelVersion、promptVersion 非空 string/null。model_and_rules 的模型／prompt 版本必须全有；rules_only / synthetic_fixture 的模型字段均 null；没有已确认 scorePolicyVersion 时不得生产签名。fixture 的测试分数／测试政策仅验证格式，不是实际评分算法。

EvidenceRef 六键：evidenceId(ASCII ID)、kind(ProcurementRequest/Quote/PO/GRN/Invoice/Receipt/Photo/Inspection)、version(uint64 string)、contentSha256(bytes32)、leafKeccak256(bytes32)、pages(integer[] / null；图片=null，PDF 空引用=[])。
Finding 五键：findingId、reasonCode(ASCII ID)、severity(information/review)、message(Unicode string)、evidenceLocators(Locator[])。
Locator 三键：evidenceId(ASCII ID)、page(integer 1..2147483647/null)、field(ASCII ID/null)。非 null page 必须出现在可信页集合与当前 EvidenceRef；文件未定位时不能声称已核实原件。
MissingInput 三键：inputKey、reasonCode(ASCII ID)、requiredForStages([0,1] 递增无重复子集；非空)；inputKey 指实际输入／依赖，reasonCode 属版本化目录，新增理由须修订 ruleSetVersion。

所有用于排序的 ID／code／非 null field 模式 `[A-Za-z0-9][A-Za-z0-9._:/-]*`。BODY 与输入的 stage、projectId、procurementId、evidenceVersion、evidenceHash、inputHash 对齐；BODY.evidenceRefs 不得引入输入没有的证据。所有 input.documents 必须有 BODY 引用，metadata 逐项一致，报告引用页可为可信页的子集；PDF 证据无使用定位时可 pages=[]；图片仍必须 pages=null。不得伪造页。未知/重复 ID 拒绝。

BODY 禁止 reportHash、assessmentId、domain、signer、nonce、deadline、signature。projectId 由 BODY/inputHash/reportHash 与 Registry 证据绑定，但不是新增 AIAssessment 字段。

## 5. 技术错误、缺资料与生产签名门禁

缺失业务资料且可安全列明：ReportBody.incomplete + outcome=Review + riskScoreBps=null，missingInputs 非空，SigningEnvelope.blocked。complete 要求 missingInputs=[]；评分政策未知的 complete/null 只允许候选诊断格式表达，永久 blocked。生产评分请求未配置已批准政策时返回 SCORING_POLICY_UNCONFIRMED，不能以诊断报告掩盖执行失败。完整并已评分的 Review/Reject 不因结果本身禁签。

已声明版本打不开、双哈希错误、链不可信、依赖技术失败、模型超时／失败／格式错误、规则执行错误、canonicalization 失败：ErrorEnvelope，不虚构风险结果。REQUIRED_EVIDENCE_MISSING 不作为泛用技术错误码；区分业务资料缺失与已声明证据不可用。精确 code、HTTP 和重试见传输候选。

ErrorEnvelope 固定十键：schemaVersion=pog.ai.error/0.2-candidate、status=error、stage(0/1/null)、procurementId(bytes32/null)、code、retryable(boolean)、message、missingInputs(MissingInput[])、report=null、signingEnvelope=null。stage/procurementId 仅可信上下文已知才填，损坏输入不能默认 stage=0。

生产门禁全部满足才可 prepared/signed：规范及版本已确认、完整可评分、已批准评分／执行政策、所有版本可解析、原 bytes/hash/role/chain 已核验、inputHash/reportHash/ID/digest 一致、allowlist/current nonce/deadline 可用。
- synthetic_fixture 在生产 **永远 blocked**，即使有整数 score 和完整数据。协议测试独立 fixture scope，不复用生产 SigningEnvelope 的可签例外。
- model_and_rules 的技术失败直接 error，不能静默 fallback 成可签 rules_only。
- rules_only 生产默认禁签，仅明确已批准且版本化的部署执行政策与评分政策允许后启用；本候选不代表已批准。
- API 尚未确认时候选报告一律不进入生产签署。reportHash 复算成功也不改变门禁。

## 6. 精确字节候选 PoG-CJSON-0.2
本修订明确选定自有 PoG-CJSON-0.2，核心字节规则沿用 0.1，图片 null 语义按本版 Schema；不再保留 CJSON/JCS 二选一。选定候选不等于 API 冻结。本 profile 不宣称 RFC 8785 或现有 json.dumps / JSON.stringify 默认输出完全实现。
1. 校验独立 BODY／完整输入 schema；拒绝未知／缺失／重复 key，解码后重复亦拒绝（a 与转义键 a 相同）。固定键只含 ASCII，所有嵌套对象同样限定键。inputHash 使用完整合法输入对象，键名和转义规则相同。不能先转换丢失重复 key 再检查。
2. 所有字符串为 Unicode scalar 序列；拒绝孤立 surrogate、无效 UTF-8。没有隐式 NFC/NFD、换行替换、空格 trim 或大小写转换。文本 NFC 与 NFD 字节不同。hash／address 若非小写格式，输入应报错，不静默修复。
3. 对象键递归按 ASCII 字节值升序，不按 schema 展示顺序／locale 顺序；不增加空白。数组保留次序，不隐式排序或去重。
4. 数字只允许 schema 中已列的小整数，canonical token 为不带符号的 ASCII 十进制；拒绝原 token float／指数／-0／NaN／Infinity；boolean 不可冒充 integer。金额／chainId／nonce／deadline 等宽整数用 string，仅 ASCII 模式 0 或 [1-9][0-9]*，按 uint64／uint256 检查上界，不转 float。代币金额为 6 decimals 的 atomic units，无自动换汇。
5. 字符串转义只有：双引号写 `\"`，反斜线写 `\\`；U+0000..001F 一律写小写六 ASCII 字符 `\u00xx`，包括 tab／LF／CR。其他 scalar 原样 UTF-8，包括 /、<>&、U+2028、U+2029；不额外转义非 ASCII。此规则故意不同于默认短转义 `\n`／`\t`；Python 与 TS 必须各自实现同一算法，不能直接假定默认序列化器兼容。
6. 固定字面量 null／true／false；逗号和冒号紧凑；对象／数组无尾逗号。输出 UTF-8，无 BOM、无尾换行。JSON 文件的漂亮排版／编辑器 LF 不是 BODY 精确字节。
7. 生产者在生成 immutable BODY 前负责确定数组语义：evidenceRefs／documents 按 (evidenceId,version数值) 升序且 ID 唯一；pages 递增且不重复；findings 按 (reasonCode,findingId) ASCII 升序、ID 唯一；evidenceLocators 按 (evidenceId,page：null先于正整数,field：null先于string) 升序且元组唯一；missingInputs 按 inputKey ASCII 升序且唯一；requiredForStages 递增且不重复；blockReasons 按 ASCII 升序且不重复。不同数组顺序改变 bytes/hash；接收方拒绝非规定顺序，不能为“修复”而排序后接受原hash。
8. 报告提交／存储端从合法 BODY 重建 canonical bytes，并与提供的 bytes 做严格相等比较，再计算 hash。允许模型结构化结果先经独立抽取校验转换为报告；模型原响应／整个示例 bundle／上传文件都不是待哈希正文。
9. 0.2 候选上限：canonical BODY／输入各 <=1MiB，嵌套深度 <=32（根对象=1），每数组 <=1024 项，单 string <=8192 Unicode scalars；单文件原 bytes <=10MiB。待 API 确认；超限拒绝，不 truncate。

候选 reportHash = 0x + lowercase_hex(Ethereum Keccak-256(canonical BODY bytes))。
不是 SHA3-256、文件 SHA256、原文件 leaf Keccak 或 API 幂等 SHA256。V2 合约只接受 bytes32 非零，不为此算法背书。合约兼容通过仅表示 bytes32 绑定兼容；完整编码与哈希规范仍待 API／合约确认，尚未冻结。


## 7. 签名信息与固定 V2
SigningEnvelope 固定键：schemaVersion=pog.ai.signing-envelope/0.2-candidate、status(blocked/prepared/signed)、blockReasons(string[])、domain(Domain|null)、assessment(AssessmentInput|null)、signature(0x bytes|null)。
blocked 时 domain／assessment／signature 均 null；prepared／signed 必须 blockReasons=[]、completeness=complete、missingInputs=[]、风险整数及评分政策／版本已确认、所需证据与运行实例已验证。prepared 时 domain／assessment 非 null，signature null；signed 时全部非 null。签名来源／验证状态由 API operation audit 单独保存，不加入 BODY，不把 signed 当链已接收。
Domain 固定键：name=PoGRegistryV2、version=2（string）、chainId(uint256 string)、verifyingContract(address)。取实际已验证运行实例，不能硬编码历史 manifest 地址或默认链 ID。
AssessmentInput 使用下述字段顺序与类型，JSON stage/outcome/risk 为小整数，nonce/deadline 为 uint256/uint64 string。domain uint chainId 转为 typed data 必须精确整数，无浮点。

AIAssessment(uint8 stage,bytes32 procurementId,bytes32 assessmentId,uint8 outcome,uint16 riskScoreBps,bytes32 evidenceHash,bytes32 reportHash,address signer,uint256 nonce,uint64 deadline)

EIP-712 primaryType=AIAssessment；AssessmentInput 只是 Solidity 元组名称，不能作为签名类型。BODY stage/procurementId/outcome/riskScoreBps/evidenceHash 必须与签名 assessment 逐字段一致；风险 null 不能转 uint16。projectId 核对链上采购；inputHash 和 reportHash 分别从审计保留的输入／正文重算。signer 是当前 allowlisted AI 权限，不是 Foundation／human／任意 request 参数。
EIP-712 signer nonce 为同一个 Registry 的 aiNonces(signer) 当前值，跨 project／procurement／stage 共用；relayer EVM transaction nonce、Recipient nonce 和 Escrow human nonce 属独立 nonce family。并发锁范围为 deployment namespace（runId、instanceId、chainId、Registry）＋verifyingContract＋nonceFamily=aiNonces＋signer。prepared 必须在返回或请求签名前，原子持久化该 nonce、deadline、完整 typed data、digest、操作身份及唯一未决锁；这与尚未消耗链上的 aiNonces 不矛盾。重启不能丢失 prepared 或重新分配同一 nonce。临签署和提交重新核对，不能本地 nonce+1。已提交但结果未知须锁住并先查原 tx／receipt／canonical block／当前 assessment 与 nonce，禁止因 NonceMismatch 或 timeout 直接盲重签。确认操作已失效后，才用链上当前 nonce 构造新 ID 并重新签署。成功录入 assessment 才消费 nonce。
deadline 为 uint64 秒时间；仅 block.timestamp > deadline 为过期，等于仍有效。前端／API 的安全裕量政策不得冒充合约条件。

assessmentId = keccak256(abi.encode(
  keccak256("POG_V2_AI_ASSESSMENT_ID"), uint8(stage), bytes32(procurementId),
  uint8(outcome), uint16(riskScoreBps), bytes32(evidenceHash), bytes32(reportHash),
  address(signer), uint256(nonce), uint64(deadline)
))
assessmentId 不含自身。不得改用 encodePacked 或 JSON。
structHash = keccak256(abi.encode(
  keccak256(完整 AIAssessment 类型字符串), uint8(stage), bytes32(procurementId),
  bytes32(assessmentId), uint8(outcome), uint16(riskScoreBps), bytes32(evidenceHash),
  bytes32(reportHash), address(signer), uint256(nonce), uint64(deadline)
))
digest = keccak256(0x1901 || domainSeparator || structHash)。
DomainSeparator 严格按 EIP712Domain(string name,string version,uint256 chainId,address verifyingContract) 编码。本次未执行真实域 helper、签名或交易；离线正文向量不代替该验证。

preEvidenceHash = keccak256(abi.encode(
 keccak256("POG_V2_PRE_EVIDENCE"), bytes32(projectId), bytes32(procurementId),
 address(foundation), address(recipient), address(vendor), address(asset),
 uint256(budgetCap), bytes32(poHash), bytes32(requestHash), bytes32(goodsRequestHash)
))
finalEvidenceHash = keccak256(abi.encode(
 keccak256("POG_V2_FINAL_EVIDENCE"), bytes32(projectId), bytes32(procurementId),
 address(foundation), address(recipient), address(vendor), address(asset),
 uint256(reservedAmount), bytes32(poHash), bytes32(invoiceHash),
 uint256(invoiceAmount), bytes32(goodsHash), bytes32(receiptDigest)
))
finalEvidenceHash 不直接加入 preEvidenceHash／requestHash／goodsRequestHash。必须对应当前 Registry，不拿阶段输入 JSON 文件 hash 替代。
对齐 helper：computePreEvidenceHash、computeFinalEvidenceHash、computeAssessmentId、assessmentDigest。helper 仅证明计算结果，不能证明当前状态／nonce／signer 可提交。AssessmentView 返回结构不能直接当签名元组。
正常同证据 renewal 使用新 assessmentId、当前 nonce、有效 deadline；新 assessment 替换会令旧人工票 bundle 无效。EOA／ERC1271 均按 SignatureChecker 现有行为验证，不将合约钱包强行当 EOA 恢复地址。


## 8. 字段与职责

API／文件／依赖服务创建可信 immutable snapshot、授权 bytes 读取、业务与链 ID 映射、保留快照／原 bytes／规范 bytes，复算正文。AI 生成可复核 risk report，不能自行“验证”或修改链事实、文件 metadata、预算／历史。
API 组的 chain adapter 读取当前实例、状态、evidence、allowlist、aiNonces，计算 ID/digest，并管理持久化 operation、EIP-712 nonce 与 deadline。AI 组负责独立 AI signing component 并控制 AI 密钥；组件重新验证输入/报告 bytes、执行模式、政策与签名门禁，未知模式或任一失败均 fail closed。模型不接触私钥。API 组 relayer 仅提交已有签名并确认 tx／receipt／event／canonical block，不持有 AI/Human/Recipient 密钥。责任归属作为具体候选供双方确认，持久化数据形状和恢复规则见 [签名协调](V2_SIGNING_COORDINATION_CANDIDATE.md)。
Registry／Escrow 保持固定验证与人工资金规则；人工分别批准 reserve/release，AI 不替代其权限。固定合约不强制所有角色地址彼此不同，运营授权落实分离。

## 9. 准确反例预期（链上试验 NOT_RUN）

其他条件均合法；多处错误按实际首个检查失败。whenNotPaused / escrowIdle modifier 在 submit 函数体之前；函数体顺序为采购存在→允许阶段状态→分数→证据→非零哈希→Duplicate→allowlist→期限→nonce→ID公式→签名。越界枚举先在 ABI 解码失败，不归为 InvalidAssessmentStage。

| 反例 | 层与准确预期 |
| --- | --- |
| 重复/缺/未知键、非法 UTF8/BOM、float/exponent/-0/NaN、孤立 surrogate、错 null/宽度/排序/大小 | FORMAT 拒绝，不泛称 Solidity revert |
| 改 BODY 或输入仍用旧 hash；正文和签名字段不符；projectId 不匹配 | 链下 BODY_BINDING 拒绝；Registry 不读取正文 |
| PrePurchase 在 Reserved 等不允许状态；FinalRelease 在 Recipient 确认之前 | InvalidAssessmentStage() |
| 当前阶段 evidenceHash 不匹配 | InvalidEvidence(expected, actual) |
| 分数 10001 | InvalidRiskScore(10001)；null 在链下 FORMAT 拒绝，不能提交 uint16 |
| 错域名／版本／chainId／Registry；错 primaryType 或旧签名 | InvalidSignature() |
| reportHash 改而 assessmentId 没重算 | InvalidAssessmentId(expected, actual) |
| 重算 ID 但保留旧签名 | InvalidSignature() |
| 原封不动重交已接受 ID，状态仍允许且前序合法 | DuplicateAssessment(id)，先于 nonce |
| 同 signer 新 ID 使用已消费 nonce，包括跨采购／跨阶段并发 | NonceMismatch(signer, expected, actual)；先解决原 operation／未知 tx，再决定重签 |
| 当前 block.timestamp 大于 deadline | SignatureExpired(deadline)；等于仍通过期限条件 |
| signer 撤销 | 新提交 UnauthorizedAISigner(signer)；旧评估用于执行资金动作 InvalidAssessment(id) |
| 旧评估在合法 reserve/release 流程执行前过期 | Escrow InvalidAssessment(id)，无资金动作；更早流程条件仍可先失败 |
| 新 assessment 的旧人工票用于资金动作 | 按当前 action/terms/assessment 独立审批检查，不可复用 |
| AI Reject | 不自动拒款；最终资金动作依赖人工审批 |
| unknown risk／模型失败冒充 Pass 或 score=0 | 链下 gate 拒绝，不能宣称合约检测模型或资料完整性 |

## 10. 示例与离线验证材料

[采购前示例](examples/v2/prepurchase.candidate.json)与[最终释放示例](examples/v2/final-release.candidate.json)包含完整候选输入、报告与 blocked 签名包。样例中的文件为公开合成测试材料，链上事实未验证、模型未执行；任何测试分数不是实际风控评分。

[vectors/v2](vectors/v2/README.md)提供报告对象→精确 UTF8 bytes／hex／长度→预期 Ethereum Keccak。Unicode、控制字符、null、空数组、对象键与数组顺序等对照仅验证候选字节规则。Python／严格 TypeScript 参考工具与外部库复核的实际结果见验证说明。

完整生产可评分、可签署正例仍需明确评分／执行政策、可信实例及独立验收。离线 scored synthetic BODY 不满足这一项。真实 typed data／签名、Solidity helpers、submit receipt／AIAssessmentRecorded／当前评估引用及 nonce 消费、Escrow 反例尚未执行。共享演示地址不是专项隔离环境授权，不使用历史地址作为当前实例。

## 11. 待确认决策

| ID | 0.2 候选与确认事项 | 确认方／状态 |
| --- | --- | --- |
| D01 | 输入／BODY／错误／签名／传输 Schema 与 inputHash；十字段合约不增加字段 | API、AI；API 未冻结 |
| D02 | 必需业务资料、整件数量限制、category/税务/检测与评分规则 | API、AI及业务负责人；待确认 |
| D03 | 不可变快照版本范围、可信 canonical chain、DocumentVersion、externalSnapshots、evidenceRole/commitment recipe | API、合约；待确认 |
| D04 | 签名服务职责、单未决 nonce、未知 tx 恢复、EOA/ERC1271 | API、AI、合约；待确认 |
| D05 | 错误分类/重试、不完整 Review/null、纯规则与模型失败禁签、生产 synthetic 永禁 | API、AI；执行与评分政策待业务批准 |
| D06 | PoG-CJSON-0.2 全部精确规则及 inputHash/reportHash Keccak，不等同 JCS | API、合约；完整规范未冻结 |
| D07 | limits、图片 pages=null／PDF 1-based list、机器 ID/理由目录 | API、AI；待确认 |
| D08 | 真实正例、已授权隔离 signer/domain/helper、独立端到端验收 | 合约、API、独立验收；NOT_RUN |

本轮修订获得用户直接授权；API 冻结由用户收到 API 负责人确认后另行通知。对任何文档、工具、向量的 self-check 均不提升为双方批准或独立验收。
