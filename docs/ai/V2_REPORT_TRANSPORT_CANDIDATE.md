# PoG V2 AI 报告：传输契约候选 0.2

状态：**CHANGE_REQUIRED／候选，未冻结，待 API 负责人复审**。本文将本轮传输、归属和持久化方案选定为一个可复核候选，选择不等于 API 批准或有界签名测试授权。A1 没有 AI 报告 endpoint；A2 `9189d8c` 仍是未验收候选，仅有 local-only synthetic `ai_pre` fixture，也没有真实 AI 报告 endpoint。真实 AI HTTP adapter、报告存储及服务联调属于 A3/M3.3，须另行授权。本次不实现 HTTP 服务、数据库、模型调用、签名器或 relayer；固定 V2 合约与 ABI 不变。

报告字段与字节规则见[审阅稿](V2_REPORT_CANDIDATE.md)及[技术附录](V2_REPORT_TECHNICAL_APPENDIX.md)。本轮明确选择自有 **PoG-CJSON-0.2** profile：附录固定全部字节规则，未声称它符合 RFC 8785/JCS。完整机器传输契约见 [OpenAPI 3.1 候选](V2_REPORT_OPENAPI_CANDIDATE.json)，其 components 嵌入同包七个 Schema，引用无需私有主机路径或远程下载。

## 1. 候选调用与权限

| 方法／路径 | 请求 | 候选响应 | 含义 |
| --- | --- | --- | --- |
| `POST /v2/ai/assessments` | `PrePurchaseInput` 或 `FinalReleaseInput`，`Content-Type: application/json` | 新操作和同键恢复操作均为 `202 OperationEnvelope`，即使既有操作已终态 | 异步建立／恢复一次逻辑审核；响应头 `Location: /v2/ai/operations/{operationId}` |
| `GET /v2/ai/operations/{operationId}` | 无正文 | 已授权存在的操作为 `200 OperationEnvelope`，任何操作状态均为 200 | 查询同一次审核；状态在 JSON 内，失败操作也不伪装成成功报告 |

每个请求必须有服务 `Authorization: Bearer <service-credential>`，仅允许受控 service principal；API 组负责 credential 发行、撤销、有效期、service identity 解析及 deployment/project/procurement scope 校验。这是待实现的服务认证契约，不假定 A1 已具备相同能力。不得将访问令牌放入 JSON、规范字节、日志或候选示例。POST 调用者必须具有目标采购的审核发起权限；GET 必须再次检查相同资源范围的读取权限，operationId 不是访问凭证。服务认证不赋予合约签名权。

输入是 API 从可信资源组装的不可变对象。将调用者自填 `verified=true` 或 `owner="api"` 通过 Schema，不能视为可信链或文件事实。生产入口需复核其来源、授权资源范围和已持久化的 snapshot。主机路径、内网地址、存储密钥、Cookie 和长期私密下载 URL 不属于输入字段。

## 2. 五类对象与哈希范围

| 名称 | 固定版本 | 用途 |
| --- | --- | --- |
| `PrePurchaseInput / FinalReleaseInput` | `pog.ai.input/0.2-candidate` | API 提供的完整风险审核输入与不可变证据快照 |
| `ReportBody` | `pog.ai.report/0.2-candidate` | 唯一报告正文，增加 `inputHash` |
| `ErrorEnvelope` | `pog.ai.error/0.2-candidate` | 未产出报告的技术失败；`report` 与 `signingEnvelope` 固定 null |
| `SigningEnvelope` | `pog.ai.signing-envelope/0.2-candidate` | 独立生产签名包，不作为 `assess` 的业务结果 |
| `OperationEnvelope` | `pog.ai.operation/0.2-candidate` | 传输与查询资源，保存 API/AI 逻辑 ID、幂等摘要及互斥结果 |

`OperationEnvelope` 固定七键：

