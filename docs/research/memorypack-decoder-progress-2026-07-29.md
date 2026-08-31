# MemoryPack 二进制 JSON 解码进展

日期：2026-07-29

## 本轮目标

把 `JsonData/Data/Json/**/*.json` 中无法按文本 JSON 读取的二进制配置，推进到可用的 schema-based MemoryPack 解码器，并接入 VFS 浏览器预览链路。

## 当前已实现

### schema 提取

`tools/extract_memorypack_schema.py` 从 Il2CppDumper AI-friendly dump 中提取：

- `MemoryPack.Beyond.dll.cs` 的 `*ForMemoryPack` wrapper setter 顺序，作为序列化字段顺序。
- runtime class dump 中的字段类型、offset、继承关系。
- 嵌套短名类的上下文消歧，尤其是大量叫 `Data` 的类。

本轮新增的关键消歧规则：

- 先按完整类型名匹配。
- 短名冲突时，使用外层类名作为上下文。
- 优先使用 metadata token 的后继关系匹配嵌套类型，例如 `ContinuousSetAnimTimeScale` 的 token 后一个 `Data`。
- token 不可用时，再按 dump 文件内与 owner class 的行距匹配。

### 解码器

`tools/decode_memorypack_json.py` 已支持：

- compact object header：普通成员数、`0xff` null、`0xfa/0xfb` 扩展长度。
- string：`int32 little-endian length + UTF-8 bytes`。
- primitive：bool、byte、int、float、double 等。
- list、array、dictionary。
- 常见 Unity 值类型：`Vector2/3/4`、`Color`、`Quaternion`、`AnimationCurve`、`LayerMask`。
- MemoryPack union：通过 union map 或参考 JSON 推导 `tag -> derived type`。
- 部分 unmanaged struct：`DispelConfig`、`BuffIconConfig.OrderPriorityConfig`、`CameraControlStateInitialParam`。
- 字段级特殊编码：
  - `GameplayTagQuery.tags[]` 使用 raw int32 tag。
  - `TimeDilationAction` / `HitStopAction` / `UltimateTimeAction` 的部分 `GameplayTag` 字段使用 raw int32 tag。
  - `BuffData.tagsAfterTriggerExtendBuffAction` 存在特殊空 tag 编码。
- 已知 byte enum 覆盖，例如 `Buff.LifeType`、`BuffStackingSettings` 的部分 enum、`EnemyHurtShakeIntensity`。

### 服务端预览接入

`server.py` 已在 `.json` 非文本分支中尝试 MemoryPack 解码：

- 根据 `logical_id` 推断根类型，目前支持 `SkillData` 和 `BuffData`。
- 默认读取：
  - `data/reports/memorypack-known-schema.json`
  - `data/reports/memorypack-known-unions.json`
- 可用环境变量覆盖：
  - `VFS_BROWSER_MEMORYPACK_SCHEMA`
  - `VFS_BROWSER_MEMORYPACK_UNION_MAP`
- 解码成功时返回 `kind: "text"`、`encoding: "memorypack-json"`，预览区显示转换后的 JSON。
- 解码成功文档同时提供 `/api/memorypack/json?id=...` 的完整打开和下载链接；它与截断预览
  共用同一份 `__meta + value` UTF-8 序列化结果。
- 解码失败或 schema/union map 不存在时，回退到原来的 binary JSON probe 和 hex 预览。

## 已验证样本

### SkillData

样本：`JsonData/Data/Json/SkillData/chr_0028_wulfa_ultimate_skill.json`

- VFS id：`331716`
- 根类型：`Beyond.Gameplay.Core.SkillData`
- 结果：完整消费 `311917 / 311917` bytes
- 纯本地 schema + union map 解码成功，不需要访问 AKEDB/CDN。

### BuffData

样本：`JsonData/Data/Json/BuffData/buff_abilityentity_interact_firewall_10m.json`

- VFS id：`245044`
- 根类型：`Beyond.Gameplay.Core.BuffData`
- 结果：完整消费 `3630 / 3630` bytes
- 纯本地 schema + union map 解码成功，不需要访问 AKEDB/CDN。

## 已知限制

- union map 目前来自已验证样本，覆盖面还不完整。遇到新的 union tag 时，解码会在精确 offset 报错。
- enum 目前多数保留为数值，没有映射回 AKEDB/游戏内字符串名。
- schema 中仍有少量字段需要依赖手工覆盖或特殊规则，后续应继续从 runtime dump 泛化：
  - enum 底层类型读取。
  - 泛型黑板值类型推导。
  - unmanaged struct 自动识别。

## 后续建议

1. 批量抽样 `SkillData` / `BuffData`，把新发现的 union tag 合并进 `memorypack-known-unions.json`。
2. 将 enum 底层类型从 runtime `value__` 字段自动提取，减少 `TYPE_OVERRIDES`。
3. 将泛型黑板类的 `TSerializeValue` 从继承链推导出来，减少 `MEMBER_TYPE_OVERRIDES`。
4. 在 UI 上把 `memorypack-json` 与普通文本 JSON 区分展示，方便用户知道这是解码结果。
