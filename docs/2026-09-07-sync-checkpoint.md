# 2026-09-07 同步检查点

当前分支 master。本次归档工作区留存的枚举目录与 Buff 标签解码修正。

- metadata 枚举提取增加 schema 引用范围过滤和嵌套类型名称验证；目录保留来源哈希。
- 正式 schema 附带枚举目录，原字段布局不在本次变更范围。
- 掩码组合只在目录显式标记 isFlags 后展开；提取器目前用名称以 Mask 结尾、None=0、
  All=-1、其余成员单比特这一严格形状识别。这是受限识别规则，不代表已读取所有类型的
  原生 FlagsAttribute；不匹配形状及未知位继续失败，后续需要同版本资源复核。
- Buff tagsAfterTriggerExtendBuffAction 改用 raw int32 tag 数组，移除旧分支字节读取。
  具体版本边界见 research/memorypack-decoder-progress-2026-07-29.md。

本次验证：枚举测试 11 项、MemoryPack overrides 12 项通过；未进行服务端全量资源
重导出或 AKEDB 对照，不能据此认定 VFS 可以取代生产 AKEDB 优先来源。

未提交且不删除：tmp、日志、deploy-backup、trial schema、模型/贴图/动画导出、
Unity Library/UserSettings 及无明确归属的实验脚本；combat-spec/artifacts 是本地
资源目录，不是要纳入本仓库的嵌套源码。后续清理必须另行核对，勿执行 git add -A。
