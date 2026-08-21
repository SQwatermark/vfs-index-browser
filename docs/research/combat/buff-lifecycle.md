# Buff 创建、启用、失活与结束生命周期

## 为什么要区分这些阶段

“创建了 Buff”不能直接等同于“Buff 已经生效”。运行时至少区分：

1. 解析配置并分配实例；
2. 初始化实例；
3. 首次启动；
4. 启用或因叠层优先级暂时失活；
5. Tick、周期触发与时间轴动作；
6. 永久结束和回收。

这一区分决定前置 Buff 能否影响同一次伤害、被覆盖 Buff 是否仍计时，以及结束动作执行时
Buff 自身的 Modifier 是否还存在。

## 创建入口

技能动作通常从以下入口进入：

```text
CreateBuffAction.ExecuteInternal
  -> AbilitySystem.AddBuffByAbilityAction
  -> AbilitySystem._AddBuffInternal
       -> BattleDataLoader.TryGetBuff(buffId)
       -> AbilitySystem.AddBuffFinal / 对应 AbilitySystem 子类实现
  -> BuffStackingGroup.StackBuff
```

`AddBuffByAbilityAction` 负责把动作配置、黑板赋值、技能释放信息、部位转移信息和目标组上下文
整理为 `AddBuffOption`。`_AddBuffInternal` 再按 ID 取得同版本 `BuffData`。最终是否新建、刷新、
增强、覆盖或共存，由 `BuffStackingGroup.StackBuff` 按 stacking 配置决定。

`AddBuffFinal` 还会检查目标存活状态、stacking key 和 timed marker 等前置条件。各 stacking 类型
的完整分支目前尚未逐项命名，不能先验地把所有重复施加都解释成“刷新持续时间”。

## 分配与初始化

需要新实例时：

```text
BuffStackingGroup._AllocateBuff
  -> BuffPool.Allocate(buffId)
  -> Buff.Reset(buffData, owner, source, addBuffOption)
```

`Buff.Reset` 的主干职责包括：

- 绑定 owner、source、BuffData 与技能释放上下文；
- 创建并赋值运行时黑板；
- 初始化持续时间、周期计时器、层数和优先级相关状态；
- 加载属性 Modifier；
- 加载 BuffAction、AbilityAction 与 TimelineAction；
- 加载 Damage、Heal、Poise 和 Global Modifier；
- 加载护盾实例、附加标签和其他运行时对象。

这里的“加载”是构造运行时对象，不等于已经把 Modifier 注册到 owner、BattleManager 或事件系统。

## 首次启动与启用

`BuffStackingGroup.StackBuff` 会根据叠层分支设置 `Buff.isEnabled`。运行时 setter 在状态真正变化时
执行：

```text
set isEnabled = true
  -> 若从未启动：OnStart()
  -> OnEnable()

set isEnabled = false
  -> OnDisable()
  -> 写回禁用状态
```

### OnStart

`OnStart` 只在实例第一次启用时执行。已确认它会：

- 执行一组开始阶段 BuffAction；
- 初始化内部触发逻辑；
- 推进起点已经到达的时间轴节点；
- 以 owner 自身为目标启动 Buff 的 `TimelineActionProcessor`。

### OnEnable

`OnEnable` 可以在首次启动或优先级恢复后多次发生。其已确认顺序为：

```text
_AddModifier()
  -> 执行启用阶段的 BuffAction
  -> 执行另一组启用相关 BuffAction
  -> 记录 BuffStart
```

`_AddModifier` 会立即把不同效果注册到对应系统：

- `Attributes.AddModifier`；
- `AbilitySystem.AddDamageModifier`；
- `AddHealModifier`、`AddPoiseModifier`；
- 护盾容器；
- owner GameplayTag；
- `BattleManager.AddGlobalModifiers`。

两次 `_ExecuteBuffAction` 对应的具体 `Buff.Event` 枚举值仍需继续从调用点寄存器取值确认；当前
只确认它们发生在 Modifier 注册之后，不先猜测事件名称。

## 为什么前置 Buff 能影响随后伤害

同一个 `SequenceAction` 中，如果 `CreateBuffAction` 位于 `DamageAction` 之前，并且 stacking
分支使新 Buff 立即启用，则 `CreateBuffAction` 返回前已经完成 `_AddModifier`。随后
`DamageAction` 读取 Attributes 和 DamageModifier 容器时，可以看到该 Buff。

仍有两个例外必须逐配置判断：

- 新实例可能因 stacking 优先级处于禁用状态；
- Buff 可能只提供状态、动作或与当前伤害无关的 Modifier。

