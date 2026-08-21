# 单次命中的动作、Buff 与伤害执行顺序

## 研究问题

技能的一次命中可能在同一个 `SequenceActionData.actionData` 中同时配置附着、伤害、
Buff、受击动画等动作。需要确认这些动作是否存在固定的类型优先级，以及同一次命中
创建的 Buff 能否影响该命中的伤害。

本文基于终末地 1.4.4 客户端的配置样本、IL2CPP 类型索引、完整运行时模块快照和
运行时方法探针。用于解释枚举常量的 metadata 来自另一份终末地客户端文件；它与
运行时立即数通过 `OnAfterSkillApplyCost = 0x8f` 交叉验证，但尚未证明来自同一构建，
因此本文只使用已经与运行时常量互相吻合的枚举项，不用它推导方法地址或字段布局。

## 结论

客户端不存在“Buff 永远先于伤害”或“伤害永远先于 Buff”的统一类型顺序。
同一个 `SequenceAction` 中的动作按照配置数组的先后顺序执行：

1. `SequenceAction.Init` 从 `actionData[0]` 开始向后遍历；
2. 启用的动作按原顺序创建并追加到 `m_actions`，过程中没有按类型或优先级排序；
3. `SequenceAction.Execute` 从 `m_actions[0]` 开始同步调用 `AbilityAction.Execute`；
4. 当前动作成功完成后执行 `index++`，继续下一个动作；
5. 动作未完成或失败时可能暂停或终止当前序列，而不是跳过后继续执行。

因此，对于同一动作序列中的显式 `CreateBuffAction` 和 `DamageAction`：

- `CreateBuffAction` 在前：立即生效的属性修正或伤害修正可以参与后续
  `DamageAction`，因而可能增益本次伤害；
- `CreateBuffAction` 在后：伤害已经完成计算和应用，该 Buff 只能影响后续伤害；
- 是否真的改变伤害仍取决于 Buff 的目标、修正类型、生效条件和处理时机。

这里的“可能”不是执行顺序不确定，而是有些 Buff 只记录状态、触发后续事件，或者
修正的属性与当前伤害无关。

## 伤害计算为何会受到前置 Buff 影响

`DamageAction._ProcessDamage` 的主干已经可以细化为：

1. 创建并填充 `DamagePackData`；
2. 在相关 AbilitySystem 上同步触发 `OnBeforeCalculateDamage`（`0x12d`）；
3. 依次执行命中配置中的 `DamageProcessorBase.ProcessDamagePackData(BeforeCalculation)`；
4. 调用 `DamagePackData.ApplyDamageModifer(BeforeCalculation)`，遍历攻击者、目标和全局
   来源汇总出的伤害修正器；
5. `_CalculateDamageResultByType` 按普通实体、塔、独立生命等目标类型建立计算结果；
6. 依次执行 `DamageProcessorBase.ProcessDamagePackData(AfterCalculation)`；
7. `BattleFormula.CalculateDamage` 首先调用 `DamagePackData.GetFinalAttackValue()`；后者会
   执行 `ApplyDamageModifer(AfterCalculation)`，再组合最终伤害倍率；
8. `BattleFormula.CalculateDamage` 继续处理防御、伤害类型抗性、暴击、格挡和战斗报告，
   将当前伤害包转换为最终伤害值；
9. 创建 `Modifier.NewDamage`；
10. 调用 `Modifier.Apply`，或在部位/伤害转移分支调用 `ApplyTransferredModifier`。

这里的 `AfterCalculation` 位于 `_CalculateDamageResultByType` 之后、
`BattleFormula.CalculateDamage` 之前。它所指的“Calculation”边界是前者，不应仅凭枚举名
把它错误地移动到最终公式调用之后。前后两个阶段都会执行命中配置中的 damage processor
与已注册的 damage modifier；区别是 `BeforeCalculation` 的 modifier 调用直接位于
`_ProcessDamage`，而 `AfterCalculation` 的 modifier 调用封装在 `GetFinalAttackValue` 内。

### 最终伤害如何落到生命值

普通的 `Modifier.Apply` 先进入目标的 `AbilitySystem.ApplyModifier`：

1. 目标触发 `OnBeforeApplyModifier`（10）；
2. `_DoApplyModifier` 按伤害、治疗、失衡、终结技能量等 Modifier 类型分派；
3. 分支结束后目标触发 `OnAfterApplyModifier`（11）。

普通生命伤害分支中已经确认的事件边界为：

1. 目标侧 `OnBeforeTakeDamage`（101）；
2. 来源侧 `OnBeforeOutputDamage`（302）；
3. 检查伤害免疫，并由 `ShieldController.AbsorbDamage` 结算护盾吸收；
4. 对可能致死的结果触发来源侧 `OnBeforeKillEntity`（221），实际更新生命与死亡状态后
   触发 `OnAfterKillEntity`（222）；
