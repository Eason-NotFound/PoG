# PoG · FinTech #2 三电脑 booth 演练脚本

日期：2026-10-03。用途：准备 exhibition 演练，不是 finalist pitch，不是已完成的
三电脑验收。本包以已验收 `blockchain-v0.3.1-m3.1` 为技术基线，只新增准备材料。
用户确认已申报 FinTech #2，团队自行编写的代码在开赛后完成；不再把选题或
代码时点作为待决事项。第三方库仍需在提交材料中注明来源。

当前已合并本地链／合约及 A1 API／数据库基础；门户使用独立的本地演示 API，
booth 所需跨模块交易编排、签名提交、确认状态和三电脑全链路尚未完成／验收。
真实 AI 模型及模拟换汇／供应商付款尚未接通。本文件不授权 M3.2–M3.4 开发，不把桌面推演称为
产品跑通。统一样例见 [scenarios.json](booth/scenarios.json)，现场记录用
[REHEARSAL_CHECKLIST.md](booth/REHEARSAL_CHECKLIST.md)。

样例校验可运行 `python3 scripts/check-booth-fixtures.py`。它只核对本包的数字／
文件关联，不发交易、不启动服务，也不证明 booth 已通过。

## 1. 评委要看懂什么

开场用一句话：**我们把供应商的交付后付款账期、受助方的收货确认和捐款者的
共同采购资金串起来；资金按证据与人工审批放出，结项余款按一致规则退回。**

这是 FinTech #2 的“信用资源＋共同采购／成本分摊”切入点。供应商愿意提供
账期是待验证的业务假设，不声称已有合作、真实信用额度或验证过的反欺诈收益。
AI 提供核账风险证据；链执行资金约束；人负责最终资金决定。

演练必须有：一笔完整采购、一次真实规则阻断、两个不同分配案例、一句清楚的
取舍说明。供应商承担等待付款和垫资风险，Donor 在 Active 项目中不能随时退出，
Recipient 拒签会暂停后续流程；平台并不能证明真实物流或强迫 Foundation 付银行款。

## 2. 三台电脑和角色

| 电脑 | 谁操作 | 页面必须能做什么 | 签名身份 |
| --- | --- | --- | --- |
| 💝 Donor | 评委 | 查看项目／规则 → 模拟 HKD 换币 → 捐 60 → 查看锁定／资金时间线 → 领取自己的退款 | Donor A；捐款、授权、退款交易必须来自它 |
| 🏛️ Foundation | 队员 | 项目、PO、预算、Invoice/goods、AI报告、拨款、换汇／付款／结算／结项 | Foundation 普通交易；人工审批用另外的 Approver 钱包，明确显示当前签名人 |
| 📦 Recipient | 队员 | 查看本单 PO／Invoice／货物／金额，选择暂不确认或签署收货 | 独立 Recipient；不是 Foundation 自己点击已收货 |

Donor B 是队员控制的背景捐款者，不需要第四台电脑。供应商的模拟收款记录在
Foundation 页展示，并供其他两页只读查看。操作人兼顾人工审批时也必须明确
切换为 Approver；不能把 Foundation 身份自动等同于审批人。

三台电脑只连同一个 API／数据库／链实例。Anvil 仍绑定 `127.0.0.1`，禁止为了
联通把解锁 RPC 暴露给 LAN。页面共同显示 run ID、project ID、mock 标记和状态；
交易确认看实际 receipt／event／blockHash，不看成功动画。API 的 202 只表示排队。
Demo 钱包和 Anvil 公开开发账户不提供生产级身份隔离。

## 3. 统一故事、钱和文件

全部名称／文件／价格均为公开的合成数据，不用真实受助人照片或私人发票。
主项目：社区应急物资共同采购；模拟供应商先交付再收款，不是货到付款，也不是
Foundation 先自行垫付后报销。

| 案例 | Donor A / B | 预算预留 | 最终 Invoice／结清成本 | 退款池 | A / B 权益 |
| --- | --- | --- | --- | --- | --- |
| A 主场直播 | 60 / 40 | 80 | 72 | 28 | 16.8 / 11.2 |
| B 独立对照项目 | 30 / 20 | 40 | 35 | 15 | 9 / 6 |

