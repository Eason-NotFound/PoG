# PoG API / Database A2 范围与接口要求

版本 A2.1，2026-10-03。本文描述本地链 API 的能力、权限和验证要求；实际 HTTP 路径止于 Recipient `ReceiptConfirmed`，不包含真实 AI、付款或后续资金动作。

## 标准账号与权限

Foundation=`foundation`；Recipient=`recipient`；Donor=`donor`；Human Approver 及页面维护使用用户名 `admin`。历史 `foundation-demo` / `recipient-demo` / `donor-demo` / `human-demo` 示例不作为对外标准账号，不另建 Human Approver 登录入口。

`admin` 仅为应用账号名，`human_approver` 角色及其与独立 `manifest.humanApprover` 钱包的绑定保持不变；不能因此赋予链 owner、Foundation、Recipient 或任意资金权限。A2 不提供页面维护 CRUD 或通用用户、密码、钱包管理接口。账号约定见 [ACCOUNT_PREFERENCES.md](ACCOUNT_PREFERENCES.md)。

既有 demo 账号只能通过显式、受控、幂等 rename 迁移为标准用户名，保留 user ID、密码 hash、role-wallet、项目/文件/审批/audit 关联；不静默重写角色、钱包或密码，不覆盖或合并目标用户，冲突即拒绝。AI fixture 和第二 Donor 是明确的技术账号，不替代四个标准入口。历史发布标签中的示例保持不变。

## 1. 版本基线

- A1：`api-v0.1.0-a1`，commit `1844186df10854cd49ccc0886f5622e71e572d8e`，源候选 `6b7739448219e969ad402d6028ad952ba7e88e33`。
- A2：`api-v0.2.0-a2`，commit `4c9f1a40ac225d684d00b5abcf8081c43594bfcc`。数据库迁移 head 为 `c31003a20004`；已发布 `c31003a20003` 保持原样，后续迁移必须追加。
- 业务与资金规则以已发布 V2 Solidity、ABI 和对应规范为准；文档清理不改变这些工件。

## 2. 本轮范围和明确不实现项

A2 实现：部署门禁、受限 chain adapter、正确 caller、三类 EIP-712 typed data/验签、持久化交易 worker、receipt/event 确认、indexer/read model、重启/重组恢复与实例隔离。

必须有真实本地链端到端路径：

1. Foundation 链下 draft → `createProject`，固定 Recipient/MockHKD/独立 human approver，1-of-1。
2. 原 Donor 自己 `approve(exact amount)` → `deposit(projectId,amount)`，Escrow `Donated` + donorCredit/ledger 核对；不同 Donor 的测试不能借用一个 relayer 来假充本人。
3. Foundation draft procurement → `createProcurement` → `recordPurchaseOrder`。
4. 明确标注的开发 synthetic AI PRE fixture，由独立 aiSigner 签名并提交 `submitAIAssessment`；不是实际 AI 推理，不替代 human。
5. 独立 human approver 看当前 PRE/terms 后签 Reserve，`submitReserveApproval`；再独立执行 `executeReserve`，仅 `BudgetReserved` 成功确认可投影 reserved。
6. Foundation `recordInvoiceAndGoods`，invoiceAmount <= reservedAmount；原 Recipient 签并提交 `submitRecipientReceipt`，确认 `RecipientReceiptAccepted` 和 stored receiptDigest。

本轮停止在 Recipient ReceiptConfirmed。不得实现真实/模拟兑换账本、AI 模型/服务、Payment 服务、释放/结算执行接口、Closing/refund 执行接口、前端、LAN/公网部署或真实资产。全部 V2 enum/event 在 read model 可准确解码，但没有 A2 写入口的动作必须明确 unsupported，不能假成功。AI/Payment adapters 继续 Unavailable；不得把 synthetic fixture 宣称实际服务联调。

## 3. 最小 HTTP 接口与兼容性

保留 A1 原 `POST /v2/projects`、`POST /v2/procurements` 和文件接口：只建 off_chain_draft，不自动发送旧 draft。新增显式 chain operation kind，HTTP 202 仅 queued。

