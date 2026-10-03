# M1 讲解、可行性与跨组接口复核

日期：2026-10-02（香港时间）。

结论：架构适合黑客松 MVP，可以按接口分工继续做。当前完成的是测试币和
采购审计账本；资金托管、人工签名审批、实际 AI 服务和三台电脑闭环尚未完成。
仓库检查时仍为空，没有其他组代码可用，因此以下是接口可行性判断，并非真实联调结论。

## 用 Python 的思路理解

`PoGRegistry` 类似一个同时管理权限、数据和状态转换的数据库服务。每次链上
交易都是一次公开可核验的写入；合约会拒绝跳过步骤、越权或重复操作。

`MockHKD` 类似一个余额、转账和授权服务，1 mHKD = 1,000,000 atomic units。
任意地址可领取演示币。它没有真实货币价值、储备或赎回能力。

未来的 `ProcurementEscrow` 保存捐款、计算可用预算、核对人工签名，并把批准的
金额转给固定供应商。AI 仍在 Python 服务中识别图片和审查材料；Solidity 只
验证结构化 AI 报告的签名、证据绑定和期限，不运行识别模型。

## 真实业务流程与当前完成度

完整目标流程：基金会创建项目 → 捐款进入 Escrow → 建立采购并上传申请／报价
→ 第一次 AI 审查 → 人工签名 → 预留预算 → PO → 收货／GRN → 最终发票
→ 第二次 AI 审查 → 人工签名 → 自动支付固定供应商 → 捐款者核对付款。

采购前上传的是申请和报价；到货后的最终发票才用于第二次 AI 审查，避免
“还没有买，哪里来的正式发票”的顺序矛盾。

| 模块 | 当前状态 | 做成什么 |
| --- | --- | --- |
| MockHKD | 已实现、已测试 | 6 位精度、mint、approve、转账 |
| PoGRegistry | 已实现、已测试 | 项目／采购、证据哈希、AI 签名、状态、Root 版本 |
| ProcurementEscrow | 只有接口和 mock | deposit、人工审批、预留、资金守恒、付款待 M2 |
| AI／API／Relayer | 有规范 | 没有实际模型、上传服务、队列和执行程序 |
| Anvil 部署／三机 booth | 有计划 | 部署脚本、地址 manifest、现场演练待完成 |

44/44 测试验证了 MockHKD 和 Registry；测试中的 Paid 是 mock 调用 callback，
没有实际 token 付款。见 `contracts/test/mocks/MockProcurementEscrow.sol:93` 和
`:105`。不能把这些测试称为全系统闭环。

## 各组如何衔接

| 项目组 | 负责工作 | 输入 → 输出／具体接口 |
| --- | --- | --- |
| 区块链 | Registry、Escrow、部署、ABI、链上验签和执行脚本 | 证据＋签名 → 合约状态、余额、确认事件 |
| 前端 | Foundation 钱包写 Registry；Donor 先 approve 再 deposit；Recipient 上传交付材料；审批人签 EIP-712 | 用户操作 → 钱包交易／API operation；展示 submitted 与 confirmed |
| API／数据库 | 文件存储、身份权限、幂等任务、链上读模型、事件索引、typed-data 构建 | 文件／签名 → 哈希、operationId、链上确认状态 |
| AI evidence audit | 两阶段识别、比对、报告及 AI 服务签名 | 原始材料＋链上 evidenceHash → reportHash、AIAssessment、signature |
| Payment／供应商 | 供应商钱包映射、PO 和付款状态，与 Escrow 对接 | fixed vendor＋批准金额 → PaymentExecuted＋供应商余额 |

规范已有 `/v1/ai-assessments`、`/v1/approvals`、采购 `reserve` 和 `payment`。
这些 API 尚未实现。文件上传、typed-data 获取、PO／GRN／invoice 写入准备、
Merkle proof 查询路径需要 API／前端组进一步明确。

Payment 组不应另建独立可支用余额。MVP 的稳定币付款由 Escrow 完成；若它们
负责的是法币供应商支付，该部分应独立标记为模拟，不得把链上付款事件当作法币到账。

## M2 前需要锁定的问题

| 优先级 | 具体问题与证据 | MVP 建议 |
| --- | --- | --- |
| P0 | Foundation 的写入口检查 msg.sender，普通 relayer 无法代写（Registry:207、415、428） | Foundation 钱包直接发交易；Recipient 上传后由 Foundation 确认；relayer 只提交签名与执行 reserve/pay |
| P0 | AI 报告提交后状态前进，无法原地续评；执行时又要求未过期（Registry:348、354、387、538、566） | 报告有效期覆盖单次 booth；过期／发票修正时取消重建采购。原地续评需要新版本设计和批准 |
| P0 | ApprovalIntent deadline 已规范，但已聚合签名到期后怎样计数尚不完整（SPEC:189、358） | M2 执行只计仍有效签名；同一审批人过期重签用新 nonce，替换旧有效性而非增加人数 |
| P0 | 文件字节、报价 bundle 排序、报告 JSON 序列化尚未统一；数学 evidenceHash 已有（Registry:755、775） | API／AI 共用文件 keccak256、确定排序和 JSON 字节规范，给出共用测试向量 |
| P1 | Foundation 可发布任意非零 Root，合约不检查它来自真实付款（Registry:501） | Donor 同时核对 leaf、Registry Paid 数据、PaymentExecuted 和余额；inclusion 只证明属于该 Root |
| P1 | 三机 RPC、钱包和 signer nonce 并行 | 一台主机的私有网络 Anvil；所有客户端同一 manifest；金额整数字符串，前端 BigInt，Python int／Decimal；nonce 按 signer 串行 |

当前 Root leaf 不包含 donor 或 donationId，不能声称具备逐笔捐款到具体采购的
归因。完整用途／供应商真实性仍依赖证据审核与人工责任。

双合约调用方向可行：Registry → Escrow hook，或 Escrow → Registry callback。
M2 必须避免 hook 回调正在执行的 Registry 受保护入口；真实 Escrow 尚未实现，
因此目前只是设计判断（SPEC:469）。

已撤销的 AI signer 不能提交新报告，但此前已接受的报告仍有效至到期。这是当前
实现策略，操作界面须据此解释撤销含义，而不是承诺旧报告立即失效。

## 最小闭环验收

固定一组数据：捐入 100 mHKD，预留 80，最终发票 72。支付后供应商新增 72，
项目可用余额 28，预留余额 0。重复支付、篡改发票、签名过期、nonce 重放和
越权调用必须失败；三台电脑显示同一份已确认状态。

下一步先由用户确认接口规则、批准 M2，再实现 Escrow。M1 当前代码保持原样。
版本与阶段闸门见 `docs/VERSION_CONTROL.md`。
