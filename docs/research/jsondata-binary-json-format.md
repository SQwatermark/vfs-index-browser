# JsonData 中二进制 `.json` 文件的初步研究

日期：2026-07-29

## 背景

终末地本地资源中存在大量 `.json` 后缀文件，但并非所有文件都是 UTF-8 JSON 文本。VFS 层解密后，部分文件仍无法按文本 JSON 解析；这些文件的二进制内容里能看到可读字符串片段，说明它们大概率不是二次加密，而是某种二进制序列化格式。

本轮研究目标是确认这些文件的分布、格式特征和后续解析路径。

## 扫描方法

在远程主机 `D:\Projects\vfs-index-browser` 上直接读取本地游戏 chunk，复用 `server.py` 中的 VFS 解密函数 `decrypt_vfs_file()`，扫描 `effective` 视图下所有 `.json` 后缀文件的解密后头部。

分类规则：

- 头部去空白后以 `{` 或 `[` 开头：视为普通文本 JSON 候选。
- 能 UTF-8 解码但不是 JSON 前缀：视为 UTF-8 非 JSON。
- 不能 UTF-8 解码：视为二进制。
- 对二进制样本额外扫描 `4 字节小端长度 + ASCII 字符串` 片段。

## 总体分布

共扫描 `89966` 个 `.json` 后缀文件，按目录统计如下：

| 目录 | 数量 | 主要类型 | 首字节特征 |
| --- | ---: | --- | --- |
| `JsonData/Data/Json/LipSync` | 70548 | 二进制 | `0x0f` |
| `JsonData/Data/Json/NPC` | 5633 | 明文 + 二进制混合 | `0x03` / `{` |
| `JsonData/Data/Json/LevelScriptData` | 4517 | 二进制 | `0x1b` |
| `JsonData/Data/Json/BuffData` | 2603 | 二进制为主 | `0x1e` |
| `JsonData/Data/Json/SkillData` | 2423 | 二进制 | `0x2f` |
| `JsonData/Data/Json/MissionRuntimeAsset` | 980 | 明文 JSON | `{` |
| `JsonData/Data/Json/LevelData` | 958 | 二进制 | `0x2b` |
| `JsonData/Data/Json/Interactive` | 791 | 二进制为主 | `0x07` / `0x19` / `0x02` |
| `JsonData/Data/Json/SpawnerConfig` | 560 | 二进制 | `0x05` |
| `JsonData/Data/Json/AnimationConfig` | 118 | 二进制 | `0x0f` |
| `JsonData/Data/Json/GameplayConfig` | 41 | 明文 + 二进制混合 | `{` / `0x01` / `0x05` |

结论：`.json` 后缀下至少同时存在两类数据：

1. 真正的 UTF-8 JSON 文本。
2. 逻辑上属于 JSON 数据目录，但内容是二进制序列化的配置对象。

## 典型样本

### 明文 JSON

`JsonData/Data/Json/GameplayConfig/NpcProxyTable.json`

头部：

```text
7b 0d 0a 20 20 22 64 61 74 61 54 61 62 6c 65 22 ...
```

文本：

```json
{
  "dataTable": {
    "a1m11daimeng_map01_v1d4d0_002": {
      "subDataParentId": 200290000
    }
  }
}
```

### 二进制 SkillData

`JsonData/Data/Json/SkillData/chr_0028_wulfa_ultimate_skill.json`

头部：

```text
2f 02 00 00 00 00 6a 00 00 00 04 3f 00 00 00 03
01 00 00 00 fa 09 01 10 01 00 00 00 00 00 00 00
00 00 00 00 00 0d 00 00 00 55 6c 74 69 6d 61 74
65 53 6b 69 6c 6c ...
```

可读片段：

```text
UltimateSkill
```

