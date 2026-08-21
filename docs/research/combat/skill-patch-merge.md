# SkillData 合并、覆盖与运行时 Patch

本文追踪 1.4.4 客户端中 `SkillData` 从 VFS 基础配置到运行时 `Skill` 实例的对象流，重点回答
等级、天赋、潜能、形态/Mode 和两种 `SkillPatchData` 的合并顺序与对象语义。

## 结论

1. **[已确认] 等级、天赋、潜能都不修改缓存的基础 `SkillData`。** 等级阶段先
   `DeepClone()`，天赋和潜能阶段又各自 `DeepClone()`，然后只修改新对象。最终
   `Skill.m_data` 指向派生出的 `SkillData`。
2. **[已确认] 等级 Patch 不是把 1..N 级逐项累计。**
   `DataManager.TryGetSkillPatchData(skillId, level)` 直接选择
   `SkillPatchDataBundle[level - 1]`，该条记录覆盖克隆对象的等级字段和黑板。
3. **[已确认] 天赋先于潜能。** 角色路径的顺序是：基础配置 → 等级 → 天赋 → 潜能 →
   图标修正 → `CreateSkillOptions.extraBlackboard`。同一数值字段上的 Add、Multiply、
   Overwrite 按实际遍历顺序逐条作用；所以“潜能覆盖天赋”只在后写的 Overwrite 或后续运算
   的意义上成立，并不是无条件整块替换。
4. **[已确认] 运行时 `AbilitySystemPatchData.SkillPatchData` 不是配置表中的
   `Beyond.Cfg.SkillPatchData`。** 它只保存 `skillObjPath` 与黑板列表，不修改
   `SkillData`，而是在 `Skill._OnCreate` 中叠到新建的运行时黑板上。
5. **[已确认] 初次创建时同键黑板的后写优先级为：**
   `SkillData.blackboard` < 运行时 Patch < `extraBlackboard` < `owner.sourceBlackboard`。
   `Blackboard.Assign` 命中已有键时替换原位置的值，因此同键后写者胜出。
6. **[已确认] Mode 不参与 `SkillData` 合并。** 它启停已经创建的技能、切换普攻列表、额外
   被动、命令映射、移动/模型/动画状态；`ModeData` 没有 `SkillData` 或 Patch 字段，
   `_ApplyModeChange` 也不调用本页的生成/刷新方法。
7. **[已确认，受 IFix 边界限制] 角色等级/天赋/潜能刷新复用原 `Skill` 实例，但重建
   `m_data`、黑板与计时器。** 当前原生调用者向 `RefreshRuntimeData` 传入空的运行时 Patch
   和空的 `extraBlackboard`，所以刷新后的初始黑板只从新 `SkillData` 与
   `sourceBlackboard` 重建。1.4.4 进程是否为这些方法启用了 IFix 热补丁仍未知。

## 证据范围与标记

- **[已确认]**：1.4.4 dump 的类型/字段，与同版本 runtime snapshot 中的原生函数体或 VFS
  索引互相印证。
- **[推导]**：由已确认的分支、对象布局和通用容器语义推出，但仍缺少直接运行时探针。
- **[未知]**：当前证据不能唯一决定，或存在 IFix 覆盖边界。

本页只使用仓库内 1.4.4 dump、metadata、VFS/API 索引、本地解码样本和仓库反汇编工具。
Endaxis 旧 TypeScript 没有作为事实来源。

### 版本与工件边界