| 接口 | 会话身份 / 固定业务输入 | 行为 |
| --- | --- | --- |
| POST /v2/projects/{id}/chain/create | 所属 Foundation；空对象 | 从 draft/role-wallet 建 createProject；caller=Foundation；asset/recipient/approver 从核验配置及绑定取得，不接受客户端 caller/asset/policy 覆盖 |
| POST /v2/projects/{id}/donations | Donor；amountAtomic | 两步父 operation：MockHKD approve Escrow exact amount，然后该 Donor deposit |
| POST /v2/procurements/{id}/chain/create | 所属 Foundation；空对象 | 链上 parent project 已确认；固定 vendor/budgetCap/businessId |
| POST /v2/procurements/{id}/chain/purchase-order | 所属 Foundation；poDocumentVersionId、requestDocumentVersionId、goodsRequestDocumentVersionId | 同采购、正确类别的不可变私有版本 raw keccak；对应三个 hash；引用后不可换 bytes |
| POST /v2/procurements/{id}/chain/invoice-and-goods | 所属 Foundation；invoiceDocumentVersionId、goodsDocumentVersionId、invoiceAmountAtomic | 不超实际 reserved，固定发票/货品 evidence leaf |
| POST /v2/procurements/{id}/signing-requests | kind=ai_pre / reserve / receipt；见下文 | 构造并保存完整 typed data、digest、实际 nonce/deadline/epoch/证据；不接受自由 calldata 或 typed message |
| GET /v2/signing-requests/{id} | 请求的同一 signer 身份；必要项目成员按脱敏规则 | 展示 what is being signed、namespace、状态；不暴露他人 raw signature/私有 evidence |
| POST /v2/signing-requests/{id}/sign-demo | 请求的同一 signer；confirm=true | 仅显式 local-unlocked demo 模式 eth_signTypedData_v4；记录授权；不会自动签其他人，不等于链确认 |
| POST /v2/signing-requests/{id}/submit | 请求的同一 signer；signature | 校验已冻结请求 + EOA signature + 当前状态；排队对应 AI PRE/Reserve vote/Recipient receipt；typed data 不由请求覆盖 |
| POST /v2/procurements/{id}/chain/reserve | 所属 Foundation 或该项目 human；reserveAmountAtomic | executeReserve，必须已有对应金额/assessment/epoch 的阈值签名票；执行权限不是批准权限 |
| GET /v2/projects/{id}/ledger | 已授权项目成员 / Donor 的公开脱敏项目范围 | ledger/current caller donorCredit 等准确 atomic strings；不向 Donor 曝光其他个人捐款/私有采购资料 |
| GET /v2/operations/{id}、GET 项目/采购 | 原授权规则 | 显示独立 operation/step/tx 确认事实、合约 enum、canonical source，不硬编码 chainVerified=false，也不因有 txHash=true |

新增 schema extra=forbid。每项 mutation 包括签名请求/签名/提交均有持久化 Idempotency-Key；客户端不能传 from/caller/role/rpc/contract/chainId/nonce/typed-message 来改变授权。现有文档类别可作兼容增补，但只在明确必要时添加，不能删/改旧类别解释。

签名请求：ai_pre 仅新 dev-only `service_ai_fixture` 独立会话身份；reserve 仅项目确认 policy 内该 human；receipt 仅原 Recipient，receiptEvidenceDocumentVersionId 必须由其上传且属于本采购。reserve 输入 reserveAmountAtomic；ai_pre 固定可复现 fixture 版本和 report bytes/hash，报告显式 synthetic；receipt 从链上当前 PO/invoice/goods/金额/parties 及其版本引用构造。默认 deadline 使用链 timestamp+300秒，允许 TTL 60..3600 秒，deadline uint64 严格检查。

默认 A1 模式 chain disabled，不伪造 readiness。A2 启用需明确 opt-in、核验 manifest、wallet 模式及运行路径；真实 RPC 不可由 HTTP 输入指定。缺依赖/失配时准确 503；A1 登录、文件及 draft 行为仍可用且未发送资金动作。

## 4. 本地钱包、部署门禁及签名

