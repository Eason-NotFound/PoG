# M3.1 本地 Anvil 使用说明

M3.1 只搭建本地链和部署已验收的三个合约。它不启动 Web、API、数据库、AI
模型、模拟换汇账本或供应商付款服务。后续阶段仍需要单独授权。

## 先理解默认配置

| 配置 | 默认值／含义 |
| --- | --- |
| 开发链 | Anvil 1.8.4，本地 EVM，非自研公链 |
| RPC | `http://127.0.0.1:8545`，只能本机访问 |
| Chain ID | `31337` |
| EVM | Cancun；保留默认合约大小限制 |
| 跨域 | 默认 `--no-cors`，本阶段不向网页提供跨域 RPC |
| 合约 | 原 `MockHKD`＋已验收 `PoGRegistryV2`、`ProcurementEscrowV2` |
| 测试币 | 6 位小数；Donor A/B 各 1,000 mHKD，仅为 faucet 初始余额 |
| Gas | 各开发账户初始 10,000 测试 ETH；部署者余额扣除已支付的部署 gas |
| 项目 | 部署不预建项目、不自动捐款、不自动批准 |
| 运行记录 | `contracts/deployments/local/`，已被 Git 忽略 |

Anvil 提供公开已知的开发账户，RPC 解锁这些账户。独立角色地址方便演示与
权限测试，但任何能调用这个本机 RPC 的程序都可能使用它们；不是生产环境的
密钥隔离。不要向这些地址发送真实资金，不要把 RPC 开到公网或 LAN。
未来三电脑通过共同 API 联接这条链，不是各自运行三条独立链。
浏览器／钱包的必要 origin 配置留待后续联调，不能直接开放所有 origin 来绕过。

## VS Code／终端操作

在本仓库根目录运行；工具会查找 PATH 中或 `~/.foundry/bin` 的 Foundry。
不需要安装 web3.py、Node.js、Java、模型 SDK 或云 RPC 服务。

```sh
python3 scripts/local-chain.py up
python3 scripts/local-chain.py status
python3 scripts/local-chain.py verify
```

`up` 启动链，按顺序部署／绑定三个合约，登记测试 AI signer，给两位 Donor
发测试币并生成运行清单。已有健康的自有部署时，它只验证复用，不重复部署或
mint。`status`、`verify` 只读，不创建项目、不发交易。读取实际运行清单中的
地址与 ABI 指纹，不把样例地址当真实配置。

如果 8545 被别的进程或未登记的 Anvil 占用，工具拒绝接管；不会杀掉它。
可以显式指定另一个本地端口及独立运行目录，例如：

```sh
python3 scripts/local-chain.py up --port 8546 --state-dir contracts/deployments/local/alternate
python3 scripts/local-chain.py verify --port 8546 --state-dir contracts/deployments/local/alternate
```

下述命令只停止工具自己记录并核对过身份的节点，保留运行记录：

```sh
python3 scripts/local-chain.py stop
```

## 重置：会清空该演示链

本阶段不承诺保存／恢复业务链状态。`stop` 后，`up` 会要求显式重置，而不是
悄悄换一条链。只有明确想清空演示项目、捐款、交易和签名时才运行：

```sh
python3 scripts/local-chain.py reset --confirm-reset
```

重置只针对本工具管理的本地实例，旧清单与日志归档后重新部署。归档是审计
记录，不是可恢复的链状态快照。重置后合约地址可能相同，但 instance/run ID
及区块身份已变化：后续 API／数据库／前端必须换用新 manifest，作废旧的
交易缓存、待提交签名与模拟付款记录；这部分同步属于后续联调阶段。

注意：instance ID 不属于已验收合约的 EIP-712 签名域。若重置后又复用相同
chain ID、地址、业务 ID 和 nonce，旧签名在链上仍可能有效；换 manifest
本身不会自动阻止这种重放。后续联调必须弃用旧签名并隔离每轮演示业务记录。

若节点不再是已登记实例、PID 被复用、端口被他人接管或记录被篡改，先检查
报错，不要用 `killall`、任意 `rm -rf` 或复制旧地址来绕过保护。

## 与其他组交接

| 对象 | 本阶段可以给什么 | 本阶段没有做什么 |
| --- | --- | --- |
| API／数据库组 | RPC、chain ID、三份 ABI、真实部署地址／交易／代码指纹、角色地址与链实例 ID | 没有 HTTP API、relayer、读模型或数据库 |
| 前端组 | 可查询的本地链、角色地址、签名域及合约配置来源 | 没有页面、钱包连接或三电脑联通 |
| AI 组 | 已登记的开发 signer 地址及原 V2 报告签名规范 | 没有模型运行或真实发票识别 |
| Payment 组 | 测试币、Foundation／供应商／模拟兑付角色地址 | 没有 HKD 账本、兑换、币销毁或付款服务 |

Donor 的 1,000 mHKD 不表示收到 1,000 HKD。MockHKD 可自由 mint 且没有 burn
入口，后续模拟兑付必须按原交接方案单独联调，不能凭部署创建真实兑付承诺。

## 复验命令

```sh
bash scripts/check-blockchain.sh
shasum -a 256 -c docs/baselines/blockchain-m2-v2-rc.1.sha256
python3 scripts/test-local-chain.py
python3 scripts/test-local-chain.py --live
```

第一项仍为原 M1/M2 验证脚本。新测试用隔离的临时目录和独立 loopback 端口，
不重置当前 8545 演示链。新 CI 为 `local-chain-verify`，不放宽原 PR／CI／tag
保护。M3.1 技术通过后只发布候选 PR，等待用户验收，不自动合并或开始 M3.2。

本次独立复验：原81/81回归、新32/32测试（13unit＋19live）均通过；M1基线
15/15与M2技术快照18/18保持一致。默认8545已实际部署并verify通过；这只是
本机本次运行，换机／重置后必须重新生成并核对真实manifest。
