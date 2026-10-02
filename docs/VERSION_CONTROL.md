# PoG 版本控制与阶段验收

更新日期：2026-10-02（香港时间）。团队远端：
https://github.com/Eason-NotFound/PoG.git。

## 当前状态

- 远端最初是空仓库；现已创建 main 空根提交 `03368f7`，作为 PR 的目标分支。
- 本地已接入 origin，工作分支为 `codex/blockchain-m1-verified`。
- M0.1 / M1 已通过技术检查；M1 尚待用户验收，M2 没有授权。
- GitHub 服务端规则已启用并经 API 读取验证：main ruleset `24365180`，
  immutable version tag ruleset `24365182`。两者均 active、无 bypass actor。
- main 要求 PR、另一位队员 review 和 `blockchain-verify` CI；禁删、禁强推。
  `blockchain-v*` 标签可新建、不可改写或删除。
- 自动合并、squash/rebase 和合并后自动删分支已关闭，使用 merge commit 保留历史。
- 本地推送 hook 已启用且 12/12 用例通过；其他成员 clone 后仍须自行启用。
- 当前提交作者为用户指定的 Eason-NotFound，邮箱 23260068@life.hkbu.edu.hk。

## 日常流程

1. 在独立分支完成一个已授权 milestone，例如 `codex/blockchain-m2-escrow`。
2. 运行 `bash scripts/check-blockchain.sh`，检查改动和生成 ABI，记录结果。
3. 提交有明确范围的 commit，推送同名分支，创建面向 main 的 PR。
4. CEO 做技术检查，向用户汇报完成内容、测试、差距与接口影响，然后停止。
5. 用户明确验收、GitHub review 与 CI 通过后，才合并。
6. 为合并结果创建新的 annotated tag，例如 `blockchain-v0.1.0-m1`。
7. 修正通过新分支和新版本发布；禁止移动旧 tag、amend 已发布 commit、
   force push 或删掉通过的版本。可以在新版本修改代码，但要解释变更并回归验证。

“已验证”是测试结论，“已验收”是用户决定。两者不得混为一谈。
GitHub PR reviewer 应是有权限的另一位队员；CEO chat 的报告不等于 GitHub approving review。
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

- `.github/rulesets/main.json`：要求 PR、另一位队员 review、CI 通过，禁止
  删除 main 和强推；新增改动撤销旧 review，讨论必须解决。
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
| M1 token + Registry | 44/44；3 个 fuzz 用例各 2,000 次；lint/ABI 通过 | 待用户验收 | 独立分支、草稿 PR 与 rc 标签；不得自动合并 |
| 版本配置 | remote/branch/hook 12/12，CI 配置、服务端规则 API 验证 | 本次用户授权配置 | main 和版本标签保护已 active |