A 的 PO：10 套物资、每套模拟价 8、预算上限 80；Invoice：同样 10 套，80 减
双方约定的模拟折扣 8，合计 72；goods／收货材料同为 10 套。不是少送货。
B 的 PO：5 套、每套模拟价 8、上限 40；Invoice：40 减约定折扣 5，合计 35。
AI 组需核对数量、供应商、采购关联、单价／折扣、合计和收货证据。此处没有
真实报价或汇率；两次兑换演示固定 mock 1:1、零手续费，退款是 mHKD 而非 HKD。

金额 API 用 6 位原子整数的十进制字符串，例如 72 = `"72000000"`。样例没有
可提交的签名、hash、nonce、assessment ID 或 deadline；这些必须根据实际文件、
当前链和本轮业务 ID 生成。不能复制上轮签名或用假 hash 点亮已确认状态。

公平规则在捐款前展示：共同承担最终确认成本，按累计投入比例分配余款，非逐项
指定采购用途、非利息回报。合约用累计区间整数差取整，消除尾差，最多 64 个
捐款地址；极小份额会受最小单位取整影响，既有规则不支持随时退出或自动转项目。

## 4. 开场前的安全预置

这是服务接好后的准备清单，不是已经完成的预置：

1. 本机只读运行 `python3 scripts/local-chain.py status` 和 `verify`；使用本轮
   真实 manifest／ABI，核对 RPC、chain ID、合约绑定、AI signer 和各角色。
2. 三页连共同 API；确认 Foundation、Recipient、Approver、Donor A/B 五个角色。
   Foundation/Donor 普通调用必须实际来自本人，不能通用 relayer 代其 `msg.sender`。
   AI／Recipient／human 签名消息才可以由 relayer 按现有合约入口提交。
3. 创建新一轮 A 项目。队员 B 先演示模拟兑换／捐入 40；主场开局 D=40，未预留、
   未拨款。可预备文件，但 A 的主要资金步骤保留现场点击。不要静默重置 8545。
4. 用独立项目完整执行 B 到 Refundable，保留两位捐款者的未领取权益。它是
   “已执行的对照项目回放”，不是冒充本轮现场产生；检查可读的确认交易与事件。
5. Donor A 的 mock HKD 足够兑换 60；链上原有 1,000 mHKD 是 faucet，不是兑换
   证明。展示本轮交易增量／项目账本，不预期捐款后钱包为零。
6. AI 若真实连接，显示模型／版本和实际报告；若只能用固定样例，持续标注
   `Mock AI：固定样例报告`，不能声称现场识别。签名验证不等于模型正确率。
7. 模拟兑付建议采用实际 token 转移至 mock redemption 钱包后关联 mock HKD
   账本，具体方案由 Payment 组另行审定。MockHKD 没有 burn，不能凭空调用销毁。
   Payment 组未接好就只做桌面推演，不伪造转账状态。

## 5. 主场操作脚本（内部目标约 5 分钟，非官方每队时限）

若现场提示更短，按实际时限裁剪。第一次全链路演练不赶时间，先跑通再计时。
以下是目标动作，所有“确认”均要等当前链的真实结果；签名必须由相应角色进行。

| 内部时间 | 操作／谁点 | 另外两屏必须看到 | 核心依据／一句话说明 |
| --- | --- | --- | --- |
| 0:00–0:20 | 队员展示 A 的背景捐款 40、mock 标记和公平规则 | 同一个项目 Active | 先说明先交付后付款与审批责任，不先讲 Solidity |
| 0:20–1:00 | 💝 评委模拟兑换 60、approve 后 deposit 60 | D 从 40 变 100；全部仍托管 | `Donated`＋项目账本；兑换成功和捐款成功是两件事 |
| 1:00–1:45 | 🏛️ 建采购／PO → pre AI → Approver 签预留 80 → executeReserve | Reserved、Q=80、freeLocked=20 | `BudgetReserved`；预留不等于转给 Foundation |
| 1:45–2:25 | 🏛️ 尝试 Invoice 82 被拒；再上传正确 72／goods；📦 先暂不确认，显示继续冻结，再检查并签收 | 错误不改账；没有收货签名时停在 InvoiceRecorded，随后 ReceiptConfirmed | `InvalidAmount(82000000)`；`RecipientReceiptAccepted`；收货签名绑定本单文件／金额 |
| 2:25–3:10 | final AI → Approver 签拨款 → executeRelease；🏛️ requestClosing，尝试未结算结项 | Foundation token 增量 +72；本项目余 28；结项被 unresolved=1 阻止 | `FundsReleasedToFoundation` ≠ Paid；无退款快照 |
| 3:10–4:15 | 🏛️ 模拟稳定币兑 HKD 72 → 付供应商 72 → 记录证据 → Approver 核对并执行结算确认 | 各步骤分别确认；PaymentConfirmed，unresolved=0 | 实际 token 兑付转移＋mock 账本；`MockPaymentConfirmed` 是人工证据确认 |
| 4:15–4:45 | Approver 签关闭快照 → executeClose；💝 评委领取自己的 16.8 | Refundable、B 尚可领取 11.2 | `RefundSnapshot`、`RefundClaimed`；A 领取后不是 Closed |
| 4:45–5:00 | 队员切换 B 对照项目，显示 30/20 → 35 → 9/6 | 同规则、不同数字；读已确认凭证 | 一句话说明信用成本、不能随时退出、mock 限制 |