`0d 00 00 00` 后紧跟 13 字节 ASCII/UTF-8 字符串 `UltimateSkill`，符合“4 字节小端长度 + UTF-8 字节”的布局。这个特征和 MemoryPack 官方 UTF-8 字符串布局并不完全一致，因此目前只能把 MemoryPack 作为相近参照，不能当成已确认格式。

### 二进制 BuffData

`JsonData/Data/Json/BuffData` 下大量文件首字节为 `0x1e`，前 2KB 内常见长度前缀字符串。样本规律非常稳定。

## 格式判断

后续对 IL2CPP dump 的检查确认：至少 `SkillData` / `BuffData` 是 MemoryPack。`MemoryPack.Beyond.dll.cs` 中存在 `Beyond_Gameplay_Core_SkillDataForMemoryPack`、`Beyond_Gameplay_Core_BuffDataForMemoryPack` 等生成 wrapper，并且 runtime 类型 `Beyond.Gameplay.Core.SkillData` 实现了 `Beyond.MemoryPack.IMemoryPackSerialize`。

不过，VFS 中的当前资源和手头旧 IL2CPP dump 版本并不完全一致。因此浏览器层仍使用更宽泛的 schema-based binary JSON 作为提示文案，避免把所有 `.json` 二进制都提前归类为已完整支持的 MemoryPack。

它不是：

- VFS 加密：VFS 解密已经完成，明文 JSON 和二进制 JSON 都能正常读到解密后内容。
- BSON：BSON 通常以整文档长度开头，样本首 4 字节并不等于文件长度，也没有 BSON 类型码结构。
- MessagePack：MessagePack 会有 fixarray/map/str 等类型头，样本更像“对象成员数 + 原始字段值顺序写入”。
- Protobuf：Protobuf 字段是 tag/value 流，样本中大量 int32 长度前缀字符串和固定首字节成员数特征不典型。

支持“MemoryPack / schema-based 顺序序列化”判断的证据：

- 许多文件首字节稳定落在类似对象成员数的值上，例如 SkillData `0x2f`、BuffData `0x1e`、LevelData `0x2b`、AnimationConfig `0x0f`。
- 紧随其后的数据像字段值顺序写入，而不是带字段名的自描述结构。
- 字符串片段使用 `int32 little-endian length + UTF-8 bytes` 形态，适合作为结构锚点。
- 相同目录下首字节高度一致，符合同一 C# 类型被同一 formatter 写出。
- IL2CPP dump 中 `MemoryPack.Beyond.dll.cs` 明确包含 `SkillDataForMemoryPack` / `BuffDataForMemoryPack` 的 formatter 和 wrapper。

重要限制：MemoryPack 不是完整自描述格式。仅凭二进制本身无法可靠恢复字段名和复杂嵌套类型，必须拿到对应版本的 C# 类型 schema 或 generated formatter 成员顺序。

## 已找到的 schema 入口

`D:\Projects\IL2CPP-Dumper\Arknights Endfield 1.2.4\IL2CPP_Dump_AI\MemoryPack.Beyond.dll.cs` 中能抽取到以下 wrapper：

| Runtime class | Wrapper | 旧 dump 成员数 | 说明 |
| --- | --- | ---: | --- |
| `Beyond.Gameplay.Core.SkillData` | `Beyond_Gameplay_Core_SkillDataForMemoryPack` | 42 | 技能配置根对象 |
| `Beyond.Gameplay.Core.BuffData` | `Beyond_Gameplay_Core_BuffDataForMemoryPack` | 28 | Buff 配置根对象 |
| `Beyond.Gameplay.Core.ActionGroupData` | `Beyond_Gameplay_Core_ActionGroupDataForMemoryPack` | 2 | `timelineActions` / `passiveEventActions` |
| `Beyond.Gameplay.Core.SequenceActionData` | `Beyond_Gameplay_Core_SequenceActionDataForMemoryPack` | 3 | action 列表与主控/非主控限制 |
| `Beyond.Gameplay.Core.CastData` | `Beyond_Gameplay_Core_CastDataForMemoryPack` | 11 | 技能施放、冷却和消耗信息 |

