# 0.2 候选修订 2 校验说明

状态：CHANGE_REQUIRED；API 与完整 PoG-CJSON-0.2/reportHash 规范未冻结。下述是接口负责人离线自检，不能提升为 API 批准、APPROVED_FOR_BOUNDED_TEST 或独立端到端验收。

## 可重复命令

从仓库／解压包根目录运行。Python 参考工具仅标准库；真正 TypeScript 必须严格编译后实际执行，不能以 CJS 代替。TypeScript 5.9.3、@types/node 25.9.5 与完整编译命令见 [工具说明](tools/README.md)。工具与编译输出在独立审阅目录。

```text
python docs/ai/tools/cjson_reference.py selftest
node <review-tools>/compiled/cjson_reference.js selftest
python docs/ai/tools/cjson_reference.py verify docs/ai/vectors/v2/manifest.json
node <review-tools>/compiled/cjson_reference.js verify docs/ai/vectors/v2/manifest.json
git diff --check
```

生成向量不等于验证。Python/TS 独立解析原始 token、编码并实现 Keccak，对照固定 manifest；另外使用 Crypto.Hash.keccak 复核。input/generic 只检原 token/scalar/limits，业务 Schema、排序/版本范围、文件/依赖 bytes、角色/可信链绑定须另检。

审阅目录使用 jsonschema 4.25.1 / referencing 注册七份本地 Schema 与日期 format checker，pycryptodome 3.23.0 复核 Keccak；没有修改 API/模型依赖或接入生产。精确命令/工具身份/输出保存在仓库外，ZIP manifest绑定源文件。

## 本修订实际结果

2026-10-03 所列检查退出0，fail/skip均0：

| 检查 | 结果 |
| --- | --- |
| Python / 严格编译并运行的 TypeScript manifest | 各75/75：22正例、48反例、5hash关系 |
| Python与TS直接输出对比 | 22/22 canonical bytes/length/Keccak完全相同 |
| legacy CJS兼容检查 | 75/75，单独JavaScript证据 |
| TypeScript5.9.3编译 | strict/noEmitOnError PASS；skipLibCheck仅第三方声明 |
| 七Schema meta、两个阶段bundle对象 | 7/7、8/8 |
| 定向Schema结构与错误码矩阵 | 72/72、25/25 |
| OpenAPI结构/内嵌源一致/离线ref解析 | 32/32、200个本地引用 |
| 第三方Keccak复核 | 22/22正例、10/10依赖原bytes双hash |
| 正例ReportBody/input Schema | 12/12、4/4 |
| 两阶段规范bytes/hash及绑定 | 4份规范文件、2阶段一致 |
| BODY身份/阶段/证据/版本及输入政策/依赖篡改 | 12/12拒绝 |
| 原始合成PDF长度/双hash | 7/7 |
| Python Keccak对第三方边界/KAT | 10/10 |
| 文档相对链接及固定实现范围 | PASS；contracts/src、V2 ABI、services/api、docs/api无修改 |

prepared 结构正例仅为内存合成 fixture，没有建立实际 durable operation 或接触真实密钥。图片null与PDF空引用的字节差异已验证；跨对象可信MIME仍是门禁责任。

## 未执行与旧证据

完整可信可评分、可签署生产正例：NOT_RUN。真实 signer/domain、Solidity helpers、真实签名、链提交receipt/AIAssessmentRecorded/当前引用/nonce消费、Escrow反例、真实模型、HTTP服务、nonce耐久并发/崩溃/reorg恢复与A1服务集成均NOT_RUN。receiptDigest使用虚构typed receipt离线计算，无链上接受事实。

原6a1e34e的Python/Node各66、六Schema/34结构/24错误码等只属于旧候选；原始证据保留。负责人转交原81项合约PASS信息不是本次复跑或AI候选端到端验收。

本修订仅docs/ai；合约/ABI、API源码和数据库未改。API确认由用户转达；选择profile及离线自检不会解除签名门禁。