因此“动作在前”是必要的时序条件，不是“必然增伤”的充分条件。

## 暂时失活

`BuffStackingGroup.RefreshPriority` 可以切换已有 Buff 的 `isEnabled`，而不结束实例。
`OnDisable` 的顺序为：

```text
执行禁用阶段 BuffAction
  -> 结束 channeling action
  -> _RemoveModifier()
```

`_RemoveModifier` 对称地移除属性、伤害、治疗、失衡、护盾、标签和全局 Modifier。
不能把暂时失活当成 `MarkFinish`。`Buff.OnTick` 已确认禁用实例仍会推进 `passedTime`、减少
`lifeTime`，并推进自身的 `TimelineActionProcessor`；禁用状态跳过的是 `_TriggerInternal` 和正在
执行的 Buff `SequenceAction`。因此低优先级 Buff 即使暂时不提供 Modifier，也仍可能在等待期间
到期。

与 `isEnabled` 不同，`m_isPaused = true` 会把本 Tick 使用的 delta time 置为 0，从而暂停上述
时间推进。暂停和优先级失活必须作为两个正交状态建模。

## Tick 与到期

`Buff.OnTick` 先排除 finished 实例，再根据 BuffData 选择普通、全局缩放或自身缩放 delta time；
paused 时统一改为 0。随后执行顺序为：

```text
passedTime += selectedDeltaTime
更新 curFrame
若 enabled：推进内部 trigger 与正在执行的 Buff SequenceAction
若为有限时长：lifeTime -= selectedDeltaTime；小于阈值时 MarkFinish
推进 TimelineActionProcessor(passedTime, selectedDeltaTime)
```

SequenceAction 循环中每执行一项后都会再次检查 finished 和 enabled；某项动作若结束或禁用当前
Buff，本 Tick 不再继续推进后面的 SequenceAction。

## 永久结束

`Buff.MarkFinish` 已确认的关键顺序为：

```text
OnFinish(reason)
  -> 移除所有子 Buff
  -> 刷新 stacking group
  -> _RemoveModifier()
  -> _RemoveExtendTagModifier()
  -> 结束 channeling action
  -> 记录 BuffFinish
  -> 按条件上传并触发 owner/source 相关事件
```

`OnFinish` 本身会先执行结束阶段 BuffAction，再结束 Buff 的 TimelineActionProcessor。

因此结束阶段动作执行时，当前 Buff 的常规 Modifier 尚未由 `MarkFinish` 移除。若结束动作包含
伤害、治疗、创建子效果或读取属性，它可能仍观察到该 Buff 自身提供的修正。这个顺序与
`OnDisable` 不同：禁用分支执行禁用动作后立即移除 Modifier，但不会走完整 Finish 生命周期。

## StackingType 的运行时语义

`BuffStackingGroup.StackBuff` 在 `0x037C52CB` 读取 `m_stackingType`，并通过 12 项跳转表分派。
跳转表下标与 `BuffStackingSettings.StackingType` 的声明顺序完全对应，因此当前版本可将枚举值和
分支行为稳定映射如下：

| 值 | 类型 | 已有未结束实例时的行为 | 实例与启用状态 |
| ---: | --- | --- | --- |
| 0 | `Unlimited` | 始终分配并加入新 Buff | 多实例共存，新增实例立即启用 |
| 1 | `HighPriority` | 分配并加入新 Buff，再按优先队列重算 | 仅队首未结束实例启用，其余实例禁用但不结束 |
| 2 | `Stack` | 分配新 Buff；达到上限时先对 `_GetLastUnFinishedBuff()` 返回项执行 `MarkFinish` | 多实例叠加，新增实例立即启用 |
| 3 | `Enhance` | 对旧实例执行增强钩子，并在未达上限时增加组层数 | 保留单一实例，不分配新的有效实例 |
| 4 | `Refresh` | 使用新配置生成临时实例，只取其 duration 刷新旧实例 | 保留旧实例，不重新执行启用流程 |
| 5 | `Extend` | `old.lifeTime = old.lifeTime + new.duration` | 保留旧实例 |
| 6 | `Modify` | 从本次输入构造 Blackboard，并调用旧实例 `_Modify(inputData, inputBlackboard)` | 保留旧实例 |
| 7 | `Unique` | 已存在时拒绝本次施加 | 不替换、不刷新，返回空结果 |
| 8 | `EnhanceAndRefresh` | 先增强旧实例，再按 Refresh 规则更新持续时间 | 保留旧实例 |
| 9 | `OverwriteDuration` | `old.lifeTime = new.duration` | 保留旧实例 |
| 10 | `EnhanceAndOverwriteDuration` | 先增强旧实例，再覆盖持续时间 | 保留旧实例 |
| 11 | `HighPriorityWithMaxStack` | 分配并加入新 Buff，再按优先队列重算 | 最多启用队首 `maxStackCnt` 个未结束实例，其余禁用 |

