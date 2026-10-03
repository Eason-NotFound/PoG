# A2 技术审查修正记录

2026-10-03，香港时间。本文记录首轮候选 `9189d8c286af2314e136d1de843da5cb5be6d407`
之后、`codex/api-db-a2-chain-integration` 工作区中的修正和专项证据。
基线为 accepted A1 `1844186df10854cd49ccc0886f5622e71e572d8e` / `api-v0.1.0-a1`。

当前按用户最新要求**先交本地审核，不更新 GitHub**。后续用户在 PM 对话直接授权
“跟ceo联合完成m3.2吧”，本地整合已启动。原 `9189d8c` 上的全部修正已保存为仅本地
恢复快照 `f1b7c92`；整合候选在新本地分支 `codex/api-db-a2-local-review-integration`。
候选以该分支 clean HEAD 的完整 40 位 SHA 定位。没有 PR 更新、main merge、push 或 tag；
最终整合回归与实际交接结果记录在本地 `.local/A2_LOCAL_REVIEW_HANDOFF.md`，
未通过的 gate 不会被本文件的历史计数替代。
专项和本地完整回归不能替代远端整合后的新 exact SHA 回归与四类 CI。

发布前发现 Hank 已在同一远端分支发布 `194ae3e` 和
`bcd5de4daa565b79876a58391292e1b127f46a35`，包括已发布的
`c31003a20003_signing_expiry.py`。原本地独立草稿 003 与其编号重叠，已在整合工作区
移到 `c31003a20004_a2_review_guards.py`，`down_revision=c31003a20003`；Hank 的已发布
003 原文不改。两边提交均保留，新增 R1 历史格式兼容不会重写 persisted payload hash。
完整双方回归和迁移升级验证仍待下述最终实测更新。
已发布 A1/A2 migration、accepted M1/M2/M3.1 合约、ABI、local-chain 工具和旧 tag
均未改写。整合工作区的 migration head 为 004；旧 003 草稿仅在恢复快照历史中保留。

## R1–R12 对照

下列路径均相对仓库根目录。测试文件是可执行证据入口；实际已运行的结果见后文，
不能把新增但未重跑的场景计入早期结果。

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
当前链 nonce、当前 bundle 和新 deadline。新增两项正向未广播恢复参数场景在早期
signing-renewal 的 34 项通过后才加入，已在本地完整回归中一并通过（该模块 36 项）。

## 数据库升级与回滚

### 追加 R5 legacy 保护

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
新增31项 API/PG source/history、13项 worker/PG 用例的最终结果以交接记录为准。

`c31003a20004` 只追加 review guards、renewal partial indexes、policy projection 和 canonical
event 字段。旧 A1/002/Hank003 migration 不修改。populated A1 升级测试核对 synthetic
旧 user/password hash/wallet/session、business ID/draft、operation/idempotency 和 audit 保留，
且升级不创建 chain queue/signature/transaction。

整合 migration 路径：先 upgrade published 003，再 downgrade base；由 populated A1
升级 head004。003 对当前 app 的 `/ready` 应为 503（schema 尚未到 head），004 为 200。
004 的 downgrade 显式拒绝且 revision 留在 004。独立 PG 测试还由 populated Hank003
expired/live 同 nonce-family 升至004，逐字段证明原签名/typed-data/digest/payload hash/
deadline/operation/audit/source/timestamps 原样保留，不生成新授权、队列或 transaction。
Hank 003 原文的 SHA256 同步验证；该专项已实跑通过，最终整套整合结果见后续更新。

004 为 forward-only：恢复永久 nonce unique 可能破坏合法 renewal history，自动 downgrade
也不能静默丢弃 policy/历史事实或复活已失效授权。回滚应恢复事前验证过的隔离备份，
并使用对应旧应用版本；本文没有宣称执行了备份恢复演练，该演练为 **PENDING**。

## 整合前已实际运行的专项结果（历史证据）

以下是 f1b7c92 整合前修正和各专项执行时的工作区版本；没有累加为最新整套通过数。
均 0 skipped。PG 专项使用本组 owned loopback `pog_api_test`/55433，串行执行；
no-connect 专项不创建 engine、不连接/清空 PG、不操作默认 Anvil。

