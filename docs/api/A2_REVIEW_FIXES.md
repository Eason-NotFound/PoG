# A2 可靠性与验证指南

本文对应公开 API A2 基线 `api-v0.2.0-a2` / `4c9f1a40ac225d684d00b5abcf8081c43594bfcc`，说明权限、恢复、迁移和测试边界。
已发布 003 迁移保持不变；新增保护使用 successor 004，`down_revision=c31003a20003`。
API 运行逻辑、历史 payload hash、Solidity/ABI 及已发布标签不因本文说明而改变。

## 可靠性边界与测试入口

下列路径均相对仓库根目录。测试入口用于复现对应保护；存在测试文件不等于当前准确版本已运行通过，也不代替独立产品验收。

| 审查项 | 修正边界 | 对应测试入口 |
| --- | --- | --- |
| R1 幂等资源绑定与重放 | validated payload 绑定资源 ID/business ID 和正文；同 key 异目标 409，同目标已推进状态仍返回原持久结果。重放仍校验当前身份、角色、钱包、资源归属和 namespace；兼容旧 body-only hash 时必须由持久 target 独立证明同一资源。 | `services/api/tests/test_a2_route_recovery.py`：资源 mutation、旧 hash、当前权限及离线重放测试。 |
| R2 授权续期与 nonce-family | advisory lock 和 partial unique index 保证 live reservation 并发唯一。已过期/材料失效且未提交的历史可退役后用新显式 key、新 deadline 重建，不删除历史、不复用旧签名。PRE 续评允许 accepted V2 的 PORecorded/PreAssessed/ReserveApprovalPending；人票可在 PreAssessed/ReserveApprovalPending 续票，仍校验当前 nonce、assessment、terms、policy 和期限。已排队/结果未知不因到期释放。 | `services/api/tests/test_a2_signing_renewal.py`、`services/api/tests/test_a2_database_bounds.py`。 |
| R3 canonical 投影与 reorg | 普通 indexer `--once` 即使 tip 不增长也复验 checkpoint/投影。短链与同高度替代分支撤销 orphaned confirmation，重建 ledger、Donor credit、采购 state/commitments/source 和 policy。event、完整历史/latest 投影和 cursor 原子提交；policy approvers 从 canonical transaction calldata 验证，不靠事件猜名单。RPC 不确定时回滚本轮、保留可恢复状态，不当成确定 reorg。 | `services/api/tests/test_a2_indexer_recovery.py`：外部事件、shorter/same-height reorg、policy calldata、并发、原子回滚和 RPC timeout。 |
| R4 accepted 部署门禁 | 启动调用原 accepted `scripts/local-chain.py verify`，锚定 schema/version、工具/构造 artifact、canonical creation/bootstrap、owner、domain、角色映射等。缓存仅复用完全相同 manifest/artifact 指纹的启动验证；fresh chain/genesis/Anvil instance、runtime、receipt、authority 检查不跳过。四笔 bootstrap 的 receipt、caller、to、calldata 每次 fresh 校验，不比较会合法变化的当前 Donor balance。RPC 仅精确 loopback HTTP，无 userinfo/path/query/fragment，禁止 redirect/proxy 逃逸。read RPC 超时为 `ChainUnavailable`，不能冒充 missing receipt；单 call timeout 5 秒，provider retries 显式关闭，send 不自动重试。 | `services/api/tests/test_a2_deployment_gate.py`；真实 Anvil 和 accepted verifier 集成结果另行记录，mock 不能代替它。 |
| R5 Receipt 证据来源 | 除采购和 `receipt_evidence` category 外，必须由该采购 designated Recipient 本人上传版本；同采购其他人上传不能供其签署使用，原私有读取权限保留。 | `services/api/tests/test_a2_signing_renewal.py::test_receipt_evidence_requires_this_recipient_as_uploader`。 |
| R6 历史 receipt 校验 | canonical receipt 确认使用其 block-number-pinned getter，不要求 latest 仍处于刚执行的旧 state。较新的 canonical 投影单独推进；历史 getter/RPC timeout 保持 submitted 可恢复，不永久置 attention 或重发。 | `services/api/tests/test_a2_indexer_recovery.py`：delayed confirmation、历史 getter timeout，以及确认期间 reorg/uncertainty；gateway `call(..., block_identifier=...)`。 |
| R7 worker caller/nonce 恢复 | caller 串行 reservation；durable envelope/lease 下多 worker 不重复分配发送。`ChainNotBroadcast` 只代表 verify/estimate 等确定未尝试发送；其 attempt/audit 保留并释放 EVM nonce reservation。发送后的断连/超时/响应丢失为 ambiguous，按完整 caller/nonce/to/data/value/envelope 匹配 reconcile，不能盲发新 nonce。unknown caller 被保护，其他 caller 可继续。 | `services/api/tests/test_a2_worker_recovery.py`、`services/api/tests/test_a2_deployment_gate.py::test_send_distinguishes_proven_not_broadcast_from_response_loss`。 |
| R8 PostgreSQL 不变量 | 追加 004；reserved/invoice、ledger 四种金额、Donor credit 拒绝负数、uint256 max+1、NaN。signing nonce/deadline 为 canonical 十进制文本及 uint256/uint64 上界，拒绝 `00`、fraction、overflow；policy epoch 受 uint32 约束。PG 直接负测、full max roundtrip、nonce-family 并发和旧 A1/published003 populated upgrade 均有独立场景。 | `services/api/tests/test_a2_database_bounds.py`、`services/api/tests/test_a2_published003_upgrade.py`、`services/api/tests/test_contracts_and_migrations.py::test_empty_migration_downgrade_reupgrade_and_ready_gate`。 |
| R9 测试目标安全 | unit 和两个 live 测试共用 `pog_api.test_database`；在 build engine/connect 前校验专用测试库 URL、角色、literal loopback/port、managed marker 或显式 CI gate。拒绝业务库、远端、错角色、DSN/query/fragment、libpq override、缺失/错误/symlink marker。builder 前再次校验，不在真实 DB 上复现拒绝路径。 | `services/api/tests/test_test_database_guard.py`；`services/api/tests/conftest.py` 与 `services/api/live_tests/` 共用 guard。 |
| R10 EOA 签名与当前 signer | 严格 65-byte hex、合法 r/s、low-s、v=27/28；畸形/零/错误 signer 稳定 422，不排队或广播。sign/submit 和 replay 均核对当前 principal、角色、active 唯一 wallet 与原 signer/项目指定身份，钱包失活/替换/歧义拒绝。 | `services/api/tests/test_a2_route_recovery.py`、`services/api/tests/test_a2_security_and_typed_data.py`。 |
| R11 fresh readiness 与 A1 snapshot | `/ready` fresh 验链；RPC 停止/reset/manifest 失效返回 503，不启动/reset/migrate chain。A1 draft/file 读取只使用精确持久 deployment snapshot，启动后和重启后行为一致，不把 snapshot 冒充当前 chain-verified。A1 offchain available 与 A2 namespace unavailable 明确区分；ChainUnavailable 按依赖错误返回，不泄漏 raw exception。 | `services/api/tests/test_a2_route_recovery.py`：fresh readiness/offchain private file、inactive/changed snapshot；旧 readiness/migration 测试。 |
| R12 AI outcome 非资金 veto | 移除隐藏的 AI Pass 硬门槛。Review/Reject 是风险证据，真实性、采购/证据关联、允许 AI signer、期限和独立人工阈值仍必须成立；未取得合法人票不能 execute Reserve。没有引入真实 AI 或新的资金 HTTP action。 | `services/api/tests/test_a2_signing_renewal.py`：Review/Reject、人票 bundle、revoked assessment signer；隔离 live fixture 使用 Review。 |

