# PoG 项目账号偏好

用户于2026-10-03明确澄清并要求记住和修改。此为项目持久规范，适用于 API/数据库以及后续页面与演示；优先于历史 demo 用户名示例。

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
- 本偏好由 PM 冻结、Coder 修改产品实现。A2 仍不扩大到前端开发或未批准的管理/资金接口。
