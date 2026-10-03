# PoG API / Database A0 历史需求基线

版本：A0.1；日期：2026-10-03，Asia/Hong_Kong。
范围：API、数据库、身份与角色权限，以及后续服务的接口边界。

本文保留 A0.1 的历史需求与分阶段设计，不能据此推断全部建议接口已实现。当前已发布能力见 A1/A2 文档；AI 和 payment 的实际服务格式、实现及验收需要分别核对。

### A0.1 设计约束

- 题目为FinTech #2：Recognise Value That Gets Overlooked。贡献为指定项目共同采购的捐款，Recipient受益；公平规则为全部累计捐款按比例共同承担已确认项目成本，关闭核账后的真实剩余币按比例返还原Donor。
- 采用共享一台宿主机的一套API/DB/链、三个角色页面、独立human账号/钱包、受限demo wallet adapter与本机三浏览器备用。A1只开发身份映射/权限和adapter接口，不发链交易、不实现EIP712签名；实际私有网暴露在A4再验证。
- 采用单体 FastAPI/Pydantic、SQLAlchemy/Alembic、PostgreSQL。Python、受支持 PostgreSQL 主版本及依赖需精确锁定并具备可重建的兼容验证；使用项目内可移除运行时，不依赖 Docker，不修改系统服务或全局环境。
- 文件白名单PDF/JPEG/PNG，单文件10MiB；原bytes的SHA256作内容审计，keccak256作on-chain leaf commitment，与冻结的ABI组合keccak分别存储；已引用文件版本不可覆盖。AI报告及payment证据byte schema仍须两组提供。
- uint256/金额/nonce为严格十进制整数字符串，无指数、正负号、小数、float或静默round；范围0..2^256-1；金额人类输入转6decimals由显式字符串转换函数处理。数据库NUMERIC(78,0)不能代替应用校验，必须拒绝NaN/Infinity。
- Idempotency-Key作用域为namespace(runId+instanceId)+principalId+operationKind+key；长度1..128的可打印ASCII。Validated payload只包含该action允许的语义字段，UTF8 JSON、sort_keys、紧凑separator、ensure_ascii=False、禁止NaN，金额保留规范十进制字符串；hash为SHA256。对象key顺序不影响，列表顺序影响；上传按原文件内容hash及固定metadata识别。key与operation/audit至少保留整个demo实例，不自动TTL删除。A1可先用明确mock namespace；不得把mock配置标为经RPC验证的真实链。
- 同key同payload返回原operation；异payload409；DB唯一约束与事务必须防并发重复。已入库operation不能以新attempt绕过幂等。A1无资金动作，只建立awaiting_authorization/queued等状态及mock adapter边界。
- project/procurement业务ID以实例namespace、资源类型、服务生成UUID和域标签形成独立ID；不接受请求中的任意caller覆盖身份。A1链下draft或明确fixture不能显示confirmed donation/release/payment。A2再实现具体bytes32向链提交与confirmed投影。
- A1 的 HTTP/数据库基础与 A2 的链动作分层交付；接口、迁移和发布版本分别固定。基础服务的验证不等于后续链、AI、付款或三端联调验收。

## 1. 文档与版本依据

- 团队仓库：https://github.com/Eason-NotFound/PoG。
- A0 是实现前需求基线；当前 API 已有 A1/A2 公开版本，实际路径和支持范围见对应接口及运行手册。
- A1 公开基线：`api-v0.1.0-a1` / `1844186df10854cd49ccc0886f5622e71e572d8e`。
- A2 公开基线：`api-v0.2.0-a2` / `4c9f1a40ac225d684d00b5abcf8081c43594bfcc`；当前迁移 head 为 `c31003a20004`，已发布 003 保持不变。
- 本地链接口只支持明确验证的隔离 Anvil、chainId 31337 与 loopback RPC；历史运行状态不能作为未来配置或 readiness 证据。
- M3.2 / M3.3 / M3.4 是区块链集成阶段，A0–A4 是 API 能力阶段；阶段名不表示对应运行功能已经完成。
- 现行依据：AGENTS.md、docs/STATUS.md、docs/MILESTONES.md、docs/FOUNDATION_SETTLEMENT_FLOW.md、docs/M2_V2_SPEC.md、docs/M2_V2_INTERFACE_IMPLEMENTED.md、docs/M2_V2_INTEGRATION.md、docs/M3_1_RUNBOOK.md、docs/VERSION_CONTROL.md；另核对两份 V2 合约与 packages/contract-abis/v2。
- ABI 包继续为 @pog/contracts-v2@0.2.0-m2-rc.1；其版本是冻结工件标识，不为 M3.1 归档重写。