主场无需评委知道私钥或终端命令。队员不替评委点击捐款／退款，也不替 Recipient
自动签收。按钮之外保留“为什么现在不能执行”和可展开的金额／签名／证据卡片。

## 6. 财务核对表（A 项目，不是合约全局余额）

| 节点 | 累计 D | Q 预留 | 累计 released | 本项目托管负债 | Foundation 本单 token 增量 | mock HKD 状态 |
| --- | --- | --- | --- | --- | --- | --- |
| A/B 捐齐 | 100 | 0 | 0 | 100 | 0 | A/B 的模拟兑换已分别对账 |
| 预算预留／收货前后 | 100 | 80 | 0 | 100 | 0 | 未付供应商；freeLocked=20 |
| 精确拨款 72 | 100 | 0 | 72 | 28 | +72 | 未兑付、未付供应商；原预留余8仍在项目 |
| 模拟兑付完成 | 100 | 0 | 72 | 28 | 0 | Foundation 本单可支付 +72，vendor=0 |
| 模拟付款／人工结算 | 100 | 0 | 72 | 28 | 0 | Foundation 本单 0、vendor 本单 +72 |
| 关闭快照 | 100 | 0 | 72 | 28 | 0 | Refundable；A 权益16.8、B 权益11.2，尚未退款 |
| A 领取 | 100 | 0 | 72 | 11.2 | 0 | A token 退款 +16.8；B 权益 11.2 未领 |
| B 也领取（扩展演练） | 100 | 0 | 72 | 0 | 0 | B token 退款 +11.2；项目 Closed |

`released` 是累计流量，兑付后不归零。表中 token/HKD 都是本单／本轮增量；
同链 B 项目、初始 faucet、其他余额会影响钱包和 Escrow 总余额。供应商收到的是
模拟 HKD 账本记录，链上 mHKD 余额不应自动增加。人工结算确认本身也不转币。

## 7. 扩展演练：争议、阻断和重复操作

- **Recipient 不确认**：停在 InvoiceRecorded。UI 可保存链外“数量待核实”说明，
  但没有本版本的链上 dispute 方法。未收货签名不能 final AI／拨款；确认之前不能
  展示为 ReceiptConfirmed。纠纷升级给人工，不伪造签名来继续。
- **超预算 82**：在 Reserved 时拒绝记录，Invoice 未上链，D/Q/released/余额不变。
  区分 API 校验、`eth_call` 模拟 revert、已广播后失败 receipt；只有最后一种有
  失败链上交易，不能拿前两种宣称“链上已拦截这笔交易”。
- **未结清想退款**：Closing 请求可以成功，但 close vote／executeClose 因未结清
  采购失败，且禁止新捐款／新采购／新预留。已有单仍可完成付款证据／结算。
- **重复点击／超时刷新**：同一操作 key 重试只返回同一 operation／结果；相同 key
  不同 payload 应冲突。不得重复兑换、捐款、付款、记账或退款。
- **已记录错误发票**：不能覆盖；Recipient 确认前按既有人工取消＋新建单纠错。
  确认后不能取消抹债。主场 82 因记录失败无需取消，可以再提交合法 72。
- **AI 报告高风险**：显示风险并让 Approver 暂不签。AI Reject 不是合约自动拒款
  条件；硬门槛来自额度、证据／签名／有效期／审批和状态，不演示不存在的规则。
