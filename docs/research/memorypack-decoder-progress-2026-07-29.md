# MemoryPack 二进制 JSON 解码进展

首次记录：2026-07-29

最近更新：2026-08-11

## 本轮目标

把 `JsonData/Data/Json/**/*.json` 中无法按文本 JSON 读取的二进制配置，推进到可用的 schema-based MemoryPack 解码器，并接入 VFS 浏览器预览链路。

## 2026-08-11 梨诺 SkillData 闭环

为解析当前客户端中的梨诺配置，本轮使用已完成 IL2CPP 初始化的 runtime 快照和同版本 AI-friendly dump，补齐了以下链路：

1. `tools/extract_memorypack_unions.py` 从 union formatter 的静态构造器恢复 `tag -> derived type`，不再依赖 AKEDB 明文样本猜测。
2. `AbilityAction.AbilityActionData` 已恢复连续的 `0..385` 共 386 个 tag。
3. `Selector.Finder.Data` 已恢复连续的 `0..21` 共 22 个 tag；梨诺连携技使用的 tag 12 对应 `OwnerSpawnedEntityFinder.Data`。
4. `tools/extract_memorypack_schema.py --union-map` 会把所有 union 派生类型纳入 schema 根集合。当前 schema 包含 500 个类，union map 引用缺失数为 0。
5. 补齐 `BlackboardBuffId.value`、`ObtainCostAction.uspRecoverTag` 和 `EnemyCheckAIMarkerInfo` 的具体序列化布局。

验证样本位于本地导出目录 `.tmp-liino-skilldata-20260809`。其中 36 个二进制 `SkillData` 文件均可完整解码，结果为 `36/36`，每个文件的 reader offset 都等于原始字节长度。范围包含梨诺普通攻击、战技、连携技、终结技、投射物子技能以及武器技能 `sk_wpn_lance_0014`。

这里的“完整”指 MemoryPack 结构完整消费，不代表所有数值 enum 已翻译为名称，也不代表已把技能语义转换为 Endaxis DSL。

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
- MemoryPack union：优先使用同版本 runtime formatter 恢复的 union map；参考 JSON 只保留为诊断辅助。
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
  - `schemas/memorypack-known-schema.json`
  - `schemas/memorypack-known-unions.json`
- 可用环境变量覆盖：
  - `VFS_BROWSER_MEMORYPACK_SCHEMA`
  - `VFS_BROWSER_MEMORYPACK_UNION_MAP`
- 解码成功时返回 `kind: "text"`、`encoding: "memorypack-json"`，预览区显示转换后的 JSON。
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

- `AbilityAction` 和 `Finder` union 已由当前 runtime 完整恢复；其他 union 基类仍可能只覆盖已知样本。遇到未知 tag 时，解码器会在精确 offset 报错，不会静默跳过。
- enum 目前多数保留为数值，没有映射回 AKEDB/游戏内字符串名。
- schema 中仍有少量字段需要依赖手工覆盖或特殊规则，后续应继续从 runtime dump 泛化：
  - enum 底层类型读取。
  - 泛型黑板值类型推导。
  - unmanaged struct 自动识别。
- 服务端预览目前只显示转换后 JSON 的前段，尚未提供完整 decoded JSON 下载接口。

## 后续建议

1. 将 runtime union 提取扩展到其他尚不完整的 union 基类，并加入版本更新检查。
2. 将 enum 底层类型从 runtime `value__` 字段自动提取，减少 `TYPE_OVERRIDES`。
3. 将泛型黑板类的 `TSerializeValue` 从继承链推导出来，减少 `MEMBER_TYPE_OVERRIDES`。
4. 为 `/api/preview` 增加 decoded JSON 的完整打开/下载接口。
5. 在 UI 上把 `memorypack-json` 与普通文本 JSON 区分展示，方便用户知道这是解码结果。

## 同版本数据更新流程

runtime 快照必须在 IL2CPP 已完成初始化后导出；仅有文件映像或过早快照时，formatter 的类型槽仍是空值，无法恢复 union 类型。

```powershell
python tools/extract_memorypack_unions.py `
  --runtime path/to/IL2CPP_GameAssembly.runtime.bin `
  --dump-root path/to/IL2CPP_Dump_AI `
  --union-map schemas/memorypack-known-unions.json `
  --output schemas/memorypack-known-unions.json `
  --base-class Beyond.Gameplay.Core.AbilityAction.AbilityActionData

python tools/extract_memorypack_unions.py `
  --runtime path/to/IL2CPP_GameAssembly.runtime.bin `
  --dump-root path/to/IL2CPP_Dump_AI `
  --union-map schemas/memorypack-known-unions.json `
  --output schemas/memorypack-known-unions.json `
  --base-class Beyond.Gameplay.Core.Selector.Finder.Data

python tools/extract_memorypack_schema.py `
  --dump-root path/to/IL2CPP_Dump_AI `
  --output schemas/memorypack-known-schema.json `
  --class Beyond.Gameplay.Core.SkillData `
  --union-map schemas/memorypack-known-unions.json
```

更新后必须执行：

```powershell
python -m unittest `
  tests.test_extract_memorypack_unions `
  tests.test_memorypack_decoder_overrides -v
```