### 确定未广播后的 signing request 恢复

`invalidated_not_broadcast` 是新增的明确终态。仅已经 `failed` 的请求，且有正向、持久的
未广播证明，才可退役后以新显式 idempotency key 续期：所有 persisted transaction
attempt 均为 `not_broadcast` 且 `tx_hash IS NULL`；或没有 attempt，但保存的失败 operation/
单个 failed step 明确记录 read-only preparation rejection。它不清除旧 submitted operation、
attempt 或 audit，不由“没有看到 receipt”、到期或错误消息文本推断。

`queued`、unknown/requires_attention、可能广播或已 confirmed/消费的请求仍受保护，
不能释放原 nonce reservation 或复用旧签名。若链 nonce 已确实消费，新授权只能使用
当前链 nonce、当前 bundle 和新 deadline。

## 数据库升级与回滚

### Receipt 来源保护

新 Receipt context 固定原 `receiptEvidenceDocumentVersionId`；sign-demo/submit
复查原 version 的 namespace/procurement/category/uploader/frozen hash。缺绑定的
legacy pending fail closed，不能凭后来 Recipient 同 hash 新版冒认原文件；历史原字节保留。
只有未提交 prepared/signed 可按确定来源无效退役，显式新 key 由合法 version 重建。
已有 submitted pointer 的新签名/提交一律拒绝；合法旧 key 历史重放保留。