| 字段 | 类型／候选规则 |
| --- | --- |
| `schemaVersion` | 固定 `pog.ai.operation/0.2-candidate` |
| `operationId` | 非空 ASCII 机器标识符；API 为每次受理的逻辑操作生成 |
| `aiRequestId` | 非空 ASCII 机器标识符；API 生成并持久化；同一逻辑审核所有 attempt 相同，AI adapter 必须回显核对 |
| `payloadHash` | `0x` 加 64 位小写 hex，SHA-256 摘要 |
| `status` | `queued / running / completed / error` |
| `report` | `ReportBody` 或 null |
| `error` | `ErrorEnvelope` 或 null |

状态是明确的 tagged union：

| 状态 | `report` | `error` |
| --- | --- | --- |
| `queued / running` | null | null |
| `completed` | 合法独立 ReportBody | null |
| `error` | null | 合法 ErrorEnvelope |

固定状态转换为 `queued → running → completed/error`，排队期间也可因总期限到达进入 error；暂态重试保留 running。同一操作的 completed/error 是终态，不因轮询、POST 重传或客户端断连重新开始。内部 attemptId、开始/完成时间、超时原因、依赖错误和 chain/tx 审计留在操作审计记录，不增加 BODY 字段。

operationId 与 aiRequestId 均由 API 在首次受理事务中生成；AI 模型不生成它们。API 到 AI adapter 的调用 metadata 保存 operationId、aiRequestId 及 attemptId，AI adapter 回显前两者；metadata 与凭证在 input 对象外，不进入 inputHash 或 reportHash。ID 回显不匹配视为 MODEL_OUTPUT_INVALID，不接受另一请求的报告。

设 `inputBytes = PoG-CJSON-0.2(完整已验证 input 对象)`：

- `payloadHash = 0x + lowercase_hex(SHA-256(inputBytes))`，用于本候选操作幂等审计。
- `ReportBody.inputHash = 0x + lowercase_hex(Ethereum Keccak-256(inputBytes))`，绑定预算、供应商、比较数据与证据快照，input 本身没有 inputHash，避免自引用。
- `reportHash = Ethereum Keccak-256(PoG-CJSON-0.2(ReportBody))`。

两种输入摘要算法明确不同；`payloadHash` 不能代替 `inputHash` 或链上 reportHash。报告哈希只覆盖完整独立 ReportBody，不含操作包、错误包、签名包、HTTP 头、operationId、aiRequestId 或 pretty JSON 的换行。现有 A1 幂等 SHA256 算法是其自身已实现接口的规则，本候选未修改或复用它的内部对象形状。

## 3. 幂等、断连、期限与重试

POST 必须提供 `Idempotency-Key`，候选为 1～128 位 ASCII 标识符，模式 `[A-Za-z0-9][A-Za-z0-9._:/-]*`。幂等 namespace 为 **(authenticated service principal ID, HTTP method=POST, route=/v2/ai/assessments, deployment.runId, deployment.instanceId, deployment.chainId, deployment.registry)**；identity 使用稳定服务 ID 而非可能轮换的 Bearer token，chainId 使用规范十进制 string、registry 使用规范小写地址。该键绑定完整 inputBytes。阶段、采购、文件版本、外部快照和链快照是输入的一部分。

首次受理以原子写入建立 `key → operationId → aiRequestId → payloadHash → inputBytes/snapshotId` 映射后才能入队。并发同键同 inputBytes 必须返回同一操作。API 同时比较摘要和保存的规范字节；相同键但不同正文返回 `409 IDEMPOTENCY_CONFLICT`，不得覆盖原操作或静默改用新键。

本轮选定幂等恢复记录在操作及其所有相关签名／提交未决事项解决后至少保留 **7×24 小时**；queued/running、签名未决、提交结果未知的操作不允许按期限清除，须持续保留至全部解决，再开始 7 天窗口。过期键保留 namespace + key fingerprint 的持久化 tombstone，在该 deployment namespace 的生命周期内禁止复用；命中已过期键返回 `409 IDEMPOTENCY_KEY_EXPIRED`，不重建原操作。新业务审核使用新的幂等键。快照、正文、签名、交易与错误审计另按项目归档周期保留，此 7 天规则不授权自动删除审计数据。