| 工件 | 身份 | 用途与限制 |
| --- | --- | --- |
| `data/research-artifacts/combat-1.4.4/binaries/GameAssembly.dll` | 1.4.4 客户端程序集 | RVA 与类型 dump 的模块身份 |
| `C:\Users\sqwat\Projects\zmd\combat-runtime-dumps\1.4.4\IL2CPP_GameAssembly.runtime.bin` | SHA-256 `7e7377…` | 原生函数体快照；RVA 映射到 GameAssembly 模块 |
| `data/research-artifacts/combat-1.4.4/binaries/global-metadata.dat` | SHA-256 `90c58e26…` | 结构兼容但尚未证明与快照成对；仅辅助枚举/签名，关键分支另由原生函数体复核 |
| `data/research-artifacts/combat-1.4.4/binaries/metadata-1.4.4/global-metadata.dat` | SHA-256 `5992…` | 已排除：Torappu/明日方舟 metadata，不作为本页证据 |
| `data/research-artifacts/combat-1.4.4/dumps/ai/Gameplay.Beyond.dll.cs` | 类型 dump | Gameplay 类型、字段偏移、方法 RVA |
| `data/research-artifacts/combat-1.4.4/dumps/normal/Common.Beyond.dll.cs` | 类型 dump | 配置表类型、字段和 getter RVA |

VFS 索引确认的逻辑配置路径如下。当前数据库记录的分块文件位于已经离线的外部盘，因此本轮
能验证索引、长度和哈希，但不能重新读取三个表的原始字节。

| 配置 | VFS 逻辑路径 | 长度 | MD5 |
| --- | --- | ---: | --- |
| 单技能基础数据样本 | `JsonData/Data/Json/SkillData/chr_0004_pelica_normal_skill.json` | 12,354 | `5540A425C5A2AE4508B1482A4CF19A96` |
| 等级 Patch 表 | `Table/Data/TableCfg/SkillPatchTable.bytes` | 1,009,488 | `A20F6D65BEDC8D5F3A7A494B5FA99791` |
| 天赋/潜能效果表 | `Table/Data/TableCfg/PotentialTalentEffectTable.bytes` | 104,468 | `AB5EE4C89BBAEC704FD5DC3D4B4AB820` |
| 角色成长/天赋节点表 | `Table/Data/TableCfg/CharGrowthTable.bytes` | 273,232 | `957BF7DE5AF8BF892A2103FF511DFD35` |
| 潜能解锁级别表 | `Table/Data/TableCfg/CharacterPotentialTable.bytes` | 20,500 | `B6A868A4C45BEF5E804A62658B1CD51B` |

## 两个同名但不同层级的 SkillPatchData

| 类型 | 来源 | 字段 | 使用阶段 | 对象语义 |
| --- | --- | --- | --- | --- |
| `Beyond.Cfg.SkillPatchData` | `SkillPatchTable` | `skillId`、`level`、名称/图标、费用、冷却、充能、`blackboard` 等 | `CalculateLevelSkillData` | 对基础 `SkillData` 的**克隆**写入字段 |
| `Beyond.Gameplay.Core.AbilitySystemPatchData.SkillPatchData` | `AbilitySystem.CreatePatchData` 及派生实现 | `skillObjPath @ 0x10`、`List<DataPair> blackboard @ 0x18` | `Skill._OnCreate` | 对运行时黑板的**叠层**，不写 `SkillData` |

因此不能把 `SkillInitParam.patchData` 理解成“等级 Patch”。它只接受第二行的运行时 Patch。

## 关键类型、字段与方法

### 创建输入与最终实例

| 类型/方法 | 关键字段或行为 | RVA |
| --- | --- | ---: |
| `CreateSkillOptions` | `skillId @ 0x10`、`level? @ 0x18`、`skillData @ 0x20`、`extraBlackboard @ 0x30`；对象大小 `0x40` | — |
| `CreateSkillOptions.GetSkillData` | `EnsureSkillId` 后进入 `BattleDataLoader.LoadSkillData` | `0x0383DFC0` |
| `BattleDataLoader.LoadSkillData` | 读单技能资源并使用 `m_skillDataCache @ 0x70` 缓存基础对象 | `0x0383E0C0` |
| `SkillInitParam` | `skillData @ 0x10`、运行时 `patchData @ 0x18`、`extraBlackboard @ 0x20`、`serverInstId? @ 0x28` | ctor `0x0383B970` |
| `AbilitySystem._CreateSkill` | 选取/生成最终 `SkillData`，查找运行时 Patch，构造 `SkillInitParam` | `0x0383BA70` |
| `Skill.Create` / `Skill._OnCreate` | 建立实例；保存 `m_data` 并构造运行时黑板、计时器 | `0x033B6C30` / `0x033B7430` |
| `Skill` | `m_data @ 0x18`、运行时黑板 backing field `@ 0xC8`、`onCreateBlackboard @ 0xD0`、`skillId @ 0xF0`；没有 Patch 字段 | — |