已新增 `tools/extract_memorypack_schema.py`，可从 Il2CppDumper AI-friendly dump 中抽取 wrapper 的 `__field__` setter 顺序，并用运行时 class dump 补全字段类型、offset 和嵌套 wrapper 依赖，生成机器可读的 schema skeleton。

示例：

```powershell
python tools\extract_memorypack_schema.py `
  --dump-root "D:\Projects\IL2CPP-Dumper\Arknights Endfield 1.2.4\IL2CPP_Dump_AI" `
  --output data\reports\memorypack-core-schema.json `
  --max-depth 4
```

注意：旧 dump 的 `SkillDataForMemoryPack` 成员顺序以 `actionGroupData` 开头，而不是 `skillId`。这和二进制样本开头 `0x2f 0x02 ...` 可以解释为：`SkillData` 对象头后，第一项是 `ActionGroupData`，其对象头为 `0x02`。

### 当前 x64/Release dump 校验

2026-07-29 继续检查 `D:\Projects\IL2CPP-Dumper\x64\Release\IL2CPP_Dump_AI\MemoryPack.Beyond.dll.cs`，该 dump 与当前 VFS 资源版本能对上：

| 类型 | x64/Release wrapper 成员数 | VFS 二进制首字节 | AKEDB 当前顶层字段数 | 结论 |
| --- | ---: | ---: | ---: | --- |
| `SkillData` | 47 | 47 | 46 | wrapper 与二进制完全对上；AKEDB JSON 省略了 `buffInputBase` |
| `BuffData` | 30 | 30 | 30 | wrapper、二进制、AKEDB JSON 完全对上 |

`SkillDataForMemoryPack` 的当前成员顺序为：

1. `actionGroupData`
2. `aiExclusiveFrame`
3. `attackRangeType`
4. `blackboard`
5. `buffInputBase`
6. `buffs`
7. `canCastInAir`
8. `canDummyCast`
9. `canMove`
10. `cardAttributeModifier`
11. `castData`
12. `castType`
13. `characterReturnToIdle`
14. `comboSkillUIBigSpriteName`
15. `comboSkillUISpriteName`
16. `dontInterruptCombo`
17. `dummyPositionOffset`
18. `durationFrame`
19. `exclusiveFrame`
20. `hittableAttackRange`
21. `iconBgType`
22. `iconId`
23. `level`
24. `needEnemyOutOfScreenWarning`
25. `needEnemyOutOfScreenWarningOverrideValue`
26. `offsetRecordFrame`
27. `overrideHittableObjAttackRange`
28. `overrideNeedEnemyOutOfScreenWarning`
29. `passiveSkillType`
30. `rootMotionCliffCheck`
31. `selectStrategy`
32. `showNotRecommendState`
33. `skillHighlightCondition`
34. `skillId`
35. `skillName`
36. `skillSpecification`
37. `skillTags`
38. `smartTargetBuffFindSettings`
39. `smartTargetBuffIds`
40. `smartTargetSelectStrategy`
41. `smartTargetTagQuery`
42. `switchToBuffConfig`
43. `switchToCenterBeforeCast`
44. `tagDuringAttach`
45. `toggleBuffs`
46. `uiRangeHints`
47. `useAIExclusiveFrame`

`BuffDataForMemoryPack` 的当前成员顺序为：

1. `abilityEventAction`
2. `addingCooldown`
3. `applyTags`
4. `attributeModifier`
5. `blackboard`
6. `buffEventAction`
7. `damageModifier`
8. `dispelConfig`
9. `duration`
10. `finishOnRepatriate`
11. `globalModifier`
12. `hasAddingCooldown`
13. `hasIcon`
14. `healModifier`
15. `iconConfig`
16. `id`
17. `igniteEventAction`
18. `ignoreCooldownWhenAdding`
19. `ignoreTagImmune`
20. `lifeType`
21. `maxTriggerCnt`
22. `onlyUseSelfTimeDilation`
23. `poiseModifier`
24. `shieldConfigs`
25. `stackingSettings`
26. `tagsAfterTriggerExtendBuffAction`
27. `timelineActions`
28. `triggerInterval`
29. `useTimeDilationDt`
30. `waitFirstTriggerInterval`

进一步用 AKEDB 明文样本做集合校验：

- `SkillData`：schema 有 47 个成员，AKEDB JSON 有 46 个顶层字段，唯一缺失字段是 `buffInputBase`；AKEDB JSON 没有额外字段。
- `BuffData`：schema 30 个成员，AKEDB JSON 30 个顶层字段，字段集合完全一致。
- AKEDB JSON 的输出字段顺序不是 MemoryPack 序列化顺序，不能直接按 JSON 顺序写 decoder。

这说明我们已经找到了当前版本根对象的 MemoryPack schema。后续要做完整 decoder，需要继续递归抽取这些字段引用到的嵌套类型、集合元素类型和多态 action `$type` 对应的 formatter。

当前脚本的处理方式：

- 以 `MemoryPack.Beyond.dll.cs` 的 `*ForMemoryPack` wrapper 为准读取序列化字段顺序。
- 再扫描同一 dump 目录下的运行时 class dump，按字段名匹配字段类型和 offset。
- 兼容 Il2CppDumper 输出里“类型名和字段名粘连”的情况，例如 `ActionGroupDataactionGroupData`。
- 对部分嵌套/泛型类型，Il2CppDumper 可能只输出短类名；脚本只会对非泛化短名做 fallback，并在报告中记录 `runtimeClassLookupFallback`。
- 递归深度由 `--max-depth` 控制，避免一次性展开过多与当前研究无关的类型。

使用当前 x64/Release dump 重新生成 `data/reports/memorypack-core-schema-x64-release.json` 的结果：

- 共识别到 `696` 个 MemoryPack wrapper。
- 共扫描到 `49717` 个 runtime class block。
- 以战斗核心类型为入口、`--max-depth 4` 时展开 `40` 个相关 schema。
- 已展开的 `SkillData` / `BuffData` / `ActionGroupData` / `SequenceActionData` / `CastData` 及其递归依赖均能匹配 runtime 字段，`missingRuntimeFields` 为空。

## 与 AKEDB 已解码 JSON 的对照

AKEDB CDN 当前仍提供已解码的 `SkillData` / `BuffData` JSON，可用于校验字段语义和版本差异。

已验证样本：

| 类型 | 样本 | 二进制首字节 | AKEDB 顶层字段数 | 旧 dump wrapper 成员数 | 结论 |
| --- | --- | ---: | ---: | ---: | --- |
| `SkillData` | `chr_0028_wulfa_ultimate_skill` | 47 | 46 | 42 | 当前资源比旧 dump 新；AKEDB JSON 可能省略了一个空字段，如旧版 `buffInputBase` |
| `BuffData` | `buff_abilityentity_interact_firewall_10m` | 30 | 30 | 28 | 当前资源比旧 dump 新；AKEDB JSON 顶层顺序可直接辅助当前版本 root schema |

`BuffData` 对照中，二进制可读字符串片段 `length`、`dmg_hp_ratio`、`au_int_fire_wall_hit`、`buff_abilityentity_interact_firewall_10m` 都能匹配到 AKEDB 明文 JSON 的具体路径，说明“二进制 payload + AKEDB 明文 JSON + MemoryPack wrapper”三者可以互相校验。

当前推论：

- `MemoryPack.Beyond.dll.cs` 是 schema 的主来源，尤其负责字段顺序。
- AKEDB 已解码 JSON 是版本校验和字段语义对照来源。
- 手头旧 dump 和当前资源版本不一致，若要写完整 decoder，最好先从当前本地游戏客户端重新跑一次 Il2CppDumper。

## 可用信息来源

### 1. AKEDatabase 前端字段知识

AKEDatabase 的前端模块可以浏览已解码的 `SkillData` / `BuffData`，本地仓库中仍有渲染逻辑：

- `AKEDatabase/plugin/js/skill-v2.js`
- `AKEDatabase/plugin/js/buff.js`

其中能看到已解码 JSON 的关键字段名，例如：

SkillData：

- `skillId`
- `level`
- `skillName`
- `castType`
- `skillSpecification`
- `durationFrame`
- `exclusiveFrame`
- `castData`
- `blackboard`
- `skillTags`
- `tagDuringAttach`
- `actionGroupData.timelineActions`
- `actionGroupData.passiveEventActions`

BuffData：

- `lifeType`
- `duration`
- `triggerInterval`
- `maxTriggerCnt`
- `attributeModifier.attributeModifiers`
- `blackboard`
- `stackingSettings`
- `buffEventAction`
- `abilityEventAction`
- `igniteEventAction`
- `timelineActions`

这些字段名可以作为反推 schema 的第一批锚点。

### 2. IL2CPP 元数据 / 运行时代码

完整解析最理想的路径是从游戏客户端的 IL2CPP metadata 或运行时反射/Hook 中恢复 C# 类型定义和对应 formatter。只要拿到类型字段顺序，就可以按顺序解析对象。

### 3. 运行时 dump 或 AKEDB 旧产物

AKEDB CDN 目前仍能读取已解码明文产物，例如：

- `https://data.akedata.wiki/public/Json/SkillData/manifest.json`
- `https://data.akedata.wiki/public/Json/SkillData/chr_0028_wulfa_ultimate_skill.json`