这里的“临时实例”由 `_AllocateBuff` 构造，但不会加入组内优先队列，也不会设置
`isEnabled = true`。它用于复用 Buff 初始化过程计算本次施加的 duration 等数据，最终仍由旧实例
承载运行状态。

### 三种持续时间合并

运行时存在三种彼此独立的持续时间原语：

```text
Refresh:   old.lifeTime = max(old.lifeTime, new.duration)
Extend:    old.lifeTime = old.lifeTime + new.duration
Overwrite: old.lifeTime = new.duration
```

`Refresh` 的比较带浮点容差；特殊 duration/lifeTime 标记会从新实例传播到旧实例。这里比较的是
旧实例当前 `lifeTime` 与本次新实例的初始 `duration`，不是两份配置中的静态持续时间。

### Stack 与 Enhance 不是同一种“叠层”

- `Stack` 为每层保留独立 Buff 实例，每个实例各自拥有生命周期、Modifier 和结束时机；达到上限
  时，运行时会结束 `_GetLastUnFinishedBuff()` 返回的实例。优先队列的同优先级排列方向仍需
  单独证明，因此暂不把它表述为“最老”或“最新”实例。
- `Enhance` 只保留一个 Buff 实例，以 `m_curStackCnt` 表示增强层数。每次重复施加都会调用
  `_OnBeforeTryEnhanced` 和 `_OnAfterTryEnhanced`；只有未达到 `maxStackCnt` 时才增加层数、刷新
  stack effects 并执行 `_Enhance()`。
- `EnhanceAndRefresh` 与 `EnhanceAndOverwriteDuration` 即使增强层数已经达到上限，仍会执行对应
  的持续时间更新和 after-enhance 钩子。

### 优先级类型

`HighPriority` 和 `HighPriorityWithMaxStack` 都会把所有实例保留在组内优先队列中，然后通过
`Buff.isEnabled` 切换实际生效项。`isEnabled = false` 会执行 `OnDisable` 并移除 Modifier，但不会
结束 Buff；重新成为高优先级项时可再次 `OnEnable`。前者只启用第一个未结束实例，后者启用
队列前 `maxStackCnt` 个未结束实例。

`Buff._LoadPriority()` 按以下规则加载运行时 `m_priority`：

```text
usePriorityKey = false: m_priority = settings.priority
usePriorityKey = true:  m_priority = blackboard.GetFloat(priorityKey)
                        * (negatePriority ? -1 : 1)
```

`Buff.CompareTo(other)` 再按以下顺序比较，浮点字段均带 epsilon 容差：

1. `m_priority` 更高者排在前面；
2. 优先级相同时，当前 `m_lifeTime` 更长者排在前面；
3. 两者仍相同时，`instanceUid` 更小者排在前面。

因此排序具有稳定兜底键，不依赖容器枚举的偶然顺序。`Buff.RefreshPriority()` 会重新调用
`_LoadPriority()`；值确实变化时，它根据 stacking key 找到所属组并调用组的 `RefreshPriority`，
从而重新安排哪些实例处于 enabled 状态。

## BuffData 样本交叉验证

以下样本来自 AKEDB 1.4.4 BuffData CDN。它们用于验证配置字段与运行时分支可以闭环，不把
AKEDB 前端的人类可读标签当成运行时证据。

| BuffData | 配置 | 按运行时分支得到的含义 |
| --- | --- | --- |
| `buff_chr_0004_pelica_normal_debuff` | `Refresh`，8 秒，防御 `BaseMultiplier -0.5` | 首次创建减防实例；重复施加保留原实例并把剩余时间至少刷新至 8 秒 |
| `buff_chr_0004_pelica_potential_3_atkup` | `EnhanceAndRefresh`，上限 2，攻击与时长来自 Blackboard | 同一实例最多增强至 2 层；每次施加仍按动态 duration 刷新剩余时间 |
| `buff_chr_0027_tangtang_water_iceballstack` | `Stack`，上限来自 `water_stack`，60 秒 | 每层是独立实例；达到动态上限后结束组内一个旧实例，再加入新实例 |
| `buff_tower_energy_inflict_or_status_stack_1` | `Enhance`，动态上限 `max_stack` | 同一实例增加 enhance 层数，并用 `OnBuffEnhanceChanged` 事件检查层数和触发后续动作 |
| `buff_chr_0004_pelica_talent_0` | `Unique`，无限时长 | 重复添加被拒绝，不替换常驻天赋实例 |

