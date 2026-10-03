# PoG API / Database A1 任务书

版本A1.1，2026-10-03香港时间。用户已确认FinTech第二题并授权开始，承接上一轮A0确认/A1授权请求。本任务负责HTTP/DB/文件/会话及角色基础，实际开发由API & Database Coder完成，PM负责冻结和review。

## 工作区与交付方式

- 重新核验origin/main，当前已接受归档基线ae7bb08292bb4a57f9d416f7725ef332be473306。
- 共享checkout已被另一组切到codex/blockchain-booth-mock-preparation，并有未提交docs/BOOTH_MOCK_RUN.md和docs/booth/。不要修改、暂存或切换该checkout。
- 先检查自己chat的artifacts，复用合适独立worktree；没有则用create_worktree从origin/main创建。只在该目录创建codex/api-db-a1-foundation feature branch。不得初始化新repo或复制团队仓库。
- 新增services/api/和docs/api/。把PM的A0冻结文件和本任务书用正常文件编辑方式纳入docs/api/，记录用户本轮授权；无需改写根AGENTS历史授权记录。保持M1/M2/M3.1技术文件/ABI/tag不变。
- 允许必需project-local Python与PostgreSQL运行时/依赖安装、创建本组私有local/test DB及迁移、loopback API/Postgres启动测试。具体版本查官方来源，exact lock，记录安装方式；没有Docker不阻塞，不安装Docker、不注册系统服务、不修改全局环境。
- 交付verified feature branch、draft PR和CI；PR包含基线、接口/DB变化、实际tests、未实现项、回退点。调用attach_artifact附PR。不要merge/tag或开A2。

## 必须完成

1. Python单体HTTP框架、配置、稳定错误schema、health/ready、OpenAPI、环境样例和精确启动命令。默认127.0.0.1；demo/mock标签必须明确。真实deployment readiness尚未接入时不得伪造verified=true。
2. 独立demo账号、会话签发/expiry/logout、角色与固定钱包授权。支持Foundation/Recipient/Donor/独立humanApprover，为AI/payment adapter留service身份边界；没有公开owner管理入口。不信任客户端role/from/caller/wallet字段；凭据不硬编码或进Git，登录不把其他用户凭据发给客户端。
3. PostgreSQL + SQLAlchemy/Alembic迁移和约束：users/sessions/role-wallet、deployment namespace、project/procurement元资料、私有documents/version、operations/steps、audit，以及risk/receipt/approval/chain/payment link的扩展结构。不需要实现后续所有写接口；金额NUMERIC(78,0)范围严格。禁止用SQLite验证结果冒充PostgreSQL。
4. 项目/采购的链下draft元资料创建、授权query和read model接口；固定Foundation/Recipient/vendor绑定。A1所有draft/sample数据必须注明off-chain或fixture；不能仅写DB显示已创建链上项目、已捐款、已放款或已核账付款。Donor可看用于捐款的脱敏项目简介/公平与Closing/refund规则，但不可读取私有发票/照片。Recipient只访问其项目必要证据；human只访问其策略允许的项目。
5. 私有文件上传/读取：PDF/JPEG/PNG，10MiB，流式大小上限，MIME/内容检查，随机storage key、防目录穿越、归属与权限、不可覆盖旧证据版本、原bytes SHA256与keccak分栏，事务失败/重复请求的文件清理。只写本组storage目录，不接受任意服务器路径。测试真实格式的合成文件，原始测试文件不能含私人资料。
6. 持久化Idempotency-Key：namespace+principal+kind+key，1..128可打印ASCII；validated payload用固定UTF8排序紧凑JSON/SHA256。每个创建/上传业务mutation有幂等保护；登录/退出的幂等语义也在OpenAPI说明。并发唯一约束、相同key返回原结果、异payload409、restart之后保留。operation状态/Audit和原请求错误持久化；无资金操作的mock adapter不可生成真实confirmed交易。
7. Chain/AI/payment adapter的Protocol/DTO和显式Unavailable/Mock边界；A1不签EIP712、不发交易、不建AI模型、不写模拟HKD账本。配置或调用缺少服务时给出准确状态/503而非虚构成功。
8. pytest/HTTP/Postgres integration测试、seed命令、运行手册及交接。seed须显式opt-in/幂等、生成本实例业务ID、展示假资料；restart不自动重seed或修改密码；密码/会话/token脱敏。依赖和CI actions固定版本/commit；自动测试无需外部AI/payment或现有8545。

## 最低验收测试

- 空Postgres迁移至head，降级/重新升级只对隔离测试DB进行，数据与constraints验证。
- 登录正确/错误、session expiry/logout；伪造role/caller/wallet、跨项目/跨采购ID权限拒绝；human与Foundation独立。
- JSON只接受规范十进制uint字符串，覆盖0、最大uint256、超范围、负数、小数、科学记数、NaN/Infinity、JSON number/bool、6decimals精确转换与超精度拒绝；无float往返。
- 同/异payload幂等、不同principal/namespace隔离；至少并发重复创建与上传一次，重启同key不会多一条业务记录/文件。
- 文件超过上限、错误MIME、扩展名与真实内容不匹配、路径穿越、未授权读取、旧版本hash不变、坏请求/事务失败无孤儿文件。
- A1draft/query明确没有链确认；Unavailable与Mock adapter状态正确；health和ready不伪造链验证。
- OpenAPI路径/schema与实际HTTP一致，测试/文档有可运行例子与稳定错误code。
- 精确依赖锁可重建，真实Postgres tests无skip；tracked变更只在本组服务/docs/必要CI及ignore，no secrets/private DB/artifacts。

## 交付给PM

尽早在自己chat commentary给worktree路径、feature branch和依赖/DB方案，PM会只读跟进。完成后在自己chat报告：commit/PR、运行命令、test数量/耗时/skip、Postgres实际版本、数据约束、endpoint与不支持路径、已知限制、回退基线。若PM提出review修正，在同一A1候选分支修正并复验；仍不merge/tag或进A2。不需主动向CEO或其他chat发送消息，PM读取状态/结果。

本任务书固定业务与验收；常规内部文件名、实现结构、确切兼容依赖选择由Coder判断并提供证据，不为这些常规选择暂停问用户。只有新的业务决定、破坏性动作或超出本组范围时报告具体阻塞。