`CreateSkillOptions.skillData @ 0x20` 虽然存在，但当前 `GetSkillId` / `GetSkillData` 原生体按
`skillId` 取缓存数据，没有观察到读取该字段。**[未知]** 它是否仅供 IFix、序列化兼容或其他
未覆盖调用点使用。

### SkillData 与可修改字段

`SkillData` 对象大小 `0x128`，有 47 个实例字段。合并路径使用的关键偏移为：

| 字段 | 偏移 |
| --- | ---: |
| `skillId` / `level` / `skillName` | `0x10` / `0x18` / `0x20` |
| `iconId` / `iconBgType` | `0x28` / `0x30` |
| `castData` | `0x48` |
| `actionGroupData` | `0xC0` |
| `blackboard` | `0xE8` |
| `switchToBuffConfig` | `0x118` |

`CastData` 的 `cooldownTime @ 0x38`、`maxChargeTime @ 0x40`、`costData @ 0x48`，以及
`CostData.costType @ 0x10`、`costValue @ 0x14` 是等级/成长修正的结构化目标。

`SkillData.DeepClone @ 0x0383EEB0` 会分配新 `SkillData`，为本路径会写入的 `CastData`、
`CostData` 和黑板列表建立独立对象。**[已确认]** 这足以证明本页所列修改不会回写缓存的基础
对象；**[未知]** 其他不参与本页写入的嵌套动作图是否全部递归复制，不能仅凭方法名断言。

## 完整对象流与覆盖顺序

### 角色技能创建主链

```text
AbilitySystem._CreateSkill(CreateSkillOptions)                  0x0383BA70
  ├─ CharInfo.TryGetActiveSkillData / TryGetServerSkillData
  │    └─ 命中时直接使用预计算/服务端 SkillData
  └─ 未命中：SkillUtil.CreateSkillDataWithCharInfo             0x03842940
       ├─ CreateSkillOptions.GetSkillData                       0x0383DFC0
       │    └─ BattleDataLoader.LoadSkillData                   0x0383E0C0
       ├─ CalculateLevelSkillData                               0x0383EB50
       ├─ CalculateTalentModifiedSkillData                      0x038431E0
       ├─ CalculatePotentialModifiedSkillData                   0x03841FB0
       ├─ _AssignSkillIcon
       └─ BlackboardExtensions.Assign(extraBlackboard)
  ├─ GetSkillObjRelativePath
  ├─ AbilitySystem._GetSkillPatchData                           0x0383B9B0
  ├─ new SkillInitParam(finalData, runtimePatch, extraBB, ...)
  └─ Skill.Create
       └─ Skill._OnCreate                                       0x033B7430
```

`_CreateSkill` 的角色分支优先尝试 `CharInfo` 已预计算的 active data；未命中且存在服务端实例
ID 时尝试 server data；两者都不可用才现场调用 `CreateSkillDataWithCharInfo`。所以表合并可能
发生在 `CharInfo` 预计算阶段，而不一定发生在 `_CreateSkill` 当前栈帧内。

普通非角色路径调用 `SkillUtil.CreateSkillData @ 0x0383DEF0`，顺序只有：基础数据 → 等级 →
`extraBlackboard`。

### 按阶段的对象与写入