比赛资料参考 HacKU 2026 Official Participant Handbook.pdf 和 HacKU 2026 — Problem Statements.pdf；以下为当时需求与展示设计，实际交付范围仍以对应版本为准。

## 2. 比赛事实与展示建议

手册第 2、7、9-11 页：10 月 4 日 13:00 香港时间提交并 code freeze，代码须在此之前提交仓库；提交 public GitHub/GitLab、live demo link 或 3 分钟录像，以及 pitch deck；材料公开可访问。FinTech exhibition 为当日 13:30-14:30。技术实现与原型演示各占 5/30 分，评分强调功能可靠、流程可用以及清楚证明方案运作。手册没有规定每个 booth demo 必须 3 分钟，也没有指定共享网络或钱包技术。

建议选择「现场本地可操作 + 3 分钟录像提交/备用」。公开视频、仓库和 deck 的制作/上传由团队后续安排，本组交付可录制的数据与操作流程；本轮不新增公网服务。

### 建议 booth 拓扑

| 设备/位置 | 展示与职责 | 访问范围 |
| --- | --- | --- |
| 宿主机（建议当前运行 Anvil 的电脑） | 一套 API、DB、worker/indexer、私有文件目录、Anvil | DB 与 RPC 保持本机；后续批准并验证后只向演示私有网提供应用 HTTP |
| Foundation 屏幕 | PO/Invoice、AI 风险、采购与释放/兑付/付款状态 | 登录 Foundation 会话；人工审批切换为独立 approver 会话并明确显示签名身份 |
| Recipient 屏幕 | 核对指定采购/金额/证据，确认收货 | 登录 Recipient 会话，签自己的 receipt |
| Donor 屏幕 | 模拟兑换、捐款、锁定余额、关闭退款规则、退款 claim | 原 Donor 会话与钱包；可给评委使用已分配 demo Donor 账号 |

三台电脑只连共同应用 API；其他电脑的 127.0.0.1 是它自己，不能直接拿宿主机 loopback RPC 当远程配置。建议使用同一应用 origin，减少 CORS 和 cookie 部署分歧。私有网络可用性应在 A4 前实际验证；本机三个独立浏览器会话作为弱网备用。部署范围与网络可用性需独立验证，不能由拓扑建议推断已完成部署。

### 建议签名模式

booth 默认建议使用明确标记为 demo 的受限 wallet adapter。固定会话映射固定开发角色，只允许白名单业务方法，服务端验证所属项目/采购/动作/参数，不能接受请求里任意 from、signer、destination 或通用 calldata。Foundation/Donor 的交易必须使用各自 caller；Recipient、AI、human 的业务签名必须由各自授权的签名适配器生成，relayer 仅接收并提交。AI 签名由 AI 组接口负责；人工必须逐次确认具体动作，不能由 AI 或 Foundation 自动签。

Anvil 解锁账户的 demo 模式只能提供逻辑权限隔离，不是生产密钥隔离。不同 demo 账号凭证由启动配置提供，会话不能仅信任 role 请求头；不要在前端放全组凭证。优先保留将来外部钱包签 raw transaction / typed data 的 adapter 边界；浏览器钱包模式是备选，需要额外钱包与网络联调。实际签名能力以部署配置和对应 A2 接口边界为准。

### 建议三分钟展示脚本

