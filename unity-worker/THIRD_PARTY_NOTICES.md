# 第三方组件与许可证

本文件记录 Unity worker 已导入或计划导入的第三方来源。对应许可证原文保存在
`third-party/licenses/`。只有来源、用途和许可证均明确的组件才能进入生产源码或发布包。

| 组件 | 来源 | 用途 | 许可证状态 | 当前是否导入生产代码 |
| --- | --- | --- | --- | --- |
| AnimeStudio | Escartem/AnimeStudio 及 SQwatermark 定制分支 | Unity/AssetBundle 通用读取基础 | MIT，已保存原文 | 已导入首批源码 |
| ACL | nfrechette/acl，随 AnimeStudio.ACLNative 跟踪 | 通用压缩动画解码 | MIT，已保存原文 | 已导入源码，尚未进入默认构建 |
| RTM | nfrechette/rtm，随 AnimeStudio.ACLNative 跟踪 | ACL 数学依赖 | MIT，已保存原文 | 已导入源码，尚未进入默认构建 |
| CSspv | AnimeStudio.Utility/CSspv | SPIR-V 处理 | 许可证原文已保存，导入前需核对实际调用 | 否 |
| simde | AnimeStudio.Oodle/simde | Oodle 原生兼容实现依赖 | MIT，已保存原文 | 否 |
| munit | simde 测试依赖 | 仅上游原生测试 | MIT，已保存原文 | 否，预计不进入产品构建 |

AnimeStudio.Oodle 中的 Kraken 解码器明确声明 GPLv3-or-later；它和 simde 不是同一种
许可证。当前既不导入 Oodle 源码，也不分发 `AnimeStudio.Ooz.dll`。若真实终末地 Bundle
需要 Oodle，必须先作产品许可决策并补齐完整 GPL 许可证与对应源代码提供方式。

权威归档还包含 `fmod.dll`、`HLSLDecompiler.dll`、`BinaryDecompiler.lib`、FBXNative、
Oodle 和多组 ACL 预编译库。它们当前均不自动进入 VFS：必须先证明产品能力确实需要，
并为二进制来源、许可证、目标架构和可重建性建立记录。GUI、Patcher 及其依赖默认排除。

NuGet 包的许可证将在对应项目真正进入生产构建时，根据锁定版本补充到发布清单；仅在
上游 csproj 中出现，不视为已经导入 VFS 产品。