客户端在 POST 断连而尚未取得 operationId 时，使用**同一键及同一规范输入**重发 POST，作为幂等查询恢复；已知 operationId 时调用 GET。它不会建立新的逻辑操作。原始错误消息或连接断开不证明受理失败，也不授权更换 snapshot、键、nonce 或签名。

从首次受理时间起，**排队、依赖读取、模型及规则执行的总期限为 120 秒**，按服务端单调时间计量。不是每次重试重新获得 120 秒。内部最多 **2 次 attempt（首次 + 1 次重试）**，仅限矩阵标记 retryable 的暂态原因，在同一 operationId/aiRequestId、相同 inputBytes、相同不可变 snapshot 下执行；每次请求预算不得超出剩余总期限。无剩余时间时产生 `REQUEST_DEADLINE_EXCEEDED`。attempt 与剩余时间须审计。

已建立操作的失败通过 `OperationEnvelope.status=error` 表达；GET 仍为 HTTP 200。错误的 `retryable=true` 描述原因分类，不使终态重新运行、不授予第三次 attempt，也不让客户端自动发新逻辑操作。受理前的 `429 RATE_LIMITED` 或暂态依赖错误可以同键同输入重传；已建立操作的两次机会用尽后，需显式处理该失败。更改任何审核事实或快照必须以新 snapshot、新 inputHash 及新幂等键发起新操作。

## 4. 可信文件、证据及外部快照

输入 `evidenceSnapshot` 由 API 拥有，固定键为 `snapshotId、evidenceVersion、createdAt、owner、deployment、documentVersions、commitments、externalSnapshots`。`owner` 固定 `api`；createdAt 为有效 UTC 秒级 `YYYY-MM-DDTHH:mm:ssZ`。API 为完整快照分配不可覆盖 ID，`evidenceVersion` 为 uint64 规范十进制 string。

不可变快照绑定 deployment（runId、instanceId、chainId、Registry）、输入 stage、registrySnapshot.projectId/procurementId/currentEvidenceHash。稳定的 evidenceVersion 命名空间为 **(deployment.instanceId, deployment.registry, procurementId, stage)**，runId、chainId、projectId 必须与该采购和实例绑定并核对；currentEvidenceHash 是快照内容，不作为版本命名空间的键。该命名空间首次创建为版本 1；文件版本/集合、绑定承诺、预算政策、供应商或比较快照版本、链证据或 canonical block 信息发生变化时，生成新快照并递增 evidenceVersion，不能覆盖旧快照，也不能因 currentEvidenceHash 改变重置为 1。链证据变化须先满足 V2 当前状态与不可变记录规则，API 不能借升版本“编辑”已承诺采购事实。模型/规则版本改变产生新报告版本；若输入未变，不必虚构文件版本变动。API 数据库具体表与事务实现仍待负责人确认。

每个 `DocumentVersion` 固定字段：

| 字段 | 类型／用途 |
| --- | --- |
| evidenceId、documentId、documentVersionId | ASCII 标识符；分别是本次证据引用、文件资源及不可覆盖文件版本 |
| version | uint64 十进制 string；不是整个 evidenceSnapshot 的版本 |
| kind | ProcurementRequest、Quote、PO、GRN、Invoice、Receipt、Photo、Inspection |
| category | 文件服务的原类别 string；必须显式映射，不能按文件名猜 kind |
| contentType | application/pdf、image/jpeg 或 image/png |
| sizeBytes | 原文件长度，整数 1～10485760 |
| contentSha256、leafKeccak256 | 同一原文件 bytes 的 SHA-256／Ethereum Keccak-256；统一 0x 小写 32-byte hex |
| pages | PDF 的可信页码列表，使用 1-based 正整数、非空、递增且不重复；非分页图像固定 null |

