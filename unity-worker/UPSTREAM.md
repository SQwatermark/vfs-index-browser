# 上游来源

## AnimeStudio

- 上游项目：`https://github.com/Escartem/AnimeStudio`
- VFS 使用的定制来源：`https://github.com/SQwatermark/AnimeStudio`
- 权威提交：`8cdec963c4e187ea0a4a339b8969844a9574638b`
- 导入日期：2026-08-25
- 许可证：MIT，导入源码时保留原始 `LICENSE`

本地 `data/research/AnimeStudio` 不是通用 AnimeStudio 核心的导入来源。更新上游时必须先
更新本文件中的精确提交，核对许可证和依赖变化，并执行 worker 契约测试与真实样本等价
测试。

## VFS 研究分支定制代码

Projectile managed-reference 聚焦解码器不在上述权威提交中。它属于此前的 VFS 研究成果，
当前来源分成两层：

- 被忽略研究副本提交：`03336c41735c959b921aa8fcee39b0079254c97f`（grafted 根提交）；
- 该提交的 `AnimeStudio.CLI/Exporter.cs` Git blob：
  `1b66a69ed4157ca501aae87c3c251836abf3b17f`；
- 其上未提交补丁：85 行新增、1 行删除；`git diff --binary` 的 Git blob ID 为
  `82ca710567cbb22113a0e76c5b219a82f64ce534`；
- 合并后文件 SHA-256：
  `c3571ce7d97ef3b1ee0c517bfd83b39964cd75c6157868c57dc558a92d91d1f3`。

这些标识只用于迁移审计，产品运行和构建不得读取被忽略研究目录。迁移时必须把所需代码
重构为 `Endfield.Extensions` 中职责单一的文件，保留历史解码边界和真实样本结果；不得把
约 1 MB 的旧 `Exporter.cs` 整体复制进产品。