1. 展示同一个项目，Donor A/B 共捐 100（如 60+40），余额已锁。
2. Foundation 提交 PO，展示第一次 AI 风险及独立人工 reserve80。
3. Invoice72 / goods evidence 已到，Recipient 屏幕确认收货；展示第二次 AI 风险。
4. 用未取得足够有效人工批准的合法执行请求尝试 release，显示被阻止、规则与链资金未变化。AI Pass 本身不放款。
5. 人工签署 release，确认 Foundation 收到72；项目仍锁28。展示各步骤的 operation 与确认事件。
6. 展示模拟兑付、固定 vendor 全额模拟付款、人工核账，并清楚标为模拟。
7. Closing / 核账关闭后，两个原 Donor 分别 claim16.8和11.2。

实际演示耗时需排练。可以预先准备材料、首轮评估及多个独立场景，以便短时段展示；预先完成的步骤须显示历史确认事实，不能伪装成当场执行。连续展示使用新业务ID，避免每轮 reset。退款可作为额外可操作场景，录像用剪辑说明已完成步骤。

已申报FinTech #2。该题要求清晰贡献/受益者/公平规则，并演示争议、退出或不平衡，至少多于一个场景及规则代价。本方案保留两条可验证场景：①A捐60、B捐40、已核账成本72，余28退款为16.8/11.2；②Recipient未确认或既有放款未核账时资金/关闭退款被阻止，争议采购保持未解决。公平规则保护Donor的比例份额和Recipient收货证据，同时限制Donor随时撤回、要求Foundation完成旧供应商义务；这些限制须在捐款前展示。已验收M2不支持部分付款/减免，因此异常只能诚实显示未解决，不能声称自动调解完成。所有兑换参数是1:1零手续费demo参数，不能表述为观察到的真实费率。

## 3. 固定的业务和权限需求

R01 Foundation 创建项目；固定 Recipient、资产和人工策略。Donor 自己模拟 HKD 换 MockHKD 并捐到指定项目 Escrow，所有金额为6decimals原子整数。

R02 PO/需求 -> 第一次 AI 风险 -> 人工 reserve -> 供应商账期交货 -> Invoice/goods -> Recipient 本人签 receipt -> 第二次 AI 风险 -> 人工 release -> Foundation 获得精确 Invoice 金额 -> 模拟兑付 -> 固定 vendor 全额模拟付款 -> 人工核账。

R03 链上 FundsReleased、链下 RedemptionPending/HKDReady/SupplierPaymentPending、链上 MockPaymentConfirmed 必须分别显示，不存在只改 DB 即付款成功的通路。

R04 捐100/reserve80/Invoice72只放72给固定Foundation，项目28锁定。Active余款可用于后续合规采购；无自由提款。

R05 Closing禁新donation/procurement/reserve，旧义务可完成。Recipient receipt之后不可由Foundation取消抹债；欠款/释放资金未核账时close/refund必须被阻止。

R06 Closing return必须是真实transferFrom，增加Escrow余额但不清债。任意非零return的采购在M2保持异常未解决；不能通过partial/topup/writeoff自动settle。

R07 完成核账并获得human close approval后快照退款；最多64原Donor，按累计捐款区间比例精确分配；原钱包自己claim，无sweep、迁移或自动HKD退款。

R08 用户身份、应用role、钱包授权、链caller、业务signer分别校验。建议Foundation UI容纳human控制，但human须独立账号/钱包/签名动作。owner管理功能不纳入三个业务角色的日常mutation。

| 动作 | caller / 签名权限 |
| --- | --- |
| createProject | Foundation caller；msg.sender被合约固定为Foundation |
| createProcurement、PO、Invoice/goods、settlement、policy、Closing、unreserved cancel | 固定项目Foundation caller |
| reserved cancellation请求、returnReleasedFunds | Foundation caller；return还需Foundation allowance |
| ERC20 approve、deposit、claimRefund | 对应原Donor caller；credit与refund目的钱包绑定msg.sender |
| AI assessment、Recipient receipt、human approval提交 | 可由relayer提交，必须持有对应授权signer的合法签名 |
| executeReserve/Release/SettlementConfirmation/ReservedCancellation/Close | permissionless执行；合约仍检查状态/当前证据/策略/有效票 |
| counterpart hooks | 仅Registry/Escrow互调，不作为HTTP业务方法 |

R09 Registry/Escrow是状态及资金事实来源，DB为私有资料/确认投影。Payment组拥有模拟HKD账本与规则；API仅保存链接和调用结果。AI组拥有实际风险服务、版本与签名接口。API不得重复实现两组全部服务。