API 的既有 SHA256 若为 64 位无前缀 hex，适配器必须显式校验 32-byte 值并转换为候选表示，不用另一份内容重算后冒充同一版本。文件读取用授权 byte stream 或短期下载能力，能力地址不进入 immutable input 或报告；有效期与权限由文件服务控制。读取失败为技术错误，读取成功但长度或摘要不符需阻止处理与签名，不能归类为“资料未提供”。

`input.documents` 是原 EvidenceRef 数组，逐项绑定 DocumentVersion 的 evidenceId、version、kind、contentSha256、leafKeccak256，pages 必须一致。`BODY.evidenceRefs` 是这些已读取可信材料的引用，页集允许为可信 pages 的子集；Locator 非 null page 必须在对应页集中。非分页图像使用 **pages=null、Locator.page=null**。可信 DocumentVersion 的 PDF pages 是非空 1-based 允许页码列表；BODY EvidenceRef 的 PDF pages 是其子集，允许 [] 仅表示本报告没有引用该 PDF 的任何页面，此时 Locator.page 必须 null。[] 仅有 PDF 空引用这一含义，不再兼作无分页图像标记。固定排序见 Schema 描述及附录；接收方检查而不修复原数组。

`commitments` 每项固定 `evidenceRole、commitmentHash、scheme、documentVersionIds、sourceRecordId、recipeVersion`，evidenceRole 在一个快照内唯一：

| 文件／记录类别 | evidenceRole | 来源与校验 |
| --- | --- | --- |
| PO (`purchase_order`) | poHash | 链上 PO 承诺；若为原文件承诺，等于对应原文件 leafKeccak256 |
| request | requestHash | 已注册采购需求文件／manifest 承诺 |
| goods_request | goodsRequestHash | 已注册物资需求文件／manifest 承诺 |
| invoice | invoiceHash | 已注册 Invoice 文件／manifest 承诺 |
| goods_evidence | goodsHash | 已注册物资／交付证据文件或 manifest 承诺 |
| 已接受 RecipientReceipt | receiptDigest | PoGRegistryV2 已接受的 RecipientReceipt typed-data digest，非收货图片/PDF leaf hash |

阶段约束如下：

| evidenceRole | 来源与校验 |
| --- | --- |
| poHash、requestHash、goodsRequestHash | PrePurchase 已记录的三个 Registry 字段；stage 0 均必须有映射 |
| poHash、invoiceHash、goodsHash | FinalRelease 的已记录文件／聚合承诺；stage 1 均必须有映射 |
| receiptDigest | FinalRelease 的已接受 RecipientReceipt typed-data digest；stage 1 必须有映射 |

`raw_file_keccak256` 必须且仅引用一个 documentVersionId，commitmentHash 等于其 leafKeccak256，recipeVersion=null。`registered_manifest` 引用一个或多个已绑定文件版本并有非空 recipeVersion；API 必须取到双方已批准、可解析复算的 manifest recipe。这里未规定或批准新 Merkle 算法。sourceRecordId 关联真实已注册承诺及原 manifest 记录。

`recipient_receipt_digest` 仅用于 receiptDigest，documentVersionIds=[]、recipeVersion=`PoGRegistryV2/2`，sourceRecordId 解析到当前 Registry 已接受的收货记录，commitmentHash 与 registrySnapshot.receiptDigest 一致。Receipt 文件可独立作为证据引用，但它的原文件 hash **不能替换** receiptDigest。

`externalSnapshots` 每项固定 `snapshotId、provider、version、contentSha256、contentKeccak256`。vendorContext/comparisonContext 中每个非 null 快照 ID 必须解析到该可信数组，可授权取回同一原始 bytes 并复核两个摘要；null 表示未取得，不能当作“没有重复或关联交易”。只有带可信不可变 ID 和摘要的有效空数据才表示该提供方的空结果。其结构定义归属对应 provider/version，不能让 AI 修改该资源事实。旧快照和审核实际使用的原 bytes 必须可审计。