- **有人未领退款**：权益一直留给原地址，不允许管理员扫走；所有人都 claim 后
  才 Closed。不要把 `returnReleasedFunds` 当快速补救：非零返回在当前 M2 会留下
  未解债务、阻止完整结算／结项，不提供部分付款、补款或核销流程。

## 8. 各组为 booth 交付什么

以下是联调验收输入，不冻结新的 API 路由、DB schema 或 SDK；开发授权与细节
由各组按阶段确认。现有准确合约入口见 `M2_V2_INTERFACE_IMPLEMENTED.md`。

| 项目组 | 必需交付 | 与本脚本的接口 |
| --- | --- | --- |
| ⛓️ Blockchain | 已验收 manifest／V2 ABI；动作、状态、签名／事件和资金期望核对 | 不改已验收合约；出现差异先报告，不能为动画放宽门槛 |
| 🌐 Frontend | 三种角色页面、确认／错误状态、独立签名提示、两项目切换 | 同一 read model；退款／AI／mock 标签清楚，页面不能自行编造成功 |
| 🗄️ API/DB | 文件与业务ID绑定、typed data／nonce／deadline、幂等操作、确认事件和跨屏刷新 | 真实调用者与签名提交分开；隔离 run/instance；错误可解释 |
| 🤖 AI audit | PO预评估＋收货后最终比对；实际结构化报告及签名 | 固定样例或真实模型必须明确标记；报告绑定当前 evidenceHash |
| 💱 Payment | Donor mock on-ramp、Foundation mock redemption、固定供应商足额 mock HKD 付款与证据 | 三笔业务分别对账，关联 release／redemption tx；无真实银行兑付 |

已有单可在 Closing 中继续完成结算，Payment 组不能把 Closing 一概视为禁止旧单
付款。Relayer 不持 AI／Recipient／Approver 的角色私钥；签名方案需独立审定。
合约未强制角色互异，Foundation 可调整 approver policy，不能宣传完全去信任或
“恶意基金会也绝对无法绕过”；booth 展示的是选定角色制度下的可追溯流程。

## 9. 三次演练和停止条件

1. **桌面走读**：各组逐步说出输入、角色、动作、屏幕结果和失败处理。允许用本包
   合成数据；结果叫“脚本审查”，不叫链路通过。目前准备到此层。
2. **真实联调**（另行授权后）：同一实例跑 A/B、全部阻断、两人 claim、幂等和刷新；
   保存真实 operation／receipt／截图。不得把 mock AI、mock payment 升格成真实能力。
3. **三电脑计时**（另行授权后）：找不熟悉项目的人做评委，连续两轮，从明示的
   预置状态起步，记录时长和帮助次数。只换新业务 IDs，不自动 reset 链。

建议内部验收：主流程完整；两例分配正确；至少一次阻断且账不变；跨屏 3 秒内
显示确认结果（内部 UX 目标）；评委自行完成捐款和领取，每次最多一句操作提示；
没有假成功、错签、重记账或 mock 冒充真实。任一关键项失败就报告，不继续扩范围。
具体记录见 checklist。准备材料通过不代表 M3.4 通过，也不能保证进入 finalist。

## 10. 现场备用和提交

先准备与已验收版本一致的 3 分钟录屏，标注预置项目和 mock 边界，包含主流程、
一次失败和两例分配。现场故障可播放它并说明故障；录屏不是本轮 live 成功。
没有完整可运行 UI 时，不把终端测试录像包装成三电脑产品已完成。

手册规定 Oct 4 13:00 code freeze，FinTech exhibition 13:30–14:30；未规定每队
5 分钟。按官方表提交公开 repository、live demo 或 3 分钟视频、Pitch Deck；
至少一名成员到 booth。公开提交前检查真实身份、发票、密钥、API token和隐私。
Pitch Deck 是提交物要求，不等于此阶段准备 finalist pitch；本包优先解决 booth。

来源：用户提供的 Participant Handbook（赛程 p2、到场 p3、代码／库 p4、提交
p7、exhibition 六项评分 p9–11）和 Problem Statements（FinTech #2 p4–5）。
内部逐项检查覆盖 problem/user needs、solution/HCD、technical implementation、
prototype/demo、innovation、practical impact；计时、刷新阈值和操作帮助上限为
团队建议，并非新增赛事规定。
