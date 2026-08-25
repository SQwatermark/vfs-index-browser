# AnimeStudio 源码快照

该目录来自 SQwatermark/AnimeStudio 提交
`8cdec963c4e187ea0a4a339b8969844a9574638b`，导入日期为 2026-08-25。当前只保留 VFS
已确认需要进一步整理的源码集合：

- `AnimeStudio`：通用 Unity/AssetBundle 读取核心；
- `AnimeStudio.CLI`：现有导出行为的迁移来源，不作为 VFS 的最终命令入口；
- `AnimeStudio.Utility`、`AnimeStudio.PInvoke`、`AnimeStudio.FBXWrapper`：待按实际能力拆分的
  上游依赖；
- `AnimeStudio.ACLNative`：终末地 ACL 解码桥；其未修改的 ACL/RTM 依赖由项目初始化脚本
  按锁定提交下载，不随仓库重复保存。

GUI、Patcher、旧测试项目和未经许可核对的预编译库没有导入。Oodle 源码包含 GPLv3-or-later
代码，也暂未进入产品源码闭包；只有真实终末地样本证明需要且产品许可决策明确后才处理。

VFS 的工程文件位于 `unity-worker/src/`，不得直接把上游 `AnimeStudio.CLI.csproj` 加入发布
solution。上游同步必须以 `UPSTREAM.md` 记录的精确提交为基线，并重新执行许可证、构建和
结果等价检查。