5. `SetHpInternal` 在生命变化路径触发 `OnHpChange`（303），致死保护分支还会先触发
   `OnTriggerUndeadFeature`（27）；
6. `_OnAttacked` 依次发出目标侧 `OnTakeDamage`（12）及可选
   `OnTakeCriticalDamage`（43），随后发出来源侧 `OnOutputDamage`（13）、命中环境或暴击的
   对应事件，最后处理队伍受伤事件。

因此“伤害事件”不是一次统一广播。`OnBeforeCalculateDamage` 可以改动计算输入；
`OnBeforeTakeDamage` 和 `OnBeforeOutputDamage` 位于免疫、护盾与落血之前；`OnTakeDamage`
和 `OnOutputDamage` 则位于生命结算之后。判断某个事件 Buff 能否增益当前 hit 时，必须看它
监听的是哪一个枚举，而不能只看名称中含有 `Damage`。

`ApplyTransferredModifier` 有一条独立实现，但保持了相同的关键边界：它同样在免疫检查前
触发 `OnBeforeTakeDamage`，并在转移伤害结算后触发 `OnTakeDamage`、暴击受击和击杀事件。
部位转移、独立生命和环境伤害仍有专用分支，不能直接套用普通生命伤害的所有事件。

Buff 初始化会加载属性修正、伤害修正和全局修正；生效阶段会把它们分别注册到
`Attributes`、`AbilitySystem.damageModifiers` 和 `BattleManager`。当后续伤害包遍历
`damageModifiers` 并读取相关属性时，前置 Buff 已经位于对应容器中。

`DamageAction._TakeDamageCalculationSnapshot` 同时维护攻击者身份和基础计算结果缓存：

- `m_cachedAttacker` 保存 `battleInstId` 与 `isMainChar`；
- `m_calcResultCache` 保存启用了 `DamageUnit.takeAtkSnapshot` 的基础 `CalcResult.value`；
- 简单计算在动作重置时缓存 `attacker.atk * atkScale`；
- 多态计算在动作重置时调用 `atkCalculation.Evaluate(...)` 并缓存结果；
- 未启用 `takeAtkSnapshot` 的单元仍在命中时读取当前 `DamagePackData` 属性副本。

因此快照只冻结该 DamageUnit 的基础计算值，不等于冻结完整最终伤害。后续
`AfterCalculation` modifier、伤害倍率区间、暴击、防御和抗性仍在命中流水线中结算。
是否采用基础值快照必须读取每个 `DamageUnit.takeAtkSnapshot`，不能对全部技能统一假设。

`DamagePackData` 构造时会导出攻击者和目标的属性数组。执行某一阶段的 damage modifier
后，`ApplyDamageModifer` 会统一调用 `_DumpAttributes()` 再次导出属性；因此
`InstantModifyAttribute` 临时挂入实体属性系统后，BeforeCalculation 阶段的非快照计算能
读取更新后的属性副本。即时修正会在本次伤害结算后由
`ClearInstantAttributeModifier()` 移除。

## 不应混为一谈的三种机制

### 同级显式动作

`CreateBuffAction` 与 `DamageAction` 都直接位于同一个 `actionData` 数组中。此时以数组
顺序为准，是本文已经确认的核心结论。

### 伤害修正器内的动作

已经存在的 Buff 可以提供 `DamageModifier`。`DamageModifier.ApplyModifier` 不仅能够修改
`DamagePackData`，还可以在指定 `ProcessTiming` 下执行自己的 `SequenceAction`。这属于
伤害流水线内部的钩子，不能只看技能命中数组中 Buff 和伤害的相对位置。

这种动作是否影响当前伤害，要结合其处理阶段和对 `DamagePackData` 的实际修改判断。

### 附着与状态

`SpellInfliction` 是独立的 `AbilityAction`，并不等同于一般的 `CreateBuffAction`。如果它
排在伤害前，附着相关状态和事件会先处理；其是否改变紧随其后的伤害，仍由对应附着、
状态 Buff 和伤害修正器配置决定。

## 配置样本

佩丽卡普通战技样本：

`data/research-artifacts/combat-1.4.4/samples/skill/pelica/pelica-normal.decoded.json`

其中一个实际命中序列为：

1. `FindTargetAction`；
2. `InterruptAction`；
3. `SpellInfliction`；
4. `DamageAction`；
5. `EnemyHurtAnimAction`；
6. `CameraImpulseAction`；
7. `CreateBuffAction`。

运行时会保持这一顺序。因此该序列中的附着发生在伤害前，而末尾创建的 Buff 不会
反向修改已经应用的这次伤害。