## 4. A0建议的数据与HTTP契约

所有下列路径/表名为 suggested；接口实现可以提出保持语义不变的命名修正，在实现前固定OpenAPI/迁移设计。

### 共通字段与状态

- 链ID、业务bytes32 ID、钱包/合约address、hash严格校验；uint256范围为0..2^256-1。JSON金额/nonce统一十进制整数字符串，禁止float/指数记法/静默round；action按业务约束拒绝零金额。
- 建议数据库为PostgreSQL，uint列NUMERIC(78,0)并明确范围/非负约束；应用入库前拒绝小数、NaN/Infinity。NUMERIC scale为0仍可能对小数作round，所以列类型不能代替输入校验。
- operation建议状态：awaiting_authorization、queued、submitted、confirmed、failed、requires_attention、invalidated_instance。confirmed取决于动作的预期成功证据；链操作须receipt成功、至少一个本地确认、canonical block与匹配事件。状态变动需audit，不能删除失败记录。
- 同一项目可同时有confirmed链状态和pending链下操作；query必须分别返回chainState与offChainStatus，不创建含糊的paid布尔值。

### suggested资源

| 路径 | 目的 |
| --- | --- |
| GET /health、GET /ready | 区分进程存活与deployment/DB readiness |
| GET /v2/deployment-config | 对外返回经验证的chain/instance/domain/contract/role配置；不返回私钥、文件路径或服务器凭证 |
| POST /v2/sessions、GET /v2/me、DELETE /v2/sessions/current | 身份会话、role及固定钱包授权 |
| GET/POST /v2/projects、GET /v2/projects/{id} | query与Foundation创建请求，最终success须链事件 |
| POST /v2/projects/{id}/donations | Donor approve/deposit流程的父operation与子tx，不改变caller |
| GET/POST /v2/procurements、GET /v2/procurements/{id} | procurement元资料和确认读模型 |
| POST /v2/documents、GET /v2/documents/{id}/content | 私有上传、授权读取，不用任意服务器路径 |
| POST /v2/procurements/{id}/po、invoice-goods、receipt | 不可变证据提交与Recipient签名 |
| POST /v2/signing-requests、POST /v2/signature-submissions | 构造具体typed data与验签/合法relay，限定支持的业务action |
| POST /v2/ai-assessments、POST /v2/approvals/{action} | 实际AI报告签名与人工action票 |
| POST /v2/procurements/{id}/reserve、release、confirm-mock-payment | 满足合约条件的execute操作；不以DB状态绕过 |
| POST /v2/projects/{id}/closing、finalize-close、refund-claims | Closing、human close执行、原Donor退款 |
| GET /v2/operations/{id}、GET /v2/projects/{id}/events | 持久化状态、失败原因与canonical确认记录 |

兑换/付款路径沿用现行integration proposed资源，在A3与Payment组明确委托调用和证据后再固定；不是上述路径已经运行。

### suggested数据表

- users、sessions、roles、wallet_authorizations：固定demo用户、session expiry、角色/钱包绑定；钱包变更不修改已存在链上Foundation/Recipient。
- deployment_instances：schemaVersion/runId/instanceId/chainId/genesisHash/deployment fingerprints/activation状态。
- projects、procurements、project_approval_policies：chain projection、固定actor/金额/enum/epoch及confirmed policy calldata与events。
- documents、document_versions：随机storage key、所属采购/类别/上传者/版本/大小/MIME/hash/时间；原文私有；已引用版本不可覆盖。
- risk_reports、receipt_proofs、approval_records：模型/报告schema版本、stage、report/evidence hash、signer、nonce/deadline/epoch、digest与提交关联；区分已取得签名、链已接收、动作已执行。
- operations、operation_steps、chain_transactions：幂等key、authenticated principal、payload/calldata hash、签名授权引用、txnonce、txHash、receipt、blockHash、预期事件、attempt与终止原因。
- chain_events、indexer_cursors：instance+chain+contract+txHash+logIndex+blockHash、canonical标记、可恢复cursor。
- audit_logs：actor、action、资源、时间、operation/tx/证据引用与结果；敏感字段脱敏。
- payment_operation_links：Payment组operationId、project/procurement/Foundation/vendor/amount/release/redemption tx引用；不另造HKD账本。