配置 ID 和命名不能替代 `stackingSettings.stackingType`。例如
`buff_common_high_ai_priority` 名称包含 `high_ai_priority`，实际 stacking 类型却是
`Unlimited`；若生成器用 ID 关键字推断行为，会得到错误结果。

## Modify：保留实例并替换运行参数

`Modify` 分支不会分配新的有效 Buff，也不会替换旧实例的 `BuffData`。它先基于本次 BuffData
和 AddBuffOption 构造输入 Blackboard，再调用旧实例 `_Modify(inputData, inputBlackboard)`：

```text
oldBuff.blackboard.Assign(inputBlackboard)
oldBuff.OnBlackboardValueChange()
  -> _ModifyAttributesModifier(
       oldBuff.buffData.attributeModifier,
       oldBuff.blackboard,
       oldBuff.enhanceCnt)
  -> owner.attributes.MarkAttributesDirty(affectedMask)
```

当前未打 Patch 的 `_Modify` 实现并不直接读取其 `inputData` 参数；重算时使用的是旧 BuffData
中的 `attributeModifier` 配置。输入 Blackboard 合并后，旧实例的 `passedTime`、trigger timer、
TimelineActionProcessor、已启动状态和生命周期均保持不变。

这不意味着 Modify 只对属性 Buff 有意义。完整配置样本中：

- `buff_harddung_healhp_hdg016` 是无限时长周期治疗 Buff，`OnBuffTrigger` 的治疗倍率读取
  Blackboard 键 `heal`；
- `buff_liquid_acid_damage` 是无限时长周期伤害 Buff，伤害读取 Blackboard 键 `dmg`。

两者的 `attributeModifier` 都为空。重复施加后，不需要重建周期动作；后续动作在原实例上继续
执行并读取更新后的 Blackboard。属性 Modifier 之所以需要额外重算，是因为它在启用时已经由
loader 物化并注册，不能只等待下一次动作读取。

因此模拟器应把 Modify 表达为“保留运行状态并合并参数”，不能实现成 Finish 旧 Buff 后新建。

## Buff 事件与重入

`Buff.Event` 的声明顺序和 `_ExecuteBuffAction` 的分支共同确认了以下事件：

| 值 | 事件 | 主要入口 |
| ---: | --- | --- |
| 0 | `OnBuffStart` | 实例首次启用时的 `OnStart` |
| 1 | `OnBuffTrigger` | 周期 trigger |
| 2 | `OnBuffFinish` | `OnFinish` |
| 3 | `OnBuffEnable` | 每次 `OnEnable`，在 Modifier 注册之后 |
| 4 | `OnBuffDisable` | `OnDisable`，在 Modifier 移除之前 |
| 5 | `DuringBuffEnable` | 每次 `OnEnable`，紧随 `OnBuffEnable` |
| 6 | `OnBuffEnhanceChanged` | enhance 层数变化 |
| 7 | `OnBuffAfterTryEnhanced` | 完成一次增强尝试后 |
| 8 | `OnBuffBeforeTryEnhanced` | 开始一次增强尝试前 |
| 9 | `OnBuffDispelled` | `BuffContainer.DispelBuff` 筛选命中后、进入统一 Finish 流程前 |
| 10 | `OnBuffFinishedEarlyInterrupted` | `FinishReason` 为 `Ignite` 或 `Early` 时，由 `OnFinish` 追加执行 |

`OnEnable` 的精确顺序为：

```text
_AddModifier()
_ExecuteBuffAction(OnBuffEnable)
_ExecuteBuffAction(DuringBuffEnable)
更新图标/记录 BuffStart
```

`DuringBuffEnable` 是特殊的持续执行事件：它调用 `SequenceAction.Execute`，对应动作会由
`_EndChannelingAction` 在 Disable/Finish 时结束。其他普通 Buff 事件调用
`SequenceAction.ExecuteInstant`。

### 同步重入边界

`_ExecuteBuffAction` 取得事件对应的 SequenceAction 列表及其初始数量，然后按索引同步顺序
执行。它没有在每项之间重新检查当前 Buff 的 finished/enabled，也没有复制动作列表。由动作
创建、增强或结束 Buff 时，相应生命周期会在当前调用栈中立即发生；当前事件剩余动作仍按原先
取得的数量继续执行。

`MarkFinish` 提供的是结束流程专用重入保护：

```text
若 !finishable、isFinished 或 isFinishing：直接返回
isFinishing = true
OnFinish(reason)
isEnabled = false
isFinished = true
继续移除子 Buff、stacking、Modifier 并广播事件
```