## 5. 技术错误与业务缺资料矩阵

受理前无法生成或不应生成可信操作的失败，直接返回 `ErrorEnvelope`，不伪造 operationId/aiRequestId/payloadHash。未知阶段或不可信采购标识填 null，不能从损坏 JSON 默认补 stage=0。受理后的失败保存为互斥 OperationEnvelope.error；下表 HTTP 列是直接拒绝或对应故障的语义状态，**已受理操作 GET 的 HTTP 始终为 200**。未实现的候选错误不能冒充 A1 现有错误代码。

| code | HTTP | retryable | 同 operation／同键处理 | 后续处理 |
| --- | --- | --- | --- | --- |
| INPUT_FORMAT_INVALID | 400 | false | 不受理；重复键、无效 UTF-8、孤立 surrogate、未知键、原 token/字段格式非法 | 修复合法输入后新请求；禁止以 422 泛化格式错误 |
| REQUEST_TOO_LARGE | 413 | false | 不受理；字节、深度、字符串、数组或文件长度超限 | 修复大小或结构，重新生成快照 |
| AUTHENTICATION_REQUIRED | 401 | false | 不受理，不泄露资源是否存在 | 更新服务凭证，人工处理认证 |
| FORBIDDEN | 403 | false | 不受理／拒绝读取，不启动模型或签名 | 人工核实采购和 deployment 权限 |
| RESOURCE_NOT_FOUND | 404 | false | 不受理；资源或不可变版本未注册 | 修复资源引用，生成可信快照 |
| OPERATION_NOT_FOUND | 404 | false | 仅查询，无新操作 | 用原幂等键同输入恢复 POST 或人工查明记录 |
| IDEMPOTENCY_CONFLICT | 409 | false | 保留原操作，不重新执行 | 查明调用错误；确属新输入才使用新键 |
| IDEMPOTENCY_KEY_EXPIRED | 409 | false | 命中过期键 tombstone，不复用键或重建旧操作 | 原审计保留；新业务审核使用新键，先核实旧操作已解决 |
| RATE_LIMITED | 429 | true | 受理前同键同输入可按 Retry-After 重传；不假称建立了操作 | 保留原键，避免并发重复请求 |
| DOCUMENT_READ_UNAVAILABLE | 503 | true | 已注册文件版本暂时读不到；剩余期限内同操作最多一次重试 | 用尽后修复文件服务，不能制造空材料报告 |
| DOCUMENT_HASH_MISMATCH | 422 | false | 长度/SHA256/Keccak 与可信版本不符；立即终止 | 隔离异常，人工查明内容与版本；重新验证后新快照 |
| EVIDENCE_BINDING_MISMATCH | 422 | false | 文件、commitment、stage 证据或快照 scope 不一致 | 修正可信映射，新快照；不得仅重写报告字段 |
| REGISTRY_SNAPSHOT_UNVERIFIED | 503 | true | 可信链读取/核验暂不可用；仅在原 block/证据仍可核实情况下同操作重试 | 不能从客户端 verified 值判定已核验；重组/事实变化改用 STALE |
| REGISTRY_SNAPSHOT_STALE | 409 | false | canonical block 重组、实例切换、当前证据/状态变化；终止旧操作 | 重新获取可信链与完整快照，生成新操作 |
| EXTERNAL_DATA_UNAVAILABLE | 503 | true | 已声明存在的供应商/比较快照或其 bytes 暂不可访问 | 同操作最多两次 attempt；不得把技术失败改为“无关联交易” |
| DEPENDENCY_UNAVAILABLE | 503 | true | 依赖服务暂不可用，符合已配置暂态分类 | 同操作预算内重试；无实现的占位 adapter 仍保持 unavailable |
| MODEL_TIMEOUT | 504 | true | 模型子调用超时、总期限尚有余量 | 相同快照可作剩余一次 attempt；禁止低风险兜底 |
| MODEL_FAILURE | 502 | true | 模型服务已分类为暂态执行失败 | 相同操作预算内重试；持续失败仍是 error，不能退化 rules_only 后签名 |
| MODEL_OUTPUT_INVALID | 502 | false | 模型输出不符合提取结构、字段类型或可信引用 | 人工修复模型/提取版本；不补默认 Pass/0 |
| CANONICALIZATION_FAILED | 422 | false | 服务产出的 BODY 不能按已确认 profile 编码或 bytes 比较失败 | 修复生产者/版本；不把另一套 JSON bytes 继续签名 |
| RULE_FAILURE | 500 | false | 规则引擎异常、执行不完整 | 修复并明确规则版本，人工复核 |
| SCORING_POLICY_UNCONFIRMED | 409 | false | 部署要求的有效评分政策未配置／未获确认，不能完成该生产评分请求 | 产品/API/AI 负责人确认版本，不借用 CPU demo 分数 |
| VERSION_UNSUPPORTED | 422 | false | 合法格式但 schema/profile/规则/模型/manifest/provider 版本未支持 | 显式对齐版本，重新发起对应有效输入 |
| REQUEST_DEADLINE_EXCEEDED | 504 | false | 120 秒总期限耗尽，终态，不重新计时 | 核实容量或依赖，再由调用方明确发起新操作 |
| INTERNAL_ERROR | 500 | false | 未分类服务异常，终止并保留审计 | 人工诊断；不滥用 retryable=true |

