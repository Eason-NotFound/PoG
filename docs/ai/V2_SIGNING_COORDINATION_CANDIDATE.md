# 0.2 签名协调候选（修订 2）

状态：CHANGE_REQUIRED 的修订提交；API 未冻结，未获 APPROVED_FOR_BOUNDED_TEST。本文件定义拟实现责任与持久化协议；没有创建实际 prepared 请求、运行签名服务、获取私钥或发送交易。

## 责任归属

| 组件 | 责任人 | 行为与密钥边界 |
| --- | --- | --- |
| AI model / report service | AI 组 | 读取可信输入并生成风险报告，不接触任何签名私钥 |
| API / chain adapter | API 组 | 提供不可变输入，校验报告、当前部署/状态/证据，构造 AIAssessment EIP-712，管理 operation、EIP-712 nonce、deadline、digest 与交易协调 |
| independent AI signing component | AI 组 | 控制 AI 签名密钥，独立重算 inputHash/reportHash/assessmentId/digest，校验执行模式、可信来源与全部签名门禁，再对确定 digest 签名 |
| relayer | API 组 | 只提交已经合法签署的 assessment；单独管理发送账户的 EVM transaction nonce，不持 AI/Human/Recipient 私钥 |
| Human Approver / Recipient | 各授权业务角色 | 各自完成 V2 原有审批／收货确认，不把权限交给 AI 或 relayer |

签名组件对模型失败、资料不全、未评分、未知 executionMode、原 bytes/hash 不符、政策未批准、域/实例不可信或当前状态/nonce 不符一律 fail closed。生产 synthetic_fixture 永远禁止签名。rules_only 只有部署执行政策与评分政策明确批准且版本化后才可生成和签署 AI 风险报告；不能批准资金。完整、已评分的 Review=1 或 Reject=2 可以生成风险签名，但不会自动 reserve/release。

## 两种 nonce

EIP-712 AI signer nonce 来自 Registry.aiNonces(signer)，同一 Registry 内跨采购与阶段共享。Relayer EVM transaction nonce 来自发送账户交易序列，两者不得混称“链 nonce”，不得互相推进。

AI 签名并发锁的完整键为：

```text
(deploymentNamespace={runId,instanceId,chainId,registry},
 verifyingContract, nonceFamily="aiNonces", signer)
```

registry/verifyingContract 必须等于被验证实例，signer 为当前 allowlisted AI 地址。地址按 schema 规范表示，不以大小写形成两把锁。持久化索引按这一完整键保证最多一个未解决请求，不能以采购 ID 或 stage 分锁。

部署 namespace 防止独立临时实例互混，不允许人为创建别名绕过同一个物理链实例的 nonce 锁。若多 deployment namespace 指向同一个实际 chainId/Registry 状态，API 必须通过部署唯一映射归并到一个锁域，禁止并行签署同一 nonce。

## prepared 的原子持久化

在数据库事务／等价的跨进程持久化原子协议中：获取唯一未决锁，读取并核对实际 aiNonces(signer)，计算 nonce、deadline、assessmentId 和 digest，保存完整 [SigningRequest schema](schemas/v2/signing-request.schema.json)，提交持久化后才返回 prepared 或调用 signing component。

必须保存 operationId、aiRequestId、signingRequestId、nonceScope、完整 domain、十字段 assessment（含 nonce/deadline）、完整 EIP712Domain/AIAssessment types 与 message、digest、inputHash/reportHash，以及可授权解析原始输入/正文 bytes 的审计索引。只保存 hash 或临时内存锁不足。临签／临提交时重新核对链状态与 nonce；重读变化不得静默改写已持久化 typed data。

prepared 已在本地持久化并占用唯一未决锁，此时尚未消耗链上的 aiNonces。崩溃重启恢复原记录与原锁，不能重新给另一个请求发放同一 nonce；也不能本地 nonce+1。签名组件接收到的 typed data 必须逐字段等于持久化内容，计算的 digest 必须一致。

## 提交与恢复

| 状态 | 保存内容／恢复条件 |
| --- | --- |
| prepared | 无签名/txHash；持久化 nonce、deadline、typed data、digest 及未决锁 |
| signed | 保留确定签名及其验证证明，仍保持同一未决锁 |
| submitted | 保存 relayer 发送记录及 txHash；交易结果未确认期间不释放锁 |
| requires_attention | timeout、断连、reorg 或来源矛盾时保留全部审计记录和锁，先调查原交易/签名是否仍可接受 |
| recorded | receipt 成功、AIAssessmentRecorded、当前引用、canonical block、nonce 已消费均核对；保留审计记录后释放未决锁 |
| invalidated | 有证据表明旧签名不可能再被链接受后，保留失效原因与记录，再释放锁 |

未知结果先查询原 tx、receipt、事件、当前 assessment 和 aiNonces。交易失败不等于旧签名失效；只要签名尚可提交，不能放行另一个请求。不得因 API timeout 或 NonceMismatch 自动重新签。链重组按固定部署的确认政策复核，不能将短暂成功提升为最终确认。若确认需要新请求，用实际当前 nonce、新 deadline 和新 ID 重新持久化并签署，旧记录不能覆盖。

SigningEnvelope 是对外签名状态投影；SigningRequest 是耐久协调记录，两者都在 ReportBody 之外。JSON Schema 只验证形状；原子性、唯一性、跨对象相等、密钥控制及实际链条件由实现与后续独立测试验证。API A1/A2 未实现本协议，A3/M3.3 需另行授权。

