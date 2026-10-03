# PoG API / Database A1 范围与验证要求

版本 A1.1，2026-10-03。A1 定义 HTTP、数据库、私有文件、会话及角色基础；不签 EIP-712、不发送链交易、不提供真实 AI 或付款。

## 版本与运行范围

- 公开基线：`api-v0.1.0-a1`，commit `1844186df10854cd49ccc0886f5622e71e572d8e`。
- 服务源码和文档位于 `services/api/` 与 `docs/api/`；已发布 Solidity、ABI 和标签保持各自版本身份。
- Python、PostgreSQL 与依赖须精确锁定并可重建，运行资源为专用 local/test 数据库和 loopback 服务；安装不应修改系统服务或全局环境。
- API/数据库变更的验证需绑定具体版本、迁移和接口，不根据 A1 基础接口推断 A2 或后续服务已实现。

## 必须完成

1. Python单体HTTP框架、配置、稳定错误schema、health/ready、OpenAPI、环境样例和精确启动命令。默认127.0.0.1；demo/mock标签必须明确。真实deployment readiness尚未接入时不得伪造verified=true。
2. 独立demo账号、会话签发/expiry/logout、角色与固定钱包授权。支持Foundation/Recipient/Donor/独立humanApprover，为AI/payment adapter留service身份边界；没有公开owner管理入口。不信任客户端role/from/caller/wallet字段；凭据不硬编码或进Git，登录不把其他用户凭据发给客户端。
3. PostgreSQL + SQLAlchemy/Alembic迁移和约束：users/sessions/role-wallet、deployment namespace、project/procurement元资料、私有documents/version、operations/steps、audit，以及risk/receipt/approval/chain/payment link的扩展结构。不需要实现后续所有写接口；金额NUMERIC(78,0)范围严格。禁止用SQLite验证结果冒充PostgreSQL。
4. 项目/采购的链下draft元资料创建、授权query和read model接口；固定Foundation/Recipient/vendor绑定。A1所有draft/sample数据必须注明off-chain或fixture；不能仅写DB显示已创建链上项目、已捐款、已放款或已核账付款。Donor可看用于捐款的脱敏项目简介/公平与Closing/refund规则，但不可读取私有发票/照片。Recipient只访问其项目必要证据；human只访问其策略允许的项目。
5. 私有文件上传/读取：PDF/JPEG/PNG，10MiB，流式大小上限，MIME/内容检查，随机storage key、防目录穿越、归属与权限、不可覆盖旧证据版本、原bytes SHA256与keccak分栏，事务失败/重复请求的文件清理。只写本组storage目录，不接受任意服务器路径。测试真实格式的合成文件，原始测试文件不能含私人资料。
6. 持久化Idempotency-Key：namespace+principal+kind+key，1..128可打印ASCII；validated payload用固定UTF8排序紧凑JSON/SHA256。每个创建/上传业务mutation有幂等保护；登录/退出的幂等语义也在OpenAPI说明。并发唯一约束、相同key返回原结果、异payload409、restart之后保留。operation状态/Audit和原请求错误持久化；无资金操作的mock adapter不可生成真实confirmed交易。
7. Chain/AI/payment adapter的Protocol/DTO和显式Unavailable/Mock边界；A1不签EIP712、不发交易、不建AI模型、不写模拟HKD账本。配置或调用缺少服务时给出准确状态/503而非虚构成功。
8. pytest/HTTP/Postgres integration测试、seed命令、运行手册。seed须显式opt-in/幂等、生成本实例业务ID、展示假资料；restart不自动重seed或修改密码；密码/会话/token脱敏。依赖和CI actions固定版本/commit；自动测试无需外部AI/payment或现有8545。

## 最低验收测试

- 空Postgres迁移至head，降级/重新升级只对隔离测试DB进行，数据与constraints验证。
- 登录正确/错误、session expiry/logout；伪造role/caller/wallet、跨项目/跨采购ID权限拒绝；human与Foundation独立。
- JSON只接受规范十进制uint字符串，覆盖0、最大uint256、超范围、负数、小数、科学记数、NaN/Infinity、JSON number/bool、6decimals精确转换与超精度拒绝；无float往返。
- 同/异payload幂等、不同principal/namespace隔离；至少并发重复创建与上传一次，重启同key不会多一条业务记录/文件。
- 文件超过上限、错误MIME、扩展名与真实内容不匹配、路径穿越、未授权读取、旧版本hash不变、坏请求/事务失败无孤儿文件。
- A1draft/query明确没有链确认；Unavailable与Mock adapter状态正确；health和ready不伪造链验证。
- OpenAPI路径/schema与实际HTTP一致，测试/文档有可运行例子与稳定错误code。
- 精确依赖锁可重建，真实Postgres tests无skip；tracked变更只在本组服务/docs/必要CI及ignore，no secrets/private DB/artifacts。

## 验证记录

记录具体 commit、接口/OpenAPI、迁移、可复现命令、passed/skipped、隔离数据库条件、已知限制和回退版本。接口与约束以实际源码、依赖锁及测试结果为准；记录存在不等于独立产品验收。