worker 在 queued 无 attempt 和 prepared envelope 的新 send 前做 DB 来源门禁。
违规变为 `requires_attention`，保留签名/pointer/nonce/envelope，并稳定保留403/409与
`chain.receipt_source_blocked` audit；不能被误当 `not_broadcast` 后自动释放。
已有 prepared 的 RPC 不确定保守待处理，只有当前新建 attempt 的正向预发送证明可退役。
已经精确找到的广播只如实 reconcile，不重发、不改 confirmed 链事实。

`c31003a20004` 只追加 review guards、renewal partial indexes、policy projection 和 canonical
event 字段。旧 A1/002/003 migration 不修改。populated A1 升级测试核对 synthetic
旧 user/password hash/wallet/session、business ID/draft、operation/idempotency 和 audit 保留，
且升级不创建 chain queue/signature/transaction。

整合 migration 路径：先 upgrade published 003，再 downgrade base；由 populated A1
升级 head004。003 对当前 app 的 `/ready` 应为 503（schema 尚未到 head），004 为 200。
004 的 downgrade 显式拒绝且 revision 留在 004。独立 PG 测试还由 populated published003
expired/live 同 nonce-family 升至004，逐字段证明原签名/typed-data/digest/payload hash/
deadline/operation/audit/source/timestamps 原样保留，不生成新授权、队列或 transaction。
已发布 003 的 SHA256 为 `fabff2145aab986001b635cb84bed96641cef996ace15379f909979c0fe32665`；升级验证应核对原文件保持不变。

004 为 forward-only：恢复永久 nonce unique 可能破坏合法 renewal history，自动 downgrade
也不能静默丢弃 policy/历史事实或复活已失效授权。回滚应恢复事前验证过的隔离备份，
并使用对应旧应用版本；本文没有宣称执行了备份恢复演练，该演练为 **PENDING**。

## 隔离验证入口

这些入口覆盖不同范围，不能把历史专项结果相加作为当前完整回归结果。测试只使用经过安全 guard 核对的自有隔离资源；no-connect 场景不能创建 engine、连接或清空 PostgreSQL，也不能操作默认 Anvil。

| 范围 | 入口 |
| --- | --- |
| Worker 恢复 | `services/api/tests/test_a2_worker_recovery.py` |
| Route recovery / 签名 / readiness | `services/api/tests/test_a2_route_recovery.py` |
| Signing renewal / Receipt source / AI outcome | `services/api/tests/test_a2_signing_renewal.py` |
| PG bounds + 旧 migration 回归 | `services/api/tests/test_a2_database_bounds.py services/api/tests/test_contracts_and_migrations.py` |
| Indexer 恢复 + 独立 Python hash | `services/api/tests/test_a2_indexer_recovery.py services/api/tests/test_a2_independent_hash_vectors.py` |
| 部署 gate（最新 transport 版本） | `services/api/tests/test_a2_deployment_gate.py` |
| 测试目标 no-connect guard | `services/api/tests/test_test_database_guard.py` |
| 新 nonzero V1 隔离合约 fixture | `services/api/live_tests/test_a2_nonzero_hash_vectors.py` |
| 完整 API/PostgreSQL 回归 | `bash services/api/scripts/test.sh` |
| 后续 manifest 类型/fallback 补测 | `services/api/tests/test_a2_cached_manifest.py` 和 route recovery 新参数场景 |
| 真实 API ReceiptConfirmed + automatic reorg | `services/api/live_tests/test_a2_receipt_confirmed.py` |
| 真实已上链 AI/human 过期续签、Review/Reject 人工决定 | `services/api/live_tests/test_a2_renewal_human.py` |
| accepted Blockchain 回归 | `bash scripts/check-blockchain.sh` |
| accepted local-chain lifecycle | `.local/runtime/bin/python scripts/test-local-chain.py --live` |

可执行 PG 专项命令（先确认是本组 owned marker，且取得独占测试 slot）：