- RPC 仅 http loopback、chainId31337、明确已管理 Anvil。API/Postgres 均 loopback，不自动 reset/up/停用他人进程。生产/公网/真实币完全禁止。
- 默认操作人员运行一次现有 local-chain.py verify 完整门禁；adapter 还须验证 manifest 指纹、三份 ABI/artifact、canonical 部署 receipts、runtime/immutables、Registry/Escrow 相互绑定、EIP-712 域、MockHKD decimals=6、独立 roles、AI allowlist。不得未经 immutable 处理比较 runtime template hash。可复用现有工具，不修改 accepted M3.1 文件。
- 每次签名构造/提交及交易发送前，重新核验 chainId/genesis/Anvil instanceId 与 active namespace；排队期间实例变化也须停用。签名 nonce、deadline、epoch、assessment/evidence 在签名前及提交前 fresh 读取。
- Foundation / Recipient / humanApprover / donorA、donorB / aiSigner / relayer 各自实际 manifest role；不可把账号统一成 owner/relayer。A1 既有 wallet/password 不能静默改写；新的 chain-demo seed 是显式 opt-in，有差异即 fail closed，操作人员可创建新的独立 demo users。AI fixture 密码单独配置，最少12字符且拒绝占位值，不提供 public owner/管理入口。
- 每次会话授权重新要求唯一 active role-wallet，不能只在 login 检查后取首条 join；已有 token 的映射若变得歧义立即拒绝。AI fixture 只需公开链上 PO commitment，不为本轮授予私有文件/采购浏览权限；当前 A1 只 seed 四角色，新 AI/第二 Donor provision 明示 opt-in。
- `eth_sendTransaction` 仅固定角色的受限业务方法；解锁账户不是安全钱包，只适用同宿主 demo。人和 Recipient 的 demo 签名必须本人会话、confirm=true、记录此次授权；Foundation/AI 不能代签资金票或收货。
- 不记录/提交 Anvil private keys、mnemonic、wallet 文件、密码或 token。签名是敏感授权资料，只存私有 DB、限制查询/日志；合成 fixture 不含私人资料。
- 本轮实现 EOA 65-byte signature。ERC1271/external hardware wallet 不冒充支持，返回明确 unsupported 并列为限制；保持合约已有支持不变。eth-account 仅在严格 schema 验证后编码，不能依赖其隐式 coercion 充当输入校验。
- 两个域名称 `PoGRegistryV2` / `ProcurementEscrowV2`，version `2`、chainId、verifyingContract 严格。字段顺序/uint宽度/enum ordinals 按 accepted ABI/interface 原样，无新增 runId 域字段。
- 完整实现 AI Assessment、HumanIntent、RecipientReceipt 编码与 digest；assessmentId/evidence/所有五类 Human termsHash 的 cross-language vector 与现有 helper 一致。A2 HTTP 只启用 PRE/Reserve/Receipt；其他 action 的 helper 单元向量不构成执行授权。
- 三种业务 nonce family 为 Registry aiNonces、Registry recipientNonces、Escrow humanNonces；各自 contract+signer 全局，不能按采购递增。与 Ethereum txnonce 分表/约束。policyEpoch 全 uint32，deadline 全 uint64，nonce 全 uint256，不能用 signed32/signed64 或 float 截断。
- runId/instanceId 不在 EIP-712 域中。重置后的防护是 API 业务 namespace/新 businessId/停用旧请求，不宣称链上密码学防重放。已接受 receipt 不因其签名 deadline 过期变为无效历史事实。

## 5. 持久化 worker、确认及恢复

- 新 Alembic migration，不改 A1 migration；从已有 A1 数据库原位升级、保留数据。扩展 chain状态/schema、operation/steps、chain_transactions、signature records、deployment snapshots、confirmed policy、ledger projection 和 cursor，准确 CHECK/unique/FK；旧 A1 操作永不自动入 queue。
- chain adapter 用明确 DTO/Protocol，返回 prepared envelope/submission/receipt/events，不保持 submit(dict)->None 假接口。受限 action 映射，绝无通用 calldata endpoint。
- 明确 CLI worker `--once` 和持续运行模式、indexer一次/持续/rebuild命令。网络 timeout 有上限。HTTP只持久化授权/队列，不能持有 DB transaction 等待链确认。
- 按 namespace+caller 串行 EVM nonce 分配，先保存确定的 from/to/chainId/nonce/data/value/envelope及hash，再发送；worker lease/锁防多worker重复发送。同 signer 同 nonce family 的请求也需持久化并发保护；不同时发两个有效同 nonce 授权。
- Donation approve 和 deposit 独立步骤。approve 已确认后失败/重启只恢复原未完 deposit，不新建捐款或重复金额动作。approve 不用 MaxUint，不 mint/faucet 冒充兑换。
- RPC 接受后响应丢失、发出后 DB 写失败属于 unknown outcome。按原 caller+nonce+to+data+value 从 pending/canonical tx/receipt 核对；能证实才绑定原 tx，不能证实就 requires_attention，绝不能换 nonce 盲重发。Idempotency-Key 不等于链上 exactly-once。
- confirmed 必须 receipt.status=1、至少1个本地确认（含 receipt block）、blockHash canonical、实际 contract/方法/调用者/资源/金额对应的预期事件且与 getter/ledger 相符。有 txHash、成功 receipt 没有事件、普通 ERC20 Transfer、DB写入或 GET某状态都不足以证明此 operation 成功。revert 记录稳定错误和原 custom error，不把超时伪装失败后重试。
- reserve vote 与 reserve execution 是不同 operation/事件；签名已取得、链已接受、资金动作已执行分开。AI Pass 不能自动 reserve。FundsReleasedToFoundation / MockPaymentConfirmed 按原名解释，不造 Paid boolean。
- indexer 按 blockNumber/transactionIndex/logIndex 应用；同 namespace+contract+txHash+logIndex+blockHash 去重。在一个 DB transaction 中落 events、projection、cursor；confirmed approvers 从创建/更新 policy 的 canonical calldata 得到并验证 isApprover，不能只从不含名单的事件猜。
- checkpoint blockHash 不 canonical 时找共同祖先，旧 events 标 noncanonical，相关确认重新核验，ledger/credit/state/policy 从 canonical 事实重建；不可只退 cursor 不撤旧余额。rebuild 是读链/重建本组 projection，不发交易、不 reset链。
- instance/run 改变时原子停用旧 namespace、pending queue/lease/signatures，保留旧 audit/confirmed历史；新实例 fresh businessIds、资源/query/cache 隔离。禁止 API reset endpoint、自动迁移旧钱/业务或删除历史记录。
- 私有 evidence/typed材料只对必要成员可见；公开 Donor query/events 不能成为绕过 A1 文件/采购权限的侧门。现有文件哈希/不可变版本/Audit/幂等保证必须回归。

