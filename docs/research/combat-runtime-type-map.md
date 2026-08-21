# 战斗运行时类型地图

## 文档范围

本文记录 `combat-1.4.4` 证据集里已经能从 IL2CPP 类型元数据直接确认的战斗结构，
以及仍需读取运行时函数体才能确认的问题。它是战斗反推的领域词典，不把方法名推测写成
已经验证的控制流。

证据版本与哈希见 [combat-evidence-manifest.json](combat-evidence-manifest.json)。类型索引由
`tools/index_il2cpp_types.py` 从同版本 dump 生成。AI structured dump 提供全限定类型名和
明确的成员区段，是当前主索引；普通 C# dump 用于交叉检查，并补充其他程序集里的枚举。

## 首批核心类型

| 类型 | 运行时职责 | 已确认的关键状态 |
| --- | --- | --- |
| `Beyond.Gameplay.Core.SkillData` | 单个技能的配置数据 | `skillId`、等级、技能类型数据、`CastData`、持续帧、独占帧、标签、动作组、Buff、黑板和高亮条件 |
| `Beyond.Gameplay.Core.CastData` | 释放参数 | 距离、高度、转向、角度、`cooldownTime`、冷却起始帧、最大蓄力时间和 `CostData` |
| `Beyond.Gameplay.Core.Skill` | 技能运行时实例 | 配置引用、所有者、冷却计时器、持续计时器、是否已经扣费、当前 Buff、目标和技能实例身份 |
| `Beyond.Gameplay.Core.AbilitySystem` | 单个战斗实体的技能与属性容器 | 技能表、当前技能、普通/连携/终结技映射、终结技能量、连招控制器和允许回能标签 |
| `Beyond.Gameplay.Core.BattleManager` | 战斗与队伍级协调 | 队伍技力、技力回复、连携待释放队列、连携事件注册表和暂停状态 |
| `Beyond.Gameplay.Core.AbilitySystem.ComboController` | 输入到技能的映射与缓存 | 基础/运行时/实际按键映射、下一技能请求、允许后续技能集合、派生技能映射和技能替换 |

## 资源账本

### 成本配置

`Beyond.Gameplay.Core.CastData.CostData` 在 AI dump 中以嵌套短名 `CostData` 输出，字段为：

| 字段 | 类型 | 含义边界 |
| --- | --- | --- |
| `costType` | `Beyond.GEnums.CostType` | 成本种类 |
| `costValue` | `float` | 配置成本值 |
| `atbValueThreshold` | `float` | 与 ATB 门槛有关；门槛的精确比较和作用时机尚待函数体确认 |

`Beyond.GEnums.CostType` 只包含 `UltimateSp` 与 `Atb`。因此当前版本的通用技能扣费入口
至少直接建模了终结技能量和技力两类资源；其他角色专属层数更可能通过 Buff、黑板或动作
条件表达，而不是增加第三种通用 `CostType`。

### 技力 ATB

ATB 位于 `BattleManager`，不是某个干员的 `AbilitySystem`：

- `m_atb`：当前技力；
- `m_returnedAtb`：返还技力；
- `atbMax`：技力上限；
- `m_atbRecoverPauseTime`：回复暂停时间；
- `m_atbRecoverValue`：回复值。

候选读写入口包括 `GainAtb`、`CostAtb`、`RawSetAtb` 和 `_UpdateAtb`。这支持“技力是
队伍共享资源”的结构性结论，但扣费失败、返还和暂停回复的准确顺序仍需函数体或运行时
探针确认。

### 终结技能量 USP

USP 位于每个 `AbilitySystem`：

- `m_ultimateSp`：当前终结技能量；
- `ultimateSp` / `maxUltimateSp`：读写当前值与读取上限；
- `onUspChange`：数值变化通知；
- `m_allowedUspRecoverTags`：允许回能的标签集合；
- `RequestAllowedUspRecoverTag`、`RevertAllowedUspRecoverTag`、
  `_RefreshAllowedUspRecoverTags`：动态维护回能许可。

这解释了为何“强化期间禁止回能”可以是实体状态，而不是技能描述层的特殊判断。
`BattleFormula.CalculateUltimateSp` 是终结技能量增量公式的候选入口。

## 技能释放候选链

当前可由类型和签名确认的候选节点如下：

```text
玩家输入或 AI
  -> AbilitySystem.TryCastSkill(...)
  -> AbilitySystem.CanCastSkill(...)
  -> Skill.IsAvailable()
       -> Skill.CheckCd()
       -> Skill.CheckCost()
       -> Skill.CheckTag()
       -> Skill.CheckState()
  -> AbilitySystem.CheckCanInterruptCurSkill(...)
  -> AbilitySystem.BeforeCastStart(...)
  -> Skill.DoCast(...)
       -> Skill._ApplyCost(...)
       -> AbilitySystem.ComboControllerOnSkillCastStart(...)
  -> Skill.CastEnd(...) / Skill.Interrupt(...)
  -> AbilitySystem._OnCastEnd(...)
```