这里的 MODEL_FAILURE 仅接纳已知暂态原因；非法配置/版本走 VERSION_UNSUPPORTED，输出结构走 MODEL_OUTPUT_INVALID，未知异常走 INTERNAL_ERROR，不把所有错误统一重试。

业务上“本阶段资料尚未提供”不等于文件读取失败：允许输入的 nullable 依赖或当前缺少的业务材料产生 **completed + ReportBody(completeness=incomplete, outcome=Review, riskScoreBps=null, missingInputs 非空)**，签名 blocked。完整但未评分的候选／诊断格式可表示 complete + Review + null，永久 blocked；生产评分调用缺少已确认政策时返回 SCORING_POLICY_UNCONFIRMED，不把它补成成功评分结果。不要使用含混 `REQUIRED_EVIDENCE_MISSING` 混合上述两类失败。该 code 不在 0.2 技术错误闭集内。

PrePurchase 不要求未来 GRN、Invoice 或收货承诺；链上已记录 PO/需求是生产阶段前提。FinalRelease 在 Recipient 收货确认后进行，不要求尚未产生的 Foundation 供应商付款回单。完整且已评分的 Review/Reject 可以作为风险报告进入签名门禁，不等于合约会自动拒款；最终资金决定仍由分别对应阶段的人工审批负责。

## 6. BODY、签名与链上门禁

API 保存原 immutable input、可信 evidenceSnapshot、报告对象、精确正文 bytes、inputHash/reportHash、操作与错误审计。AI 服务只产出符合输入事实的风险解释、版本和评分，不拥有改写文件／链事实的权力。

本轮责任归属候选明确如下，待 API／AI 负责人复审：

| 组件／负责人 | 必须承担的责任 |
| --- | --- |
| AI 模型／AI 组 | 只生成风险报告，不接触任何私钥，不改写可信输入事实 |
| API／API 组 | 提供可信不可变输入；复核报告与规范 bytes；生成 operationId/aiRequestId；管理幂等、operation、deadline；链适配器读取实际部署域、状态、证据、allowlist 和 aiNonces，构造并持久化 EIP-712 typed data、assessmentId、digest 及签名请求；编排交易提交 |
| 独立 AI signing component／AI 组 | 独立控制 AI 签名密钥；复核完整 input/BODY 的 inputHash/reportHash、执行模式、评分政策、域及所有门禁后签名；不是新增 Oracle 合约 |
| Relayer／API 组 | 只提交已有合法、已授权签名，管理自身 EVM transaction nonce、txHash、receipt、事件与 canonical block；不持有 AI、Human Approver 或 Recipient 私钥 |

