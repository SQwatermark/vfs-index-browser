# VFS 本地补丁

本目录以 AnimeStudio `8cdec963c4e187ea0a4a339b8969844a9574638b` 为导入基线。以下是
VFS 为产品构建施加的最小源码差异；同步新上游时必须逐项复核，而不是静默覆盖。

## 2026-08-25

- `AnimeStudio/AssetMap.cs`：未知过滤字段明确返回不匹配，补全 switch 表达式；原行为对
  未知键没有定义，且产生非穷举编译警告。
- `AnimeStudio/Classes/GameObject.cs`：移除未使用的异常变量，不改变原有降级日志和返回值。
- ACL/RTM 完整源码不再 vendor；提交仍由 `dependencies.lock.json` 锁定，初始化脚本下载并
  校验后供 Endfield ACL 桥构建使用。

项目级适配（目标框架、包版本、nullable 编译模式和源码选择）定义在
`unity-worker/src/`，不直接修改上游 csproj。其中 MessagePack 从上游 3.1.4 提升到 3.1.8，
用于消除已知安全漏洞；合入 AssetMap 能力前仍需格式兼容测试。
