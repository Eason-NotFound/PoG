# 已确认的页面权限

用户确认：捐款人仅捐款人；基金会可捐款人＋基金会；受捐机构仅受捐者；管理员看全部；维护人员只看所负责的页面。

| 身份 | donor | foundation | recipient | admin | 业务写操作 |
|---|---|---|---|---|---|
| donor | 全部 | 无 | 无 | 无 | 个人模拟捐款 |
| foundation | 全部（个人视角） | 全部 | 无 | 无 | 本机构项目/模拟拨款入账/采购/发票及发货证明/申诉/审计通过后的采购操作 |
| recipient | 无 | 无 | 全部 | 无 | 本机构采购、收货照片、验收、申诉、报销草稿 |
| admin | 全部 | 全部 | 全部 | 全部 | 人工审计、处理申诉、维护人员账号与页面授权；不自动取得付款权 |
| maintainer | 指定页面 | 指定页面 | 指定页面 | 指定页面 | 无，默认只读 |

“能看页面”与“能执行财务操作”是独立概念。管理员跨角色查看时展示只读提示；maintenance 不能调用资金 API。未来若需要内容编辑、故障处理或财务签名，应添加专门 action 权限，不能把页面 read 权限视作 approve/pay 权限。

每个页面有稳定 ID，例如 donor.projects、foundation.review、recipient.delivery、admin.connections。维护人员 grants 数组只接受注册页面 ID，默认空，按最小权限分配。固定角色工作区规则在代码中定义，后台不提供随意提权按钮。

## 服务端执行点

1. HttpOnly Cookie 仅携带随机 session token，不携带可由用户修改的 role/grants。
2. 每次请求从服务端读取用户最新 active/role/grants。
3. 页面渲染使用 canRead；API 数据读取也使用 canRead，且只返回该页面需要的 DTO。
4. mutation 使用 canAct，再检查项目归属、业务状态与幂等键。
5. 捐款记录按 ownerId；采购按 foundationId/recipientId 过滤。管理员全平台审计。
6. 维护页面权限不自动授予附件原件/个人捐款单等私密数据；这些 Hash 查询与下载默认拒绝。
7. 接口不接受用户指定身份或 role 来替换已登录用户。

## 验收

- donor 访问 /foundation/review：只显示拒绝访问；对应 /api/page?id=foundation.review 返回 403。
- foundation 切换工作区：只有捐款人和基金会；在捐款人页面只能看其自己捐出的款项。
- recipient 不能捐款、审批或付款。
- admin 看到四个工作区；只有 admin 可以新增维护人员/更改页面授权。
- maintainer 初始仅 donor.projects、admin.connections；不能看到这些工作区内的其他页面。
- grant 给 maintainer admin.users 后仍不能调用 updatePermissions。
- 取消已授权页面后，原会话下一次请求即 403；停用后变为 401。
- donor A 使用 donor B 的凭证 Hash：不返回 B 的内容。
- 直接构造写 API 请求和更改 localStorage 均不能绕过服务端权限。

这是一套本地演示的应用权限实现，不代替智能合约权限。未来合约还需独立验证角色、项目归属、签名及状态。


## 新增页面与审计权限

- `foundation.evidence`：基金会可创建本机构采购、上传发票/发货证明并提交；管理员及获授权维护人员只读。
- `recipient.delivery`：受捐机构上传图片格式的收货照片及验收单，填写数量与说明；不能代替基金会上传发票。
- `admin.audit`：只有管理员能保存人工审计结论，审计对应固定单据版本；不自动触发任何资金变更。
- `foundation.appeals` / `recipient.appeals`：只可就本机构项目的当前未通过审计申诉；一个审计最多一宗未处理申诉。
- `admin.appeals`：只有管理员能受理／驳回。受理重新开启人工审计，不等于批准。
- 附件原件仅项目关联双方与管理员可下载；维护人员即使拥有页面读取权也不能下载原件。
