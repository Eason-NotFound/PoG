# PoG 项目账号偏好

标准应用账号约定，适用于 API/数据库以及页面与演示；当前示例使用以下名称。

| 职责 | 标准用户名 |
| --- | --- |
| Foundation | `foundation` |
| Recipient | `recipient` |
| Donor | `donor` |
| Human Approver、页面维护等管理员职责 | `admin` |

- Human Approver 与页面维护使用同一个 `admin` 应用身份，不额外提供 `human-demo`/独立 human 用户入口。
- 用户名不等于链角色：`admin` 的 human 审批仍绑定独立 humanApprover 钱包，不与 Foundation/Recipient/Donor/owner 身份合并，不获得任意审批/代签/资金/钱包管理权限。
- 四个标准入口不加 `-demo` 后缀；模拟资产、解锁钱包、合成报告等 demo 标签仍须展示。
- AI fixture、第二 Donor 如隔离测试需要，可作为明确技术账号另设；不构成新的对外标准角色入口。
- 现有同组 demo 用户 rename 使用显式受控、幂等迁移；保留 user ID、密码hash、钱包与业务/audit关联；目标username冲突不得覆盖或合并。不能改写历史接受tag/技术快照。
- A2 不提供页面维护 CRUD 或通用管理、钱包及资金接口。