模型失败、资料不全、未评分、hash 不符、未知 execution mode 时，独立 AI signing component 必须 fail closed。HTTP operation completed 只表示报告产出，不代表签名、链录入或资金动作成功。

生产签名器必须：

1. 对 trusted input 与正文分别校验 Schema、原始 token、排序、上界和规范 bytes，并复算 inputHash/reportHash。
2. 核对 BODY 与 input 的 stage、projectId、procurementId、evidenceVersion、evidenceHash、inputHash、可信证据引用和允许页码，核对 projectId 对应实际链上采购。
3. 核对 BODY 与签名结构的 stage、procurementId、evidenceHash、outcome、riskScoreBps 一致；null 分数禁止转换成 uint16，完整性与缺资料门禁由链下执行。
4. 使用 EIP-712 **primaryType=AIAssessment**，固定类型字符串及 name=PoGRegistryV2、version=2，绑定实际 chainId/Registry；AssessmentInput 是 ABI 参数类型，不是签名类型名。
5. 读取当前 signer 全局 nonce，计算 assessmentId 时使用固定 V2 `abi.encode` 并排除 assessmentId 自身；不使用 encodePacked，不本地预增 nonce。
6. 验证签名与实际 signer/digest，区分 EOA/ERC1271；helper 算出 digest 不代表已签名或已允许提交。
7. 提交后检查 receipt、AIAssessmentRecorded、当前阶段 assessment 引用、nonce 成功加一及 canonical block，再认定链录入成功。

签名并发锁的唯一范围为 **(deployment namespace = (runId, instanceId, chainId, registry), verifying contract = Registry address, nonce family = `aiNonces`, AI signer address)**。四项 deployment 字段构成完整复合命名空间；registry 必须与 verifyingContract 相等，地址及 chainId 均使用规范表示。该范围同一时间最多一个未解决的签名／提交 operation；它跨采购、跨 stage，不能只按 procurementId 加锁。同一物理链与 Registry 的 runId／instanceId 别名必须先由 API 部署目录归并，共享同一 `aiNonces` 未决锁；不得通过换别名为同一实际计数器建立并行签名请求。

持久化记录的机器形状见 [SigningRequest Schema](schemas/v2/signing-request.schema.json)。它是 API 内部签名请求及审计资源，不增加 ReportBody 或固定 V2 AssessmentInput 字段，也不是本轮新增 HTTP 写链 endpoint。inputHash/reportHash 必须能解析回 API 保留的完整不可变原 bytes；Schema 约束对象形状，真实事务持久化、唯一锁与引用一致性仍由 API 和独立签名组件执行。

API 必须先以持久化事务取得上述唯一锁，再读取当前 `PoGRegistryV2.aiNonces(signer)`。进入 prepared 前，必须持久化 **signingRequestId、operationId、aiRequestId、不可变 input/BODY 引用及摘要、EIP-712 AI signer nonce、uint64 deadline、完整 typed data（domain/types/primaryType/message）、digest、signer、锁范围及 prepared 状态**；持久化失败不进入 prepared，也不调用 signer。prepared 尚未消耗链上 aiNonces，但其持久化记录与唯一锁保留该签名请求使用的值，防止两个并发请求读取同一 nonce。重启、重复调用及重试恢复原 signingRequestId/typed data/digest，不能生成第二份未决请求。

必须分别命名 **EIP-712 AI signer nonce = Registry.aiNonces(signer)** 与 **Relayer EVM transaction nonce = relayer account 的交易序号**。Relayer 单独以 (chainId, relayer address) 管理 transaction nonce；两者不是同一个计数器，不能互相代替、共同预增或以模糊的“链 nonce”表示。报告 operation 的 120 秒总期限也区别于签名结构的区块时间 deadline。