| 顺序 | 阶段 | 输入 → 输出 | 是否修改输入 | 同字段语义 |
| ---: | --- | --- | --- | --- |
| 0 | VFS/缓存 | 配置字节 → 缓存基础 `SkillData` | 缓存保存共享对象 | 后续阶段只读 |
| 1 | 等级 | 基础对象 → clone L | 否 | 选中的等级记录直接写结构字段；表黑板后写 |
| 2 | 天赋 | clone L → clone T | 否 | 节点/效果遍历顺序逐条运算 |
| 3 | 潜能 | clone T → clone P | 否 | 解锁项/效果遍历顺序逐条运算，晚于天赋 |
| 4 | 图标修正 | clone P → 同一 clone P | 是，修改派生对象 | 当前仅确认调用位置 |
| 5 | options extra | clone P 的黑板 → 同一黑板 | 是，修改派生对象 | 同键覆盖此前等级/天赋/潜能黑板 |
| 6 | 运行时 Patch | 新运行时黑板 → 同一黑板 | 不修改 `SkillData` | 同键覆盖 `SkillData.blackboard` |
| 7 | runtime extra | 运行时黑板 → 同一黑板 | 不修改 `SkillData` | 同键覆盖运行时 Patch |
| 8 | source Blackboard | 运行时黑板 → 同一黑板 | 不修改 `SkillData` | 最后写，同键优先级最高 |

阶段 5 在现场生成路径中发生；`_CreateSkill` 随后仍把同一个 `extraBlackboard` 放入
`SkillInitParam`，所以它会在阶段 7 再应用一次。命中 `CharInfo` active/server data 时，当前栈
没有阶段 5，但仍执行阶段 7。重复应用对直接赋值是幂等的；若调用方构造的列表含重复键，
仍按列表顺序以后项为准。

## 等级：选择单条配置，克隆后覆盖

### 配置入口

- `Beyond.Cfg.Tables.SkillPatchTable.CreateMap @ 0x04907CD0`
- `Beyond.Cfg.Tables.SkillPatchTable.GetConfig @ 0x04D63CC0`
- `Beyond.Cfg.SkillPatchDataBundleList.SkillPatchDataBundle @ 0x03840720`
- `DataManager.TryGetSkillPatchData @ 0x03840510`

`TryGetSkillPatchData` 先按 `skillId` 取 bundle，拒绝 `level <= 0` 或超过 bundle 长度的请求，
然后只取 `bundle[level - 1]`。**[已确认] 不会依次合并 1、2、…、N 级记录。**

### `SkillUtil.CalculateLevelSkillData @ 0x0383EB50`

1. 调用 `SkillData.DeepClone @ 0x0383EEB0`。
2. 调用 `TryGetSkillPatchData(clone.skillId, level, out patch)`。
3. 缺少记录时返回 clone，不返回缓存原对象。
4. 命中时依次写入：
   `skillName`、`level`、`iconId`、`iconBgType`、`costType`、`costValue`、
   `cooldownTime`、`maxChargeTime`。
5. 最后把表中 `blackboard` 用 `Blackboard.Assign` 合到 clone 的黑板。

这说明等级阶段是**复制后修改**，不是原对象修改，也不是在 `Skill` 上延迟求值的运行时叠层。

## 天赋与潜能：逐条运算到新的 clone

### 配置入口与枚举

| 配置类型 | 关键成员 |
| --- | --- |
| `Beyond.Cfg.CharGrowthData` | `charId`、`talentNodeMap`、`skillGroupMap` |
| `Beyond.Cfg.TalentNodeData` | `nodeId`、`nodeType`、`passiveSkillNodeInfo` |
| `Beyond.Cfg.PassiveSkillNodeData` | `talentEffectId` |
| `Beyond.Cfg.CharacterPotentialList` | `potentialUnlockBundle` |
| `Beyond.Cfg.PotentialUnlockData` | `level`、`potentialEffectId` |
| `Beyond.Cfg.PotentialTalentEffectDataBundle` | 有序 `dataList` |
| `Beyond.Cfg.PotentialTalentEffectData` | `activeCondition`、`modifyType`、`skillParamModifier`、`skillBbModifier` 等 |