### 幂等、文件与错误

- 每个mutation必须持久化Idempotency-Key；建议唯一范围为instance+authenticated principal+operation kind+key。同key同validated payload返回原operation，异payload409。关联AI/payment操作时透传固定operationId。
- approve/deposit是不同交易；一个父operation可以跟踪多个step，已确认approve后失败的deposit不得造成第二次donation。未知提交结果进入requires_attention并核对原tx，不能直接重发金额动作。
- signer合约nonce是全局signer nonce，与Ethereum交易nonce分开，不能按procurement递增。并发签名时持久化并发保护；重新读取nonce/assessment/epoch/deadline，失效后重新取得用户签名。
- 建议文件白名单为PDF、JPEG、PNG，单文件10MiB（待确认）；扩展名/MIME/内容检测、大小限制、路径隔离及授权读取均测试。文件metadata可存SHA256；on-chain leaf commitment建议keccak256原始bytes，并与既定ABI组合hash分列。AI reportHash/付款证据bytes规范须与各组固定后再实现，不自行选JSON序列化。
- 建议HTTP错误：400格式；401无会话；403越权；404未知资源；409幂等/状态/nonce/epoch冲突；422具体业务/签名/证据规则不满足；503依赖不可用。每个错误有stable code、operationId（如已建立）、可操作说明；不暴露private evidence或keys。
- 202只表示排队/待处理，未获得签名与链确认不能返回成功付款。HTTP错误与链上revert须精确映射，并保留原始revert标识供审阅。

## 5. Chain adapter固定约束

C01 RPC严格loopback、chainId31337。读取实际manifest的nested chain字段：chain.chainId、chain.rpcUrl、chain.genesisHash、chain.instanceId；top-level runId/schemaVersion；contracts/roles/version/transactions结构依实际文件，不猜平面字段。

C02 同宿主机启动可复用原local-chain.py verify做完整门禁；工具依赖state、PID身份、Foundry/编译artifact/Git tag/baseline，不把它当每请求通用HTTP adapter。readiness错误时禁止mutation，不自动up/reset。原脚本虽然不发交易，仍会使用运行锁/目录，不能声称文件系统零动作。

C03 三份ABI与指纹、canonical部署receipt、运行代码/immutable验证、Registry-Escrow绑定、EIP712域、asset6decimals及AI signer均须核对。不要把带部署immutable的runtime直接与未经处理的编译模板全hash比较。

C04 Registry EIP712名PoGRegistryV2，Escrow名ProcurementEscrowV2，version2，chainId与verifyingContract精确。type/order/uint宽度、enum ordinals、keccak(abi.encode(...))和termsHash/evidenceHash/assessmentId按冻结interface。测试跨语言digest与合约helper一致。

C05 当前nonce、policyEpoch、assessment/evidence及deadline在签名前读取并展示。human票是最终资金决策；AI credential和Foundation上传不能替代。Receipt accepted后其历史事实不因deadline经过而撤销。

C06 每个chain mutation保存receipt/blockHash和匹配事件；indexer dedupe/restart/rebuild、canonical回退以及policy approver confirmed calldata必须实现。只有txHash或GET里一个预期state都不足以判定该operation成功。

C07 instance变化时隔离新业务ID/DB projection，停用旧签名与pending tx/payment记录，保留旧audit。instance/run不是EIP712字段，换manifest不是合约防重放。API不提供通用reset endpoint；受控reset另行批准并由运行人员执行。

C08 MockHKD公开mint、无burn；初始faucet不能当兑换成功。所有支付都明确模拟，禁止真实银行/真实稳定币/公网链。

## 6. 阶段任务与验收