未知交易结果期间保持签名未决记录与唯一锁，先用原 txHash、receipt、事件、当前评估引用、aiNonces 及 canonical block 查明事实。API 必须在可审计地证明该请求已成功录入或已终止且不会再被提交／接受后，才释放锁；仅连接超时或本地取消不足以释放。未知断连、NonceMismatch 或签名 deadline 到期不能触发盲目 nonce+1／自动重新签名。更换 input/report、人工批准或重新发起必须基于已核实链状态，重新记录完整签名请求。

生产 `executionMode=synthetic_fixture` **永久禁止 prepared/signed**。协议字节/哈希/签名的合成测试 fixture 使用独立的测试 scope、测试目录和测试 harness，不以 SigningEnvelope 中的生产例外实现，也不隐含对共享演示链、节点或私钥的授权。模型失败后降级 rules_only 不能作为签名报告；独立纯规则生产模式默认关闭。未来启用须明确“**纯规则模式生成并签署 AI 风险报告的部署策略条件**”及已获产品确认的版本化政策；它仅生成风险证据，不批准 reserve/release，不替代 Human Approver。完整且已评分的 Review/Reject 可签名并记录：固定链上枚举为 Pass=0、Review=1、Reject=2；缺资料导致的未评分报告仍不得签名。

## 7. 机器 Schema 与检查边界

候选 Schema 位于 [schemas/v2](schemas/v2/common.schema.json)，JSON Schema draft 2020-12：

| 文件 | $id |
| --- | --- |
| common.schema.json | urn:pog:ai:0.2-candidate:common |
| input.schema.json | urn:pog:ai:0.2-candidate:input |
| report-body.schema.json | urn:pog:ai:0.2-candidate:report-body |
| error-envelope.schema.json | urn:pog:ai:0.2-candidate:error-envelope |
| signing-envelope.schema.json | urn:pog:ai:0.2-candidate:signing-envelope |
| operation.schema.json | urn:pog:ai:0.2-candidate:operation |
| signing-request.schema.json | urn:pog:ai:0.2-candidate:signing-request |

全部 $ref 按这些 $id 在本地注册，校验不需要联网获取 Schema。可用 Python jsonschema 的 Draft202012Validator.check_schema 校验每个 Schema，以 referencing.Registry/Resource 注册七文件后分别校验对应输入、BODY、签名包与操作外层；启用日期 format checker。候选示例 bundle 不是 ReportBody，需分别抽取 inputCandidate、reportCandidate 和 signingEnvelopeCandidate 再校验。

Schema 检查固定键、类型、nullable、取值范围、互斥结果、模式字段、incomplete→Review/null/missingInputs 非空、complete→missingInputs=[]、签名包三状态。**Schema 不完成以下检查**：解析前重复键/UTF-8/BOM/整数 token；uint64/uint256 string 的精确最大值；Unicode scalar；数组语义排序及 ID 唯一；跨字段绑定；政策批准；可信资源与原 bytes；Registry block/domain/evidence/nonce；生产 signing gates。上述检查必须另有实现与负例验证，不能以“Schema 全通过”宣布签名安全或端到端验收。

本轮已选定异步 202 + polling、受控服务 Bearer 认证、API 生成两类 ID、完整 inputBytes 的 SHA-256 payloadHash、7 天恢复窗口及过期键 409、120 秒总期限／最多两次 attempt、完整 tagged union。API 负责人仍须确认候选、认证 scope 映射、审计存储与事务实现、文件读取能力及 provider/manifest 版本；这些实现与后续有界测试没有由本文获批。**整体保持 CHANGE_REQUIRED／未冻结；Schema 或 OpenAPI 通过不等于 APPROVED_FOR_BOUNDED_TEST。**