结构兼容 metadata 给出的相关值为：`TalentNodeType.PassiveSkill = 4`；
`PotentialModifyType.ChangeSkillParam = 2`、`ChangeSkillBlackboard = 3`；
`SkillParamModifyType.Add = 1`、`Multiply = 2`、`Overwrite = 3`。关键比较值已在原生函数体中
独立复核，避免仅依赖尚未配对的 metadata。

### 天赋 `SkillUtil.CalculateTalentModifiedSkillData @ 0x038431E0`

1. 对等级结果再次 `DeepClone()`。
2. 通过 `CharUtils.GetVirtualCharTemplateId` 归一角色 ID，查 `CharGrowthData`。
3. 从 clone 黑板建立临时 `Blackboard`。
4. 以调用方传入 `HashSet<string> talentNodeIds` 的枚举顺序遍历；只接受
   `TalentNodeType.PassiveSkill (4)`。
5. 取 `passiveSkillNodeInfo.talentEffectId`，调用
   `_CalculatePotentialTalentEffect @ 0x03755780`。
6. 把临时黑板物化回 clone 的 `blackboard` 列表。

**[未知]** `HashSet` 的配置插入顺序及运行时枚举稳定性没有被当前静态证据固定；若多个已激活
天赋对同字段使用 Overwrite，最终胜者需要运行时样本确认，不能假设按 UI 节点编号排序。

### 潜能 `SkillUtil.CalculatePotentialModifiedSkillData @ 0x03841FB0`

1. 对天赋结果再次 `DeepClone()`。
2. 按 `CharacterPotentialList.potentialUnlockBundle` 数组顺序遍历。
3. 对每个 `unlock.level <= potentialLevel` 的条目调用同一个
   `_CalculatePotentialTalentEffect`。
4. 把临时黑板物化回 clone。

所以多个已解锁潜能是累计应用的，与等级表“只选第 N 条”的行为不同。

### 单条效果 `_CalculatePotentialTalentEffect @ 0x03755780`

方法先按 `PotentialTalentEffectDataBundle.dataList` 数组顺序遍历，并对每项执行
`CheckSkillConditions @ 0x037568F0`。在 `SkillData` 生成路径中只处理：

- `ChangeSkillParam (2)`：要求 modifier 的 `skillId` 等于当前技能 ID；可写
  `CostValue`、`CoolDown`、`MaxChargeTime`。充能次数先 `MathUtil.Round(value, 0)` 再转整数。
  `CoolDownDisplay` 不写入 `SkillData`，另有独立显示修正路径。
- `ChangeSkillBlackboard (3)`：也先匹配 `skillId`。非空 `stringValue` 直接 Assign，忽略
  数值 `modifyType`；数值项从当前值开始执行 Add/Multiply/Overwrite，缺失键从零开始。

`PotentialUtil.CalculatePotentialModifiedValue @ 0x04668A40` 的原生语义为：

```text
Add       => old + modifier
Multiply  => old * modifier
Overwrite => modifier
其他      => old
```

`AddPassiveSkill`、`ModifyAttr`、`AddBuff` 等效果由其他角色功能路径处理，不在这里改写当前
`SkillData`。附加被动技能是新增/重建另一个技能实例，不应扁平化成当前技能的字段 Patch。

## Blackboard 的同键覆盖规则

`Blackboard.Assign(IList<DataPair>) @ 0x033B9370` 按输入列表顺序调用
`_AssignInternal @ 0x0307A910`：

- 已存在同名键：保留旧条目的 `isDynamic` 位，在原索引替换值/类型；
- 不存在：追加新条目；
- 因此同一个输入列表内重复键以后项为准，不同阶段间也是后执行阶段为准。