| 阶段 | 做什么 / 交付 | 必须展示的验收证据 | 依赖 |
| --- | --- | --- | --- |
| A0 | 本稿、caller矩阵、suggested API/DB契约、typed-data与hash映射、test matrix、决策表 | V2源码/ABI一致性；AI/payment边界逐项确定后才声明对应接口冻结 | 现行规范、实际模块接口与版本 |
| A1 | HTTP基础、OpenAPI、schema验证、会话/role-wallet、DB迁移、文件/audit、operation框架、env样例/启动 | 空库迁移、重启保留、会话越权、任意caller/role伪造拒绝、uint精度/范围、文件大小/MIME/hash/版本、幂等同异payload、基础query | 明确范围授权、工具与依赖锁版本 |
| A2 | deployment guard、chain adapter、真实正确caller交易、typed data/验签、worker确认/indexer/query、实例隔离 | Foundation创建项目；原Donor approve/deposit/credit；至少一项合法签名提交；nonce/deadline/domain错误测试；canonical/event确认；restart恢复；隔离节点instance变化停用旧缓存；最小M3.2-ready全部证据 | 已验收A1、本阶段范围授权、已固定V2接口及wallet能力 |
| A3 | AI/payment独立adapter、operation关联、实际服务联调 | exact report/evidence/signature绑定；repeat exchange/payment只记一次；服务失败/超时恢复；Foundation72/vendor72/项目28核对；实际AI与mock标签分明 | 已验收A2、本阶段范围授权、实际AI/payment服务与证据规范 |
| A4 | 三端、私有网和本机备用、三分钟排练、多轮场景、冷启动/故障/关闭退款 | 一套共同事实，正确角色，成功及受阻两条流程，余额/事件/审计一致，restore/restart/受控instance更换，录制可复现 | 已验收A3、本阶段范围授权、frontend及隔离部署/网络条件 |

验证应给出准确 commit、变更范围、运行命令、passed/skipped、隔离条件、未实现项和回退版本；声明 test passed 不能替代对应证据或独立验收。

最小M3.2-ready不等待完整AI/payment/UI：可复现API启动、health/readiness/config实例校验、已迁移DB、正确Foundation创建和Donor approve/deposit、nonce/deadline typed data及合法提交、持久化幂等operation、canonical预期事件query/错误映射、restart恢复和reset-instance隔离测试。

建议单体Python HTTP服务 + SQLAlchemy/Alembic + PostgreSQL + web3.py + pytest，持久化operations驱动轻量worker/indexer，私有本地文件目录。FastAPI提供OpenAPI与security集成；PostgreSQL numeric支持精确数值，但应用要拒绝scale coercion。依赖具体版本、Python版本、PostgreSQL获取方式按可重建的兼容验证固定；项目运行方式不依赖Docker或全局系统服务。参考官方文档：https://fastapi.tiangolo.com/tutorial/security/ ，https://www.postgresql.org/docs/current/datatype-numeric.html ，https://web3py.readthedocs.io/en/stable/transactions.html 。

已发布 Solidity、ABI 和标签保持不可变版本身份。私有材料、数据库、钱包文件和密钥不属于公开仓库内容；公开接口示例不能包含真实凭证或敏感证据。

## 7. 设计选择与实现边界

| 项 | 状态 / 本稿建议 |
| --- | --- |
| API/数据库需求基线 | A0历史设计；当前实现范围见A1/A2公开文档 |
| 现行业务/合约/假钱边界 | 已确定，按本稿R/C条款保持 |
| 一台host共享API/DB/Anvil，三角色页面，本机三会话备用 | 采用；A1 localhost，私有网络在A4实测 |
| 受限demo wallet adapter；独立human会话/签名；外部钱包为备选 | 采用；A1只建立权限/接口，签名和交易在A2 |
| 单体Python FastAPI/PostgreSQL；确切依赖/运行方式 | 采用；使用项目内运行时、exact lock及可重建的兼容验证 |
| PDF/JPEG/PNG，10MiB；leaf keccak原bytes+audit SHA256 | 固定；AI/payment报告bytes规范仍单独确认 |
| 题目/申报情况 | FinTech第二题 |
| AI/payment实际接口 | 按模块具体版本单独对接，不根据A1基础或A2链演示推断已完成 |
| 文档状态 | A0.1为历史需求基线；A1/A2具体能力见对应版本文档 |
| 验证边界 | 每阶段需要准确版本的验证，历史计划或基础接口通过不代表后续功能或生产验收 |