| 专项 | 实际结果 | 测试路径 |
| --- | --- | --- |
| Worker 恢复 | 11 passed | `services/api/tests/test_a2_worker_recovery.py` |
| Route recovery / 签名 / readiness | 59 passed | `services/api/tests/test_a2_route_recovery.py` |
| Signing renewal / Receipt source / AI outcome | 单跑 34 passed，20.51s；新增后模块 36 项在完整回归中全通过 | `services/api/tests/test_a2_signing_renewal.py` |
| PG bounds + 旧 migration 回归 | 56 passed，1.96s | `services/api/tests/test_a2_database_bounds.py services/api/tests/test_contracts_and_migrations.py` |
| Indexer 恢复 + 独立 Python hash | 19 passed，5.40s = Indexer 17 + hash 2；之后新增 tip transport wrapper 待全套复跑 | `services/api/tests/test_a2_indexer_recovery.py services/api/tests/test_a2_independent_hash_vectors.py` |
| 部署 gate（最新 transport 版本） | 42 passed，0.59s | `services/api/tests/test_a2_deployment_gate.py` |
| 测试目标 no-connect guard | 49 passed，0.08s | `services/api/tests/test_test_database_guard.py` |
| 新 nonzero V1 隔离合约 fixture | 1 passed，0 skipped，6.69s；21 个非零 Python==contract 比对，不是 21 个独立测试 | `services/api/live_tests/test_a2_nonzero_hash_vectors.py` |
| 完整 API/PostgreSQL 回归 | 364 passed，0 skipped，80.47s；包括原 A1 回归和全部当时已保存的新专项 | `bash services/api/scripts/test.sh` |
| 后续 manifest 类型/fallback 补测 | 31 passed，0 skipped，3.88s；25 no-engine + 6 PG；不冒充又跑过完整 395 项 | `services/api/tests/test_a2_cached_manifest.py` 和 route recovery 新参数场景 |
| 真实 API ReceiptConfirmed + automatic reorg | 1 passed，0 skipped，10.93s | `services/api/live_tests/test_a2_receipt_confirmed.py` |
| 真实已上链 AI/human 过期续签、Review/Reject 人工决定 | 4 passed，0 skipped，34.71s | `services/api/live_tests/test_a2_renewal_human.py` |
| accepted Blockchain 回归 | 81 passed，0 skipped；M1 baseline 15/15、ABI 一致、push guard 12/12 | `bash scripts/check-blockchain.sh` |
| accepted local-chain lifecycle | 32 passed，0 skipped，35.520s | `.local/runtime/bin/python scripts/test-local-chain.py --live` |

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

两个既有 dependency deprecation warnings（Starlette/httpx、websockets.legacy）未计作失败。
命令不得替换成业务库/远端或共享/default chain，不得为了复现负测绕过安全 guard。

## V1 独立跨语言证据边界

新增 `pog_api.hash_vectors` 用独立 Python `keccak(abi.encode(...))` 实现，不调用 contract
helper 生成预期值。非零固定输入和预期值在
`services/api/tests/test_a2_independent_hash_vectors.py`；2 项已通过，其中 assessment ID
含 uint256/uint64 最大值和 overflow/type negatives。

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
新 fixture 已在 owned Anvil 的真实 helper 比对中 **1 passed、0 skipped、6.69s**，输出
21 个非零 Python==contract comparison rows（包括重复采购下的对应向量，不是 21 个
独立 pytest 场景）。stdout JSON 给出每个实际输入与双方结果，没有签名。精确执行命令：

```sh
source .local/postgres/connection.env
export POG_MANAGED_POSTGRES_STATE=.local/postgres
export PYTHONPATH=services/api/src
export POG_A2_LIVE_MANIFEST=/private/tmp/pog-local-a2-api/manifest.json
.local/runtime/bin/python -m pytest services/api/live_tests/test_a2_nonzero_hash_vectors.py -q -s
```

该 fixture 不连接/写入 PG，但仍要求 shared guard 校验测试环境。链目标必须是本组
owned 隔离 Anvil，不能使用默认 8545。纯 Python 2 项或原综合 live 场景不能替代这些
真实 helper 比对；新 exact candidate 的完整 live 回归/CI 仍待最终整合。

A2 API 综合场景仍停在 ReceiptConfirmed：实际 HTTP 综合测试 Donor 50/30，
D80、Reserve60、Invoice50；Foundation 尚未收到稳定币。CEO 的 60/40、D100、
Reserve80、Invoice72、Q80/free20/released0 数例在独立 nonzero 合约 fixture 的
ReceiptConfirmed checkpoint 中使用（最后新增该 checkpoint/计数 assert 尚未单独复跑）；
新的四项 HTTP 续签场景使用 D100/Reserve80 并止于 Reserved，未冒充 ReceiptConfirmed。
nonzero hash fixture 后续合约状态
仅用于验证 accepted helper；所有资产为本机无价值 MockHKD，不是 supplier Paid 的证据，
也不授权真实换汇、最终支付或可运营 Release/Settlement/Close/Refund API。

## 最终整合门禁

- 本地完整 API/PostgreSQL suite：364 passed；后续 31 项单独通过，最终统一重跑及远端整合后的回归 **PENDING**。
- 本地真实隔离 Anvil：原路径 1、renewal 4、nonzero vectors 1 分别通过；最终统一重跑及远端整合后的回归 **PENDING**。
- 新 nonzero V1 live fixture：本机专项已通过 1 项/21 比对；新 exact SHA CI **PENDING**。
- accepted Blockchain / local-chain 回归：81 / 32 passed，0 skip。
- 新 exact candidate SHA、PR #9 四类 CI：**PENDING**。
- PM/CEO 复审及用户 milestone acceptance：**PENDING**。

本地审阅入口：本文件、`.local/A2_LOCAL_REVIEW_HANDOFF.md` 和 clean HEAD 的本地候选；
恢复快照 `f1b7c92` 是原本地修正，不是给 bcd5 直接应用的整合 patch。测试 PG 使用
owned `127.0.0.1:55433/pog_api_test`，Anvil 使用 owned `127.0.0.1:18545`；未触碰 8545。
本轮将停止自有测试进程，保留 DB、部署记录和测试源码；Anvil 为内存测试链，stop
不承诺保存其业务链状态。以后发布需填写 exact SHA、实际完整命令、passed/skipped、
隔离目标、已知限制和 CI 链接。
不从旧候选绿 CI 或当前专项结果推断上述门禁完成，也不自动 merge、发 accepted tag、
启动 A3/A4 或扩展真实 AI/payment/公开部署。