数值成长 modifier 有一层额外语义：每条 modifier 都读取上一步当前值，所以 Add/Multiply 是
顺序组合，而不是简单的“最后一项覆盖”。字符串 modifier 和 `Overwrite` 才是直接替换。

结构字段与黑板是两个命名空间。运行时 Patch、`extraBlackboard`、`sourceBlackboard` 都只能
覆盖黑板键，不能覆盖 `SkillData.castData.cooldownTime` 等结构字段。

## 运行时 Patch：实例黑板叠层

### Patch 的来源

`AbilitySystem._DoInit @ 0x0383A3C0` 在 `_InitSkills` 之前调用虚方法
`CreatePatchData @ 0x03422690`，并把结果保存到 `m_patchData @ 0x198`。
`AbilitySystemPatchData` 的 `attributePatch @ 0x10` 与
`List<SkillPatchData> skillPatch @ 0x18` 是实体级 Patch 容器。

基础 `AbilitySystem.CreatePatchData` 只建立属性 Patch。派生实现可以填技能 Patch：

- `AbilitySystemForInt.CreatePatchData @ 0x06E629BC` 先调用基类，创建新的
  `skillPatch` 列表，调用 `_GetSkillPatchDataBlackboard @ 0x06E645C8`，再为属性中取得的
  多个技能对象路径各创建一项运行时 `SkillPatchData`；这些项共享所求得的黑板列表。
  其数据输入包括 `AbilitySystemForIntData.useSelfBlackboard @ 0x108` 与
  `skillBlackboardDataPairs @ 0x110`。
- `AbilitySystemForNpc.CreatePatchData @ 0x03423590` 从
  `AbilitySystemForNpcData.NpcData.blackboard @ 0x28` 的 `SkillBBData` 项构造路径到黑板的
  Patch 项。
- `AbilitySystemForIntResource.CreatePatchData @ 0x06E624C8` 也覆盖该虚方法。

这进一步证明运行时 Patch 是**实体/组件实例配置产生的技能对象路径 → 黑板叠层**，不是
`SkillPatchTable` 的等级行。

### 查找与应用

`AbilitySystem._GetSkillPatchData @ 0x0383B9B0` 在 `m_patchData.skillPatch` 中按
`skillObjPath` 与 `GetSkillObjRelativePath(options)` 的结果匹配。当前函数体只返回一个 Patch，
没有把多个同路径条目合并的循环。

**[推导]** 该调用形状与 `List.Find`/首个匹配一致；**[未知]** 通用闭包目标尚未恢复符号，
所以配置若人为包含多个完全相同路径，精确是“首项”还是其他单项选择仍需探针确认。正常构造
路径按每个技能对象路径创建一项，没有发现依赖重复路径叠加的证据。

### `Skill._OnCreate @ 0x033B7430`

原生函数体的顺序为：

```text
m_data = finalSkillData
direct = new ActionBlackboard(m_data.blackboard)
blackboard = new BlackboardWithOwner(direct, ...)
blackboard.Assign(runtimePatch?.blackboard)
blackboard.Assign(extraBlackboard)
onCreateBlackboard = snapshot(blackboard)
blackboard.Assign(owner.sourceBlackboard)
create duration/cooldown timers from m_data
```

运行时 Patch 没有保存在 `Skill` 的独立字段中，也没有写入 `m_data.blackboard`；它只在创建/刷新
时被物化进运行时黑板。`onCreateBlackboard` 在 source 合入前拍快照，说明 source 是可更换的
最后一层，而 Patch 和 extra 属于实例创建基线。

`Skill.AssignSourceBlackboard @ 0x035F5720` 会先从 `onCreateBlackboard` 恢复，再 Assign 新的
source，然后通知 `Ability`。这再次确认 source 同键最后胜出，且不会永久污染创建基线。

## Mode/形态：选择层，不是 Patch 层

