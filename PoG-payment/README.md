# PoG-payment

> 原 PoG 网站现已加入原生 Payment 与三方可视化资金链，使用网站自己的统一账本。演示请优先使用原网站3000端口，参见 [网站资金链指南](../docs/PAYMENT-FLOW.md)。本目录保留为独立服务和Anvil适配器参考；4010数据不会同步到网站，旧前端安装器不会覆盖新的集成路由。

独立的 Payment 模块：Donor 模拟 HKD ⇄ MockHKD、项目捐款、Foundation 按拨款兑付、暂停入口、关闭核账与原 Donor 退款。**不执行供应商付款，不因 Foundation 收到币或 HKD 就标记供应商已收款。**

## 在 VS Code 运行

用 VS Code 打开 `PoG-payment` 文件夹。需要 Node.js 22.13+（推荐 Node 24）；仅使用 Node 内置模块，没有第三方运行依赖，无需 `npm install`。

```sh
npm run setup
npm start
```

打开 http://127.0.0.1:4010 。账号 `donor`、`donor2`、`foundation`、`admin`，演示密码 `PoG-demo-2026`。两位 Donor 的模拟 HKD 初始余额各 1,000；Foundation 为 0。默认没有捐款、拨款或退款记录。

如果这台 Mac 的终端还没有 `node/npm`，可直接在 VS Code 终端执行 `bash scripts/start-local.sh`；启动器会使用本机已有的 Codex Node 运行时。其他机器建议正常安装 Node。

也可先运行 setup，再按 VS Code 的 F5，选择 **PoG Payment**（需要 Node 在 PATH 中）。修改代码时用 `npm run dev`。Ctrl+C 停止服务。

`npm run setup` 只在 `.env` 不存在时创建配置和随机的前端代理密钥。`.env` 与 `.data` 不应提交 Git。演示密码可在 `.env` 调整，重启服务使所有旧独立登录会话失效。

## 两种模式

| 模式 | 运行条件 | 到账含义 |
| --- | --- | --- |
| `PAYMENT_MODE=demo`（默认） | Node 即可 | HKD 与测试币都是本地模拟账本；不生成虚假 txHash |
| `PAYMENT_MODE=anvil` | M3.1 本地 Anvil 和真实 manifest | 测试币在本地链实际 mint/transfer/deposit/claim；HKD 仍为模拟 |

当前机器未安装 Foundry/Anvil，已验证的是演示闭环、HTTP/前端代理、链适配器的受控回执测试；未声称在当前机器完成真实链上端到端验收。

## 连接现有 PoG 前端

在 `PoG-payment` 终端执行：

```sh
npm run connect-frontend
```

默认连接上一层 PoG。若前端在其他位置：

```sh
node scripts/connect-frontend.mjs /absolute/path/to/frontend
```

安装器会：

- 新增 `src/app/payment/page.tsx` 和 `src/app/api/payment/[...path]/route.ts`。
- 在原门户侧栏添加 `Payment · 模拟兑换与退款` 链接。
- 在前端 `.env.local` 添加本机 Payment URL 与服务端代理密钥。不会把密钥交给浏览器。
- 保留其他配置；目标文件或环境变量已有不同内容时停止，避免覆盖已有实现。

同时启动原前端与本模块，重启原前端以加载 `.env.local`。在原前端登录后打开 http://localhost:3000/payment 。本次已为当前根目录前端安装上述入口。

| 入口/端口 | 用途 |
| --- | --- |
| 前端 `3000/payment` | 嵌入 Payment 页面，复用现有登录 |
| 前端 `/api/payment/state` | 当前用户余额、项目、操作与凭证 |
| 前端 `/api/payment/operations/{kind}` | Payment 业务写接口 |
| 前端 `/api/payment/reconcile/{id}` | 查询待确认交易，管理员关联丢失的 txHash |
| Payment `127.0.0.1:4010` | 独立页面与内部 API |
| Anvil `127.0.0.1:8545` | 本地测试链，仅 Payment 服务访问 |

原前端已有 `donor`、`donor2`、`foundation`、`admin` 的 ID/角色/机构组合必须保持一致；维护人员和 Recipient 无 Payment 权限。新用户需要同时扩展本模块账号、代理身份映射及钱包映射，不能仅从请求传入钱包地址。

原 `/api/actions/donate`、前端采购付款和种子项目资金池仍属于旧演示适配层，不会同步写入 Payment。演示这条新资金链时统一从 **Payment 页面**兑换、捐款、兑付和退款，避免把两套账本混用；本模块没有改写旧业务动作。项目 ID 可以与前端 ID 对齐，链上 bytes32 则显式登记。页面数据和余额以 Payment API 为准。

三电脑使用同一台主机的前端 LAN 地址，分别登录不同角色；Payment 与 RPC 保持仅本机监听，不能在每台电脑各开一份账本。单电脑可用独立浏览器配置文件模拟多个角色。

## 切换到实际本地测试链

1. 在合约项目中按原 M3.1 文档启动、部署、验证 Anvil。本模块不自动安装 Foundry、部署合约、创建项目或生成审批签名。
2. 停止 Payment，在 `.env` 中设置以下配置。**切换模式或重置链必须更换数据目录。**

```dotenv
PAYMENT_MODE=anvil
PAYMENT_DATA_DIR=.data/anvil-round-1
PAYMENT_MANIFEST=../contracts/deployments/local/manifest.json
```

