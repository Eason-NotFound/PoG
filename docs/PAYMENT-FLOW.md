# 网站内的 Payment 与透明资金链

Payment 现在是原 PoG 网站的一部分，直接使用同一个 `.data/pog-demo.json`，不再通过 iframe 或 4010 代理驱动网站演示。启动网站即可：

```sh
npm run dev
# 若本机没有 PATH 中的 Node/npm：
bash scripts/dev-local.sh
```

登录后，Donor、Foundation、Recipient 的总览顶部都有资金链图；侧栏“透明资金链”打开操作与完整流水。`/payment` 自动转入当前身份的资金链页面。admin 的资金链页面提供外部凭证核账、关闭退款与未收货采购取消。

## 页面与 API

- `/donor/overview`、`/foundation/overview`、`/recipient/overview`：钱包/项目分布图。
- `/donor/funds`：兑换冻结与确认、捐入项目、领取退款、换回 HKD。
- `/foundation/funds`：兑付、暂停恢复、申请关闭。
- `/recipient/funds`：相同项目分布与可追溯流水，资金操作只读；收货验收仍在原页面。
- `/admin/funds`：人工核账、批准关闭、退款快照、取消未收货采购。
- `GET /api/payment/state`：当前身份的钱包和可见项目资金投影。
- `POST /api/payment/operations/{kind}`：同源会话、角色和机构校验；写入要求 `Idempotency-Key`。

三个业务工作台都轮询同一来源（8秒）；当前浏览器执行资金操作后立即刷新。账户私人余额只返回本人；共享图展示 Donor 汇总和项目分布，流水不返回内部 actorId。

## 如何解读图

1. **Donor 冻结 HKD**：创建兑换后，金额仍在 Donor 账户中，但不可再支出。点击“确认模拟币到账”才扣 HKD 并产生等额本地模拟币。取消兑换会解冻。
2. **Donor 钱包**：当前全站 Donor 模拟币余额，属于项目外资金，可捐入选定项目。这个汇总不计入项目守恒公式，也不是“此项目的钱还在某个 Donor”。
3. **项目资金池**：已经捐入，仍锁定的余额。待使用与采购预留均在池中，预留不等于已拨款。
4. **Foundation 钱包**：本项目已拨付、尚未兑付或退回的模拟币。
5. **模拟兑付钱包**：本项目已兑付的币；相应模拟 HKD 已记入基金会账户。只在公式里计算币这一面，避免币与 HKD 双重计数。
6. **退款分支**：累计已回原 Donor 的金额；退款币可再次使用，所以这里是累计流向，并非当前钱包持有额。

每个项目都核对：累计捐入 = 池中锁款 + 基金会持币 + 已兑付 + 累计退款。服务端另校验全站发币量 = 所有钱包持币 + 项目池 + 模拟兑付钱包；操作不守恒时整笔取消。

Recipient 工作台表示物资受捐机构，负责确认收到物资，**不是本轮资金收款钱包**。拨款给 Foundation 不表示供应商收款。本模块不执行供应商付款。

## 演示：100 → 72 → 28

默认新增的“透明资金链 · 社区学习计划”（`P-FLOW`）从0开始；两位 Donor 模拟HKD各1000，初始持币0。旧演示记录保持原样，在各页面右上角勾选“查看历史示例”可查看，默认不混入当前资金链。

1. Donor：透明资金链 → 冻结兑换60。三方可看到冻结额。确认到账，再捐入60。
2. donor2：同样兑换、捐入40。项目池100。
3. Foundation：原“采购与发货证明”页面新建72元采购（数量1，单价72，选择当前资金链项目和认证供应商）。
4. admin：原“审计记录”通过当前采购版本；Foundation在“采购审批”确认预留。池中仍100，其中预留72、待使用28。
5. Foundation上传发票并提交证明；Recipient上传收货照片并确认数量。顺序不限。
6. admin通过交付版本审计；Foundation在“交付与付款”确认“拨付模拟币给基金会”。池中28，Foundation钱包72。
7. Foundation资金链页面选择该拨款，兑付72。币在模拟兑付钱包，Foundation模拟HKD72。
8. Foundation在本系统外自行处理供应商付款后，admin登记对应外部凭证（演示时使用清晰标注的模拟凭证）。这里只核账，不扣HKD、不向供应商转账。
9. Foundation申请关闭，admin核准关闭并计算退款。存在预留/未结采购会阻止关闭。
10. Donor领取16.8，donor2领取11.2；两笔到账后项目Closed。

测试全额退款请由Foundation在原“项目与预算”新建另一个项目。捐入60/40，不建采购，暂停后申请关闭并核账，两位原Donor分别退回60/40。

## 请求约定

金额为字符串，最多两位小数。示例：

```js
// 同一笔请求刷新、网络失败或重复点击时复用同一个key。
fetch('/api/payment/operations/exchange', {
  method: 'POST',
  headers: {'Content-Type':'application/json','Idempotency-Key':'your-stable-request-key'},
  body: JSON.stringify({projectId:'P-FLOW',amount:'60'})
});
// 返回 id 是兑换订单ID；下一步为 confirm-exchange，body: {orderId:id, projectId:'P-FLOW'}。
```

本轮原生模拟接口包括：`exchange`、`confirm-exchange`、`cancel-exchange`、`donate`、`redeem`、`cashout`、`pause`、`resume`、`closing`、`return`、`external-reconciliation`、`close`、`claim`、`cancel-procurement`。采购审批和拨款仍走原 `/api/actions/approvePurchase`、`/api/actions/approvePayment`，并在同一事务中更新资金链。没有可跳过审计的直接发币拨款接口。

同一幂等键不同请求会被拒绝。页面保留相同表单的请求键；要有意执行相同金额的新一笔兑换/捐款，请点击“开始新一笔操作”。

## 保留范围与验证

- 本轮是网站原生的模拟币演示；没有连接真实银行或链上资产，不伪造txHash。
- 原 `PoG-payment` 独立包与 Anvil 适配器保留，可作为以后真实测试链接入参考，但**不要同时把4010独立账本当作网站资金来源**。旧 `connect-frontend` 安装器会因文件不同停止，不会覆盖本轮集成。
- 旧项目、余额、记录、Hash、账户、附件保留；新建项目默认使用统一资金链。旧示例API保留兼容性，演示请使用新资金链项目。
- 项目暂停阻止新捐款、采购、预留、拨款和兑付；取消兑换及关闭核账可继续。关闭会解冻未确认的兑换。
- 未收货采购可由admin取消并释放预留；已经收货的采购必须继续核账。退回已放款金额不会自动消除未结采购义务，不能伪造全额外部结算。
- 使用一台主机、一个Node进程、同一账本；另外两台电脑访问主机局域网的3000端口，分别登录不同身份。三电脑正式演示建议先停止开发服务，再运行 `npm run build`、`npm start`；生产模式不依赖开发资源来源白名单。本机开发模式已明确允许 localhost/127.0.0.1。

自动测试覆盖既有26项回归、新增7项资金链测试和2项原生HTTP路由权限/幂等测试（合计35项），包括真实经过网站采购/双方证明/人工审核的72元拨款及28元退款。见 `tests/payments.test.ts`。所有测试使用临时账本，不修改演示数据。

本轮验证：35/35自动测试、TypeScript检查和webpack生产构建通过。浏览器已验证Donor冻结60→确认到账→捐入60、重复点击保持60、Recipient看到同一60且无资金写按钮、中英切换以及原3000端口的Foundation工作台。浏览器资金写操作仅使用临时隔离账本；当前网站新项目仍从0开始。