`AbilitySystemData.modeConfig @ 0x18` 进入 `AbilitySystem.m_allModes @ 0x190`。`ModeData`
对象大小 `0xC0`，包含：

- `modeId @ 0x10`、`defaultEnable @ 0x18`、`modeLayer @ 0x20`、`parentModeId @ 0x28`；
- `addExtraPassiveSkill @ 0x30`、`extraPassiveSkillId @ 0x38`；
- `overrideNormalAttackList @ 0x52`、`normalAttackList @ 0x58`；
- 移动、动画、模型、`overrideCmdMapping @ 0xB0`、`cmdMapping @ 0xB8`。

`AbilitySystem._ApplyModeChange @ 0x03DE5DD0` 查找已经存在的技能后调用 Enable/Disable，并更新
连段/命令映射、额外被动及表现状态。函数体没有调用 `CreateSkillData`、三个 Calculate 方法、
`RefreshRuntimeData` 或 `_GetSkillPatchData`。

因此 **[已确认] Mode 与等级/天赋/潜能不存在同一个 `SkillData` 字段上的覆盖优先级**。Mode
决定当前选择/启用哪一个既有技能；被选中技能各自拥有已经完成合并的 `SkillData` 和运行时
黑板。**[未知]** 个别模式切换前后的业务事件是否会间接触发其他系统重算角色成长数据，需按
具体事件链另行验证，但不属于 `_ApplyModeChange` 自身语义。

## 等级、天赋、潜能变化后的刷新

`Skill.RefreshRuntimeData @ 0x06CA7A08` 对同一个 `Skill` 调用 `_OnCreate(newData,
patchData, extraBlackboard)`；若实例已初始化，再调用 `_DoRefreshRuntimeData(false)
@ 0x033B6520` 重新载入 Ability 动作组与切换配置。技能对象身份不变，但 `m_data` 指针、
黑板和计时器都会重建。

已确认的事件入口包括：

| 事件/刷新 | RVA | 作用 |
| --- | ---: | --- |
| `AbilitySystem._OnSkillLevelChanged` | `0x06CB4B64` | 进入 active SkillData 刷新 |
| `AbilitySystem._OnTalentPassiveSkillChanged` | `0x06CB4C24` | 刷新天赋影响的主动技能与 Buff |
| `AbilitySystem._OnPotentialLevelChanged` | `0x06CB48E8` | 刷新潜能影响的主动技能 |
| `AbilitySystem._OnNormalSkillChange` | `0x06CB4864` | 普通技能选择变化 |
| `AbilitySystem._RefreshCharActiveSkillData` | `0x06CB4DBC` | 找现有 Skill，必要时中断当前施放，再刷新数据 |
| `AbilitySystem._RefreshCharDefaultPassiveSkillData` | `0x06CB501C` | 刷新默认被动实例 |
| `CharInfo._RefreshActiveSkillData(string, int)` | `0x072EE5DC` | 用 `CreateSkillDataWithCharInfo` 生成替换项并写 active 列表 |
| `CharInfo._RefreshTalentModifiedSkillData` | `0x072EF424` | 识别受天赋影响的技能 |
| `CharInfo._RefreshPotentialModifiedSkillData` | `0x072EE974` | 识别受潜能影响的技能 |

两个 `AbilitySystem` 原生刷新调用点向 `Skill.RefreshRuntimeData` 传入的 Patch 与 extra 参数均为
null。由此得到当前原生体的刷新结果：

```text
新 SkillData.blackboard
  -> onCreateBlackboard 快照
  -> owner.sourceBlackboard（最后覆盖）
```

即创建时的运行时 Patch 与 extra 叠层不会自动重放。这个行为非常容易在静态模型中遗漏。

**[未知]** 所有关键方法入口都先检查 `WrappersManagerImpl.IsPatched(id)`。现有 snapshot 能证明
原生 fallback 函数体和热补丁边界，不能证明抓取进程当时这些 ID 的 IFix 状态。若 1.4.4 热补丁
接管刷新方法，实际重放行为可能不同，必须以 `IsPatched`/wrapper 探针或 IFix 方法体为准。