3. `npm start`。启动及写操作会校验链 ID 31337、Anvil instanceId、创世块、部署区块、合约字节码 SHA-256、6 位小数和 V2 互相绑定。清单失效时拒绝继续。
4. Foundation/合约组先创建链上项目；Payment 的 admin 使用“关联链上项目”登记前端 ID、名称和 bytes32 ID。固定 Foundation 和 MockHKD 资产必须匹配清单。
5. Donor 兑换会 mint 对应 MockHKD，核对成功回执和 Transfer 事件后扣除 HKD。捐款会先 approve 再 deposit 到具体项目。
6. 采购/审批组按原合约流程执行拨款。Foundation 输入 `FundsReleasedToFoundation` 所在的真实 txHash 与 logIndex；Payment 校验事件、项目、Foundation、采购和发票金额，禁止重复关联。
7. Foundation 兑付调用 `MockHKD.transfer(mockRedemption, amount)`。确认后才增加模拟 HKD。MockHKD 没有 burn，兑付钱包持有测试币，不称为销毁。
8. Foundation 可申请 Closing。关闭核账需要原审批组事先提交足够的有效 `submitCloseApproval`；Payment admin 只执行 `executeClose`，不自动签名或绕过义务检查。
9. 原 Donor 调用 `claimRefund`，按链上固定快照退款至原钱包。可再自行换回模拟 HKD。

清单中的 Donor 初始 faucet 测试币不代表 HKD 兑换收入；本模块显示实际链上余额，但不会凭初始余额给 HKD 入账。Anvil 解锁开发角色仅适合本机无价值演示。

## 暂停、退款与异常

- `pause/resume` 是 **本 Payment 服务的项目入口开关**，不是 Solidity 的项目暂停状态。它阻止此服务的新捐款和 Foundation 兑付，不阻止外部直接发交易；全链 Registry 暂停由原合约管理员管理。
- 暂停不会自动退款。关闭顺序为 `Active → Closing → Refundable → Closed`，需消除预算预留和未结采购；零余额直接 Closed。
- 剩余资金按原 Donor 的累计捐款比例分配，按 donor 顺序的累计区间差取整，原子单位总和守恒；零金额 entitlement 也允许领取一次。
- 退款回测试币钱包。HKD 为两位小数，测试币六位小数；不足一分的余额保留在钱包，不强制舍入或吞掉。
- Foundation 的 `return` 仅退回已拨出但尚未兑付的测试币，且必须处于 Closing。已兑为 HKD 的部分不在此入口撤销。
- 遵守当前 V2 限制：退回已释放资金**不会消除未结采购**，存在返回额时不能按全额供应商结算关闭。需由采购/治理流程解决；Payment 不会伪造完成状态。
- 演示模式的 admin“登记外部核账”仅用于模拟 Foundation 在系统外处理付款后的凭证，不能发起供应商付款，不扣 HKD，不生成供应商账本。要求先完成本笔兑付且无返回资金。本地链模式没有该捷径。
- 同一幂等键和同一请求返回原操作；同一键不同内容返回 409。兑换中的网络超时会冻结 HKD 可用额，不立即扣账，也不会自动重复广播。
- 有待确认操作时暂时阻止所有新业务写入，这是本地单进程演示采用的保守串行策略。页面“重新查询确认结果”继续原操作。丢失 txHash 时仅 admin 可填入经核查的原始交易；必须匹配发送方、收款合约、nonce、calldata、事件和规范区块。
- 无法找到原交易时保持待核对，不提供“假定失败后重发”按钮。重启服务会保留账本与提交意图；不要通过删账本解除待核对状态。

## API 示例

前端在既有登录会话中调用：

```js
const key = crypto.randomUUID(); // 每笔业务一个；超时/重复点击必须复用
const response = await fetch('/api/payment/operations/exchange', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json', 'Idempotency-Key': key },
  body: JSON.stringify({ amount: '60.00' })
});
const operation = await response.json();
// 200 也需检查 status；failed 不等于到账。202 表示 pending/needs_reconciliation。
```

前端页面已在 sessionStorage 保存请求键，刷新或重复提交相同表单复用原键。主动发起相同金额的新一笔操作时点击“开始新一笔操作”。LAN HTTP 环境使用 `crypto.getRandomValues` 生成键，不依赖 secure-context 限制下的 randomUUID。

详细请求体见 [API.md](API.md)，演练步骤见 [ACCEPTANCE.md](ACCEPTANCE.md)。

## 验证与文件

```sh
npm test
# 两个服务已启动时，检查现有前端会话/代理/资源与跨站阻断，无资金操作：
node scripts/check-frontend.mjs
```

14 项自动测试覆盖 60 兑换、72 兑付、重复提交/拨款、余额与权限、部分/全额退款、暂停、精度和零额领取、退回资金后的关闭阻断、事务超时恢复、回执校验和 HTTP。另已执行原前端 TypeScript 检查与真实 HTTP 代理检查。独立 UI 已浏览器检查；真实三电脑和本地链端到端仍需现场验收。

- `src/core.mjs`：金额、账本、单写入者锁与持久化。
- `src/service.mjs`：业务权限、资金流程、幂等与交易恢复。
- `src/chain.mjs`：M3.1/V2 本地链适配器。
- `src/server.mjs`：HTTP、独立登录、服务端代理认证。
- `public/`：Donor/Foundation/admin 操作页。
- `frontend/`：可安装的 Next.js 页面和 API 代理模板。
- `tests/`：自动验收，全部使用临时目录，不污染演示账本。

本模块是单进程本地演示实现，账本文件采用原子替换与 fsync。请只开一个进程写一个数据目录；不部署共享目录多副本，不连接真实银行或真实价值资产。
