# PoG 版本控制与阶段验收

更新日期：2026-10-03（香港时间）。团队远端：
https://github.com/Eason-NotFound/PoG.git。

## 当前状态

- 最新阶段：用户于2026-10-03在M2归档后授权无阻塞选择时完成M3.1；本地默认
  配置无阻塞决策。分支 `codex/blockchain-m3-1-anvil` 已完成技术验证：32/32
  新测试（13unit＋19真实隔离Anvil）、原81/81回归、M1 15/15／M2 18/18基线。
  用户随后确认“3.1没问题”并要求上传GitHub，本次按既定流程完成M3.1
  合并、正式标签和归档汇报；不授权M3.2及以后实施。
- M3.1源码 `6295409` 最初发布为草稿PR #4，push／PR的blockchain与local-chain
  CI均通过；新RC `blockchain-v0.3.1-m3.1-rc.1` 固定指向该源码，不移动。
  后续仅发布状态文档的commit不得修改源码／测试／CI／spec／runbook快照。
- M3.1验收评论记录在PR #4；仅在所有原有CI通过、独立归档检查与本机
  32/32（36.843秒）／81/81复验通过后，用普通merge合并为
  `977ea6223f2ce8a9e0f0159c42c289bab4420656`，tree与验收head `e6aebae`一致。
  新annotated正式标签 `blockchain-v0.3.1-m3.1` 指向该merge，远端标签对象为
  `45d50e963db32c233f7b1a4e502034c8facb55cb`。原五个标签未变。
  归档文档使用 `codex/blockchain-m3-1-archive-status` 和独立PR/CI，不重写
  已验收技术文件。合并main与正式tag的两个workflow均已实际通过。
  新API／数据库PM目前只有只读接任／任务方案权限，不代表后端编码授权。
- 远端最初是空仓库；现已创建 main 空根提交 `03368f7`，作为 PR 的目标分支。
- 本地已接入 origin；M2实施分支 `codex/blockchain-m2-escrow` 保留，归档状态文档
  使用新分支 `codex/blockchain-m2-archive-status`，仍走 PR／CI。
- M0.1 / M1 已通过技术检查；用户于 2026-10-02 验收 M1，并确认 Hank 线下审批通过。
  用户授权 M1 合并并正式归档后启动 M2；M2 完成须先汇报，不进入 M3。
- M1 已通过 PR #1 合并，正式标签 `blockchain-v0.1.0-m1` 指向 `45e6bab`。
  用户于 2026-10-03 批准新版 M2（Foundation限额放款＋关闭比例退款）。
  新版使用独立 V2 合约与子目录 ABI 包，原 M1 基线15项不改动。
- M2源码 `810a54e` 已通过独立81项测试和GitHub CI；候选标签
  `blockchain-v0.2.0-m2-rc.1` 永久固定指向该提交，不移动。
- 用户于2026-10-03明确验收 M2 V2，接受 AI 为风险证据、人工审批为最终资金
  决策，以及 MockHKD／模拟供应商付款／无真实AI、API、数据库部署限制。
  本次授权仅完成 M2合并、正式归档和汇报；不授权 M3。
- PR #2已通过普通merge合并为 `61aa673653dd31188d2627d76cbba3f97fed6137`。
  新annotated正式标签 `blockchain-v0.2.0-m2` 指向该merge；main与标签CI均通过。
  M1正式／候选标签和M2候选标签的对象与指向均未改动。
- 18项M2技术快照全部保留；ABI包的原 `0.2.0-m2-rc.1` 是冻结工件标识，
  不为改变验收状态重写包、规格、接口或runbook。正式验收以Git标签和STATUS记录。
- GitHub 服务端规则已启用并经 API 读取验证：main ruleset `24365180`，
  immutable version tag ruleset `24365182`。两者均 active、无 bypass actor。
- 用户于 2026-10-02 明确取消 GitHub 强制人工 review；main 仍要求 PR 和
  `blockchain-verify` CI，禁删、禁强推。不得伪造 Hank 的 GitHub review。
  `blockchain-v*` 标签可新建、不可改写或删除。
- 自动合并、squash/rebase 和合并后自动删分支已关闭，使用 merge commit 保留历史。
- 本地推送 hook 已启用且 12/12 用例通过；其他成员 clone 后仍须自行启用。
- 当前提交作者为用户指定的 Eason-NotFound，邮箱 23260068@life.hkbu.edu.hk。

## 日常流程

1. 在独立分支完成一个已授权 milestone，例如 `codex/blockchain-m2-escrow`。
2. 运行 `bash scripts/check-blockchain.sh`，检查改动和生成 ABI，记录结果。
3. 提交有明确范围的 commit，推送同名分支，创建面向 main 的 PR。
4. CEO 做技术检查，向用户汇报完成内容、测试、差距与接口影响，然后停止。
5. 用户明确验收且 CI 通过后，才合并；不再要求 GitHub approving review。
6. 为合并结果创建新的 annotated tag，例如 `blockchain-v0.1.0-m1`。
7. 修正通过新分支和新版本发布；禁止移动旧 tag、amend 已发布 commit、
   force push 或删掉通过的版本。可以在新版本修改代码，但要解释变更并回归验证。