## 可实现的优先级模型

在没有 IFix 覆盖的原生角色现场生成路径中，可按下式实现：

```text
data0 = LoadSkillData(skillId)                       // 缓存，只读
data1 = DeepClone(data0); ApplyLevelRow(data1, N)    // 只选 N-1 行
data2 = DeepClone(data1); ApplyTalentEffects(data2)  // 实际枚举顺序
data3 = DeepClone(data2); ApplyPotentialEffects(data3)// unlock 数组顺序
Assign(data3.blackboard, options.extraBlackboard)

runtimeBB = Copy(data3.blackboard)
Assign(runtimeBB, abilitySystemRuntimePatch.blackboard)
Assign(runtimeBB, options.extraBlackboard)
onCreateBB = Copy(runtimeBB)
Assign(runtimeBB, owner.sourceBlackboard)
```

若使用 `CharInfo` active/server data，从相应预计算对象开始，跳过当前栈内的 data0..data3，
但 `Skill._OnCreate` 的运行时叠层顺序不变。

## 仍缺的证据

1. **[未知] IFix 激活状态。** 需要在同一 1.4.4 进程读取关键 method ID 的
   `WrappersManagerImpl.IsPatched` 结果；若为 true，还需提取 wrapper 实际调用体。
2. **[未知] 运行时对象身份探针。** 当前原生函数体已证明 clone 分配与写入目标；仍可用探针
   同时记录缓存 base、L/T/P clone、`Skill.m_data` 的指针，作为最直观的动态复核。
3. **[未知] 冲突样本。** 需要找一个同技能同键同时出现在等级、两个天赋、多个潜能、运行时
   Patch、extra、source 的 1.4.4 样本，逐阶段转储值，尤其验证 HashSet 天赋顺序。
4. **[未知] 重复运行时路径。** `_GetSkillPatchData` 的通用谓词目标尚未符号化；需构造重复
   `skillObjPath` 或 hook 返回项确认精确单项选择规则。
5. **[未知] 刷新事件先后。** 已确认 `CharInfo` 的替换方法和 `AbilitySystem` 的消费方法，尚未
   动态证明同一事件中 active data 更新与 Skill 刷新的监听器先后。
6. **[当前不可重读] 表原始字节。** VFS SQLite 有路径、哈希、长度和 chunk 记录，但现配置的
   外部盘 chunk 不在线；需恢复对应块文件后抽取实际冲突记录。

## 只读复核入口

原生体分析使用仓库工具，不修改仓库内容：

```powershell
python -m tools.analyze_runtime_snapshot `
  C:\Users\sqwat\Projects\zmd\combat-runtime-dumps\1.4.4\IL2CPP_GameAssembly.runtime.bin `
  C:\Users\sqwat\Projects\zmd\combat-runtime-dumps\1.4.4\IL2CPP_GameAssembly.runtime.json `
  data\research-artifacts\combat-1.4.4\derived\indexes\gameplay-types-ai.json `
  --match "SkillUtil|AbilitySystem|Skill::|Blackboard" `
  --output <scratch-output.json>
```

定位字段和 RVA：

```powershell
rg -n "CreateSkillDataWithCharInfo|CalculateLevelSkillData|CalculateTalentModifiedSkillData|CalculatePotentialModifiedSkillData|_GetSkillPatchData|RefreshRuntimeData|_ApplyModeChange" `
  data/research-artifacts/combat-1.4.4/dumps/ai/Gameplay.Beyond.dll.cs

rg -n "SkillPatchTable|PotentialTalentEffectData|SkillParamModifierData|CharGrowthData|PotentialUnlockData" `
  data/research-artifacts/combat-1.4.4/dumps/normal/Common.Beyond.dll.cs
```