## 6. 核验环境及最低测试

使用实际 PostgreSQL + pinned Foundry1.8.4/Anvil、chain31337、自动选择隔离 loopback port 与独立 owned state。不得交易、reset、stop 现有默认8545，不触碰其他组 chain/DB/API；测试自己进程完成后安全清理。不得承诺 stopping 能恢复 Anvil 本身链状态；API/worker重启测试保持同一测试链运行。

锁定兼容依赖（Python/SDK/SQL/crypto）及 transitive closure，macOS/Linux可重建，不改系统环境或 vendor。允许本组 project-local runtime/私有DB安装。API CI 增加必需隔离链测试环境，action/镜像固定；既有 Blockchain/Local-chain workflows 不改 accepted 技术语义。不跳过测试或调用真实 AI/Payment 网络。

必须提交实际运行命令、计数、耗时、skip=0 和结果：

1. A1 原78 tests 全部回归；迁移空库/原A1 populated库升级；constraints 包括uint32/64/256边界、签名/tx非float、A1旧draft不入queue。
2. HTTP/auth/role/privacy：错误role/caller/asset/签名人拒绝；跨资源拒绝；Foundation不能AI/human/Recipient代签；Donor脱敏 ledger/operation/event；关闭demo-sign模式拒绝。
3. 真实路径第2节，来自各自实际 caller；余额/allowance/donorCredit/ledger、state、typed digest、receipt、事件和 confirmed policy 全核对；AI synthetic 标识明显。
4. 三类签名 helper/vector；错误 domain/name/version/chain/address、字段顺序/证据、nonce、过期deadline、epoch/assessment、wrong signer及 replay；同 signer 跨采购 nonce竞争与 AI/Recipient family隔离。其他 Human terms helpers 用隔离链准备合法 fixture 状态验证，不新增HTTP执行路径。
5. 并发幂等、同caller两个op、两worker抢一步、两Donor各自捐款；approve成功后deposit失败/重启；RPC已接受响应丢失、receipt后DB写失败，证明最终credit最多一次，unknown无新nonce盲发。
6. status0 receipt、缺少/不匹配预期事件不confirmed；canonical block核验；重复日志、cursor提交前异常、worker/indexer进程重启，projection/cursor幂等。
7. 隔离Anvil snapshot/revert + 替代分支测试reorg，确认后旧credit/状态回退，canonical重建一致；不得在默认8545做此测试。
8. 隔离实例 reset/替换，复用 chainId/address 时旧pending/签名禁止发送或API重放、query隔离；不自动删除audit。manifest/code/ABI/binding/role/instance/chainId错误及RPC unavailable门禁失败，无 mutation。
9. `bash scripts/check-blockchain.sh` 与既有 local-chain检查回归；全 tracked diff `git diff --check <accepted-base> HEAD`，无 secrets/private state/硬编码测试 privatekeys；PR上 API/Blockchain/Local-chain CI 在 exact candidate SHA通过。

## 7. 实现与验证边界

既有 ABI、角色、签名、hash 和 manifest 是 A2 链接入依据。实际 AI report schema/bytes 对接及 Payment operation/ledger 接口需要各自版本的实现和验证，不能根据本地 synthetic 演示推断已接入。

验证记录应绑定具体 commit、接口/OpenAPI、迁移、命令、passed/skipped、隔离目标、CI 及已知限制。技术验证不等于用户里程碑验收、生产可用或全链验收；运行与恢复说明见 [A2_RUNBOOK.md](A2_RUNBOOK.md)。

技术参考（主规范仍以本仓库冻结版本为准）：

- https://eips.ethereum.org/EIPS/eip-712
- https://eth-account.readthedocs.io/en/stable/eth_account.html
- https://web3py.readthedocs.io/en/stable/web3.eth.html