“已验证”是测试结论，“已验收”是用户决定。两者不得混为一谈。
线下队友审批由用户确认记录，CEO chat 的报告不等于 GitHub approving review。
取消线上 review 要求不取消用户的阶段验收门槛；不得自动合并未验收的 M2。
合并前可用 `blockchain-v0.1.0-m1-rc.1` 保存已验证候选版本；`rc` 不代表用户验收。

## 本地配置

本次已配置 `pull.ff=only`、`push.default=simple`、`fetch.prune=true`，
并使用 `core.hooksPath=.githooks`。新 clone 需要执行：

```sh
git config --local core.hooksPath .githooks
git config --local pull.ff only
git config --local push.default simple
git config --local fetch.prune true
```

hook 会阻止直接推 main、改写远端分支历史、修改/删除现有 tag、误推其他仓库。
唯一初始化例外是远端 main 尚不存在时，推送一个不含任何文件的根提交。
它只用于让后续草稿 PR 有目标分支，不接受应用代码或配置文件。
Git 提交作者的姓名和邮箱单独配置；它们不是 GitHub 登录凭证。

本轮将 GitHub 官方 CLI 2.102.0 放在忽略目录 `.tools`，下载的 SHA-256 校验通过。
本机登录命令（请先在终端进入仓库根目录；Home 目录中的 `.tools` 不存在）：

```sh
.tools/gh_2.102.0_macOS_arm64/bin/gh auth login \
  --hostname github.com --git-protocol https --web
.tools/gh_2.102.0_macOS_arm64/bin/gh auth setup-git
```

当前 M1 源码、测试、ABI 和冻结接口的 SHA-256 存在
`docs/baselines/blockchain-m1.sha256`。这份清单记录当前验证基线，不代表用户验收。
在 M1 历史 checkout 上可运行 `shasum -a 256 -c docs/baselines/blockchain-m1.sha256`。
后续合法版本可能改动文件，必须保留此历史清单，不应为使新版本匹配而覆盖它。

`.gitattributes` 保留官方 vendored 依赖的原始字节，也保留 Forge 的文件末尾
格式与规格 Markdown 换行。首次暂存检查发现的上游 CRLF／空白不通过重写依赖
解决；源码基线的 SHA-256 保持不变。

## GitHub 服务端设置

以具有该仓库 Contents/Workflows write 和 Administration write 权限的账号登录。
不要把 PAT、SSH 私钥、助记词发进 chat 或写进仓库。

空仓库先创建已说明的空 main 根提交，随后启用规则：

- `.github/rulesets/main.json`：要求 PR、CI 通过，禁止删除 main 和强推；
  必需 approving review 数为 0，不要求最后推送者以外的人审批，讨论必须解决。
- `.github/rulesets/versions.json`：版本 tag 可新建，但不可更新或删除。

在 Settings → Rules → Rulesets 导入 JSON，或用已登录的 GitHub CLI：

```sh
gh api --method POST repos/Eason-NotFound/PoG/rulesets \
  --input .github/rulesets/main.json
gh api --method POST repos/Eason-NotFound/PoG/rulesets \
  --input .github/rulesets/versions.json
gh api repos/Eason-NotFound/PoG/rules/branches/main
gh api repos/Eason-NotFound/PoG/rulesets
```

先读取现有 rulesets，已有相同规则时更新其 ID，不要重复创建。服务端生效情况
必须据实记录。当前规则已启用，后续调整必须读取现有 ID 并通过审查，
不得在没有用户决定的情况下放宽或临时关闭保护。

## 版本记录

| 阶段 | 技术验证 | 用户验收 | 远端版本 |
| --- | --- | --- | --- |
| M0.1 规范 | build + smoke test 通过 | 架构作为当前实施规范 | 随 M1 候选发布 |
| M1 token + Registry | 44/44；3 个 fuzz 用例各 2,000 次；lint/ABI 通过 | 用户于 2026-10-02 验收，确认 Hank 线下通过 | PR #1 已合并；正式标签 blockchain-v0.1.0-m1；旧 rc 永久保留 |
| M2 V2 | CEO独立81/81，5个fuzz各2000次；归档前复检、M1基线15/15、M2快照18/18；PR、合并main及正式标签CI通过 | 用户于2026-10-03明确验收，仅授权合并、归档、汇报 | PR #2已合并61aa673；正式blockchain-v0.2.0-m2；RC固定810a54e；该次验收本身不授权M3 |
| M3.1 本地部署 | CEO独立32/32（13unit＋19live）；归档前再次32/32与81/81通过，M1 15/15、M2 18/18；push／PR、合并main与正式tag双workflow通过 | 用户于2026-10-03确认并要求上传，仅授权归档汇报 | PR #4已合并977ea62；正式blockchain-v0.3.1-m3.1；RC固定6295409；旧tags不变；M3.2未授权 |
| 版本配置 | remote/branch/hook 12/12，CI 配置、服务端规则 API 验证 | 本次用户授权配置 | main 和版本标签保护已 active |