这张图目前是**入口候选图**，不是已经恢复的直接调用图。特别需要继续确认：

1. `CanCastSkill` 是否总会调用 `Skill.IsAvailable`，以及各检查的短路顺序；
2. `CheckCost` 是否只检查余额，还是会预留资源或改变门槛状态；
3. `_ApplyCost` 在技能开始前后哪个精确时点执行；
4. `skipApplyCost` 的所有调用来源，以及它是否只用于系统动作和调试路径；
5. 技能取消、被打断和启动失败时是否返还 ATB，`m_appliedCost` 如何防止重复扣费。

## 连携排队结构

`BattleManager` 明确维护以下容器：

- `m_comboSkillEvents`：事件到连携条件信息集合的注册表；
- `m_pendingComboSkill`：干员到待释放连携记录的字典；
- `m_toRemovePendingComboSkill`：待移除记录；
- `m_triggeredComboSkillFromLastFrame`：上一帧触发记录；
- 全局和按干员的连携暂停状态。

候选流程为 `TriggerComboSkillEvent` -> `PendingComboSkill` ->
`_UpdatePendingComboSkill` -> `CastPendingComboSkill`。`GetRemainComboSkillPendingTime` 同时输出
剩余时间和 `canCast`，说明“窗口已经打开”与“当前允许释放”在运行时是两个不同状态。
这与游戏中连携窗口需要排队、不能任意越过先打开窗口的现象一致；具体排序键和同帧优先级
仍需读取函数体确认。

## 内部枚举不等于编辑器技能类型

`Beyond.Gameplay.SkillType` 包含 `PassiveSkill`、`Attack`、`BreakingAttack`、
`NormalSkill`、`AttachSkill`、`Dodge`、`ComboSkill`、`UltimateSkill` 和
`ExtraActiveSkill`。其中 `Dodge` 是客户端内部分类的证据，不能据此推导游戏 UI 存在一个
可独立配置或排轴的“闪避技能”；领域 IR 仍应按实际可操作动作和配置身份建模。

## RVA 的当前限制

自定义 Dumper 的解析顺序是：先尝试动态查找 `il2cpp_method_get_pointer`，不存在时读取
`MethodInfo` 对象的第一个指针。当前磁盘 `GameAssembly.dll` 的 393 个导出中没有
`il2cpp_method_get_pointer`，因此现有 dump 的 RVA 极可能来自 `MethodInfo` 首指针，而
不是一个标准 IL2CPP 导出 API 的返回值。它仍不是可靠的磁盘 PE 函数入口：

- 部分 RVA 落在磁盘文件的 `.rdata`；
- 部分落在可执行节中，但不是合法函数边界；
- Dumper 运行时看到的模块大小与磁盘 PE `SizeOfImage` 不一致；
- `GameAssembly.dll` 含 `il2cpp`、`.Sgxm*`、`.tvm0` 等保护相关区段。

因此当前可以可靠使用类型名、字段 offset、方法签名和运行时返回的地址身份，但不能直接
对磁盘文件中同 RVA 的字节下反编译结论。下一步需要在游戏运行并完成初始化后读取对应
进程内存，检查方法指针是实际代码、跳板还是描述符，再决定静态反编译或最小 Hook 路线。
后续 Dumper 探针还需同时记录 `MethodInfo` 地址、首部若干指针和 RVA 的解析来源，不能只
保存首指针换算后的 RVA。

批量探针定义在 `combat-runtime-probes.json`。游戏进入可操作场景后可执行：

```powershell
python -m tools.probe_runtime_rvas `
  docs/research/combat-runtime-probes.json `
  --output data/research-artifacts/combat-1.4.4/runtime-probes.json
```

报告会记录每个地址的内存区域权限、前导字节、首个 64 位值，以及该值是否仍指向
`GameAssembly.dll`。这一步用于区分真实可执行入口、跳板和只读描述符。

## 可复现命令

```powershell
python tools/index_il2cpp_types.py `
  data/research-artifacts/combat-1.4.4/dumps/ai/Gameplay.Beyond.dll.cs `
  --output data/research-artifacts/combat-1.4.4/derived/indexes/gameplay-types-ai.json

python tools/index_il2cpp_types.py `
  data/research-artifacts/combat-1.4.4/dumps/normal/Common.Beyond.dll.cs `
  --output data/research-artifacts/combat-1.4.4/derived/indexes/common-types.json `
  --match 'CostType|SkillType|Ability'
```

生成结果位于被忽略的 `data/research-artifacts/`，不提交体积较大的原始 dump 和派生索引。