因此 Finish 动作中再次结束同一实例不会重复进入 `OnFinish`。目前未观察到覆盖所有事件类型的
通用递归深度保护；配置若在事件中同步创建另一个 Buff，其 Start/Enable 事件可以嵌套执行。

### 驱散筛选与结束顺序

`AbilitySystem.DispelBuff` 只是把请求转发给自身的 `BuffContainer`。容器遍历已有 Buff，并按以下顺序
筛选候选项：

1. 跳过已经 `isFinished` 的实例；
2. 要求 `buffData.dispelConfig.canBeDispelled == true`；
3. 要求传入的 `dispelLevel >= buffData.dispelConfig.dispelledLevel`；
4. 若传入了 `GameplayTagQuery`，则要求该查询匹配 `buffData.applyTags`。

最后一项检查的是 Buff 配置声明的应用标签，并非持有者 AbilitySystem 当前聚合出的全部标签。每个
通过筛选的实例立即按以下顺序处理：

```text
buff.OnDispelled(dispelSource)
  -> _ExecuteBuffAction(OnBuffDispelled, dispelSource)
buff.MarkFinish(
  upload = true,
  reason = Dispelled,
  finishSource = dispelSource)
  -> OnFinish(Dispelled)
  -> Disable / finished / stacking / modifier 清理
```

所以驱散专用动作先于普通 Finish 动作和 Modifier 移除执行；它仍能读取当前 Buff 的运行状态与已注册
Modifier。`FinishReason.Dispelled` 不属于 `Ignite` 或 `Early`，因此不会追加
`OnBuffFinishedEarlyInterrupted`。

容器直接遍历现有集合并逐项处理，没有先收集一份“待驱散列表”。`MarkFinish` 在此处不会立刻从正在
遍历的底层集合中物理删除实例，而是标记结束并走统一清理，因此遍历可以继续。未打 Patch 的正常路径
在遍历完成后固定返回 `true`；该返回值表示调用流程完成，不能解释为“至少驱散了一个 Buff”。

## 尚未闭环

1. 三种 delta time 选择字段在 BuffData 中的准确名称与配置分布；
2. 子 Buff、GlobalBuff、KeywordBuff 与普通 Buff 的继承和结束顺序；
3. AbilitySystem 事件广播与 Buff 自身事件嵌套时的完整先后顺序；
4. 动作列表在运行时是否存在会改变长度的 Patch 或动态配置路径；
5. 驱散请求携带的 `ServerActionParams`、`SkillCastInfo` 在联网同步和回放路径中的来源。

下一步应选择两种样本交叉验证：一个简单即时属性 Buff，以及一个可叠层、可刷新并带周期动作
的 Buff。先把每个 stacking 分支映射到行为，再批量分类全量 BuffData。

## 运行时证据

客户端版本为 1.4.4。主要方法：

| 方法 | RVA |
| --- | ---: |
| `AbilitySystem.AddBuffByAbilityAction` | `0x035F39D0` |
| `AbilitySystem._AddBuffInternal` | `0x037C2DC0` |
| `AbilitySystem.AddBuffFinal` | `0x037C22C0` |
| `BuffStackingGroup.StackBuff` | `0x037C5260` |
| `BuffStackingGroup._AllocateBuff` | `0x037C5930` |
| `Buff.Reset` | `0x037C66E0` |
| `Buff.isEnabled.set` | `0x037C58A0` |
| `Buff.OnStart` | `0x037C5B40` |
| `Buff.OnEnable` | `0x02F4C850` |
| `Buff.OnDisable` | `0x03E98770` |
| `Buff.MarkFinish` | `0x02F4B1D0` |
| `AbilitySystem.DispelBuff` | `0x06CAD9B0` |
| `BuffContainer.DispelBuff` | `0x06D4734C` |
| `Buff.OnDispelled` | `0x06D41D70` |

可重建报告位于忽略目录：

- `runtime-snapshot-buff-lifecycle.json`；
- `runtime-snapshot-buff-activation.json`；
- `runtime-snapshot-buff-enabled.json`；
- `runtime-snapshot-buff-state-hooks.json`；
- `runtime-snapshot-buff-duration-stacking.json`；
- `runtime-snapshot-buff-stack.json`；
- `runtime-snapshot-buff-priority.json`；
- `runtime-snapshot-buff-tick.json`；
- `runtime-snapshot-buff-modify.json`；
- `runtime-snapshot-buff-blackboard-change.json`；
- `runtime-snapshot-buff-events.json`；
- `runtime-snapshot-buff-dispel.json`。