这可以和本地 VFS 二进制同 ID 对照，用来快速验证字段顺序。不过 VFS 浏览工具本身仍应以本地游戏文件为数据源，不应依赖远程 CDN 才能浏览本地资源。

## 后续计划

1. 先在 VFS 浏览器中增加“二进制 JSON 候选”预览：
   - 标识该文件不是文本 JSON。
   - 显示首字节、可能的对象成员数、前若干长度前缀字符串。
   - 给出“疑似 schema-based 二进制配置，需要 schema”的提示。

2. 编写独立的二进制配置探针：
   - 支持读取疑似 object header、collection length、UTF-8 字符串、基础数字。
   - 不尝试完整语义解析，只做结构游标和字符串/数值定位。
   - 用 SkillData、BuffData、AnimationConfig 各选一批样本验证规律。

3. 寻找 schema：
   - 优先用 AKEDB CDN 已解码 JSON 产物和二进制样本做字段顺序对齐。
   - 需要完整自动解码时，再从 IL2CPP metadata 或 formatter 代码恢复字段顺序。
   - 对战斗相关的 SkillData / BuffData 优先投入，LipSync 和 LevelData 可后置。

4. 形成解码 API：
   - `/api/jsondata/preview?id=...`：轻量预览，不要求完整 schema。
   - `/api/jsondata/json?id=...`：在对应 schema 已实现后输出真正 JSON。
   - 对未支持 schema 明确报错，不静默伪装成 JSON。

## 当前结论

这些无法直接按文本 JSON 解读的 `.json` 文件不是“还没解密”，而是 VFS 解密后的二进制序列化数据。格式更应理解为依赖 C# 类型 schema 的顺序二进制配置。要完整解码，关键不在加密，而在恢复类型 schema 和字段顺序。

## 参考来源

- AKEDB 已解码 SkillData 样本：`https://data.akedata.wiki/public/Json/SkillData/chr_0028_wulfa_ultimate_skill.json`
- AKEDB SkillData 清单：`https://data.akedata.wiki/public/Json/SkillData/manifest.json`
- MemoryPack 官方仓库：`https://github.com/Cysharp/MemoryPack`