单个样本不能说明所有技能都采用这一排列；它证明配置确实会把不同动作交错放置，而
运行时的通用序列执行器负责忠实执行配置顺序。

## 运行时证据

| 方法 | Token / RVA | 关键证据 |
| --- | --- | --- |
| `SequenceAction.Init` | `0x0600D7DE` / `0x033BB810` | 索引从 0 递增，按输入数组创建并追加动作 |
| `SequenceAction.Execute` | `0x0600D7D7` / `0x033BD9C0` | 调用 `m_actions[index].Execute` 后执行 `index++` |
| `AbilityAction.Execute` | `0x0600D7AC` / `0x033C05E0` | 同步进入具体动作实现 |
| `CreateBuffAction.ExecuteInternal` | `0x0600DA91` / `0x035F1D60` | 调用 `AddBuffByAbilityAction` 创建 Buff |
| `Buff._AddModifier` | `0x0600E438` / `0x03E98C40` | 注册属性、伤害、失衡、护盾和全局修正 |
| `DamageAction._ProcessDamage` | `0x0600DAE5` / `0x0353F4C0` | 建立伤害包、运行修正器、计算并应用伤害 |
| `DamagePackData.ApplyDamageModifer` | `0x0600E4FC` / `0x03A8FEE0` | 遍历当前 `damageModifiers`，阶段结束后重新导出属性数组 |
| `BattleFormula.CalculateDamage` | `0x03B53700` | 从处理后的伤害包生成最终伤害、暴击和格挡结果 |
| `AbilitySystem.ApplyModifier` | `0x03938430` | 在实际类型分派外触发 Modifier 前后事件 |
| `AbilitySystem._DoApplyModifier` | `0x0393A3D0` | 普通伤害的受击前、输出前、护盾、生命与击杀主干 |
| `AbilitySystem.SetHpInternal` | `0x03A903A0` | 更新生命并触发生命变化与不死保护事件 |
| `AbilitySystem._OnAttacked` | `0x03DCCAC0` | 结算后的受击、输出、暴击与队伍事件 |
| `AbilitySystem.ApplyTransferredModifier` | `0x06CAA340` | 部位/伤害转移的独立 Modifier 结算路径 |
| `DamageAction._TakeDamageCalculationSnapshot` | `0x0600DAEB` / `0x035F0F90` | 缓存攻击者身份，并为启用 `takeAtkSnapshot` 的单元缓存基础计算值 |

可复现分析输入位于忽略目录：

- `data/research-artifacts/combat-1.4.4/IL2CPP_MethodProbes.hit-order.json`；
- `data/research-artifacts/combat-1.4.4/derived/runtime-method-analysis.hit-order.json`；
- `data/research-artifacts/combat-1.4.4/derived/runtime-snapshot-damage-pipeline.json`；
- `data/research-artifacts/combat-1.4.4/derived/runtime-snapshot-damage-formula.json`；
- `data/research-artifacts/combat-1.4.4/derived/runtime-snapshot-final-damage-scale.json`；
- `data/research-artifacts/combat-1.4.4/derived/runtime-snapshot-modifier-hp-events.json`；
- `data/research-artifacts/combat-1.4.4/derived/runtime-snapshot-damage-post-events.json`；
- `data/research-artifacts/combat-1.4.4/derived/ability-system-events.json`。

## 当前边界

已经确认通用动作序列、两阶段伤害处理和普通生命伤害的主要事件边界，但还不能只凭通用代码判断每个具体
Buff 是否影响当前命中。逐技能验证仍需读取以下配置：

- `actionData` 中动作的实际顺序；
- `CreateBuffAction` 的目标和 Buff ID；
- Buff 的属性修正、`DamageModifier` 与 `ProcessTiming`；
- 条件动作、IFix 热补丁和服务端结果是否改变该分支。

同一伤害的嵌套遍历顺序已经闭环：`DamageAction.ExecuteInternal`（RVA `0x03B85B30`）按目标列表
逐项调用 `_ProcessDamage`，后者（RVA `0x0353F4C0`）再按 DamageUnit 索引递增执行，因此是“目标
外层、DamageUnit 内层”。普通目标完成后才处理 `m_extraTargets`。

尚未闭环的通用边界包括：额外目标的产生与转移语义、独立生命/塔/环境伤害的完整事件差异、
护盾归零与生命归零发生在同一 hit 时的嵌套顺序，以及 IFix 是否替换上述任一原生分支。

后续可以据此生成“同命中增益检查”：扫描每个命中序列，找出位于伤害前后的 Buff、
附着和条件动作，再追踪对应 Buff 对当前伤害包的实际影响。