```sh
source .local/postgres/connection.env
export POG_MANAGED_POSTGRES_STATE=.local/postgres
export PYTHONPATH=services/api/src
.local/runtime/bin/python -m pytest services/api/tests/test_a2_worker_recovery.py -q
.local/runtime/bin/python -m pytest services/api/tests/test_a2_route_recovery.py -q
.local/runtime/bin/python -m pytest services/api/tests/test_a2_signing_renewal.py -q
.local/runtime/bin/python -m pytest services/api/tests/test_a2_database_bounds.py services/api/tests/test_contracts_and_migrations.py -q
.local/runtime/bin/python -m pytest services/api/tests/test_a2_indexer_recovery.py services/api/tests/test_a2_independent_hash_vectors.py -q
```

可执行 no-connect 专项命令（文件自带 fixture 隔离 DB 清理；redirect 测试只临时开启
本机 loopback HTTP server 并在结束后关闭）：

```sh
export POG_TEST_DATABASE_URL=postgresql+psycopg://pog_api@127.0.0.1:55433/pog_api_test
export POG_MANAGED_POSTGRES_STATE=.local/postgres
.local/runtime/bin/python -m pytest services/api/tests/test_a2_deployment_gate.py -q
.local/runtime/bin/python -m pytest services/api/tests/test_test_database_guard.py -q
```

命令中的 loopback 端口和相对目录是示例，必须与操作者的隔离配置一致；不可换成业务库、远端库或默认链，也不能绕过安全 guard。

## V1 非零 hash 向量与证据边界

`pog_api.hash_vectors` 使用独立 Python `keccak(abi.encode(...))` 实现，不能调用 contract helper 生成期望值后再自证。测试入口为 `services/api/tests/test_a2_independent_hash_vectors.py`，覆盖 uint256/uint64 边界以及 overflow/type negatives。

| 公式 | ABI 有序输入（首项为对应 domain 的 keccak） |
| --- | --- |
| PRE evidence | `POG_V2_PRE_EVIDENCE`, projectId, procurementId, foundation, recipient, vendor, asset, budgetCap, poHash, requestHash, goodsRequestHash |
| FINAL evidence | `POG_V2_FINAL_EVIDENCE`, projectId, procurementId, foundation, recipient, vendor, asset, reservedAmount, poHash, invoiceHash, invoiceAmount, goodsHash, receiptDigest |
| Assessment ID | `POG_V2_AI_ASSESSMENT_ID`, stage uint8, procurementId, outcome uint8, riskScoreBps uint16, evidenceHash, reportHash, signer, nonce uint256, deadline uint64；不包含 assessmentId 自身 |

`services/api/live_tests/test_a2_nonzero_hash_vectors.py` 是单个独立隔离 contract-verification
fixture：逐向量输出精确输入、Python hash 和 accepted contract helper hash，覆盖非零
PRE/FINAL、两种 stage assessment ID/digest、五种 terms 及 HumanIntent digest。后续 action
只能在该 synthetic/direct-contract 测试中准备合法 state；不新增 A2 HTTP，不写 PG，不
提供运营资金能力，不连接真实 AI/payment。不得把单个 fixture 报成多项独立故障测试。

向量 fixture 输出实际输入与 Python/contract 的比较结果。comparison rows 的数量不等于独立 pytest 场景数，也不能据此推断签名或付款验收。先按 [A2_RUNBOOK.md](A2_RUNBOOK.md) 设置并验证自有隔离链 manifest，再运行：

```sh
source .local/postgres/connection.env
export POG_MANAGED_POSTGRES_STATE=.local/postgres
export PYTHONPATH=services/api/src
export POG_A2_LIVE_MANIFEST="${POG_A2_CHAIN_MANIFEST:?Set the verified owned-chain manifest path first}"
.local/runtime/bin/python -m pytest services/api/live_tests/test_a2_nonzero_hash_vectors.py -q -s
```

该 fixture 不连接或写入 PostgreSQL，但仍要求 shared guard 校验测试环境。链目标必须是自有隔离 Anvil，不能使用默认 8545。纯 Python 比较不能替代实际 helper 比对；新版本仍须绑定准确 commit、命令及 CI 结果。

## 运行与验收边界

A2 HTTP 综合流程止于 `ReceiptConfirmed`，不证明 Foundation 已收到稳定币或 Vendor 已付款。独立 direct-contract fixture 的后续状态只用于验证 helper，不提供新的 API 资金能力。所有资产是无真实价值的 MockHKD；真实 AI、换汇、最终支付和可运营 Release/Settlement/Close/Refund API 均不在此范围。

公开 CI 应按准确 commit 核对 [GitHub Actions](https://github.com/Eason-NotFound/PoG/actions)。本文不新增测试通过声明；局部技术验证不能替代新版本完整回归、备份恢复演练或独立产品验收。
