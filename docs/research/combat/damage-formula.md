# 伤害公式的已确认分层

本文记录从 1.4.4 运行时快照中恢复的客户端伤害公式。文中将原生代码事实、运行时配置和仍待验证的推断分开描述，不使用社区经验公式填补空白。

## 总体入口

`BattleFormula.CalculateDamage(DamagePackData&, ...)` 接收已经完成前置处理的伤害包。普通伤害首先通过 `DamagePackData.GetFinalAttackValue()` 取得攻击值：

```text
if damageType == LifeDrain:
  return calcResult.value

ApplyDamageModifier(AfterCalculation)
return calcResult.value * GetFinalDamageScale()
```

`GetFinalDamageScale()` 从 1 开始，遍历全局定义的伤害倍率区间，并把每个区间的结果相乘。不同区间之间是乘算关系；同一区间内部如何合并，要由对应区间的求值函数决定。

### `CalcResult.value` 的上游来源

`DamageAction._CalculateDamageResultForNormalEntity(...)` 会按
`DamageUnit.simpleCalculation` 选择两条基础值生产路径：

```text
if simpleCalculation:
  scale = DamageUnit.atkScale.GetValue(actionBlackboard)
  attack = attackerOverrideAttributes[2] ?? attacker.atk
  CalcResult.value = attack * scale
else:
  CalcResult = DamageUnit.atkCalculation.Evaluate(
    attacker,
    defender,
    actionBlackboard,
    actionEnvironment.context,
    attackerOverrideAttributes,
    defenderOverrideAttributes,
    argsForServer)
```

字段位置与运行时读取一致：`DamageUnit.simpleCalculation` 位于 `0x44`，
`DamageUnit.atkScale` 位于 `0x48`，`DamageUnit.atkCalculation` 位于 `0x50`，
`DamageUnit.takeAtkSnapshot` 位于 `0x58`。
覆盖数组的索引 `2` 对应 `AttributeType.Atk = 2`；数组不存在时调用
`AbilitySystem.atk.get`。`CalcResult` 本身只有一个 `double value` 字段，计算是否成功由独立的
`calcSucceed` 引用参数传递，不应在复刻结构中给 `CalcResult` 虚构状态字段。

`AtkScaleCalculation.Evaluate(...)` 已由完整运行时快照离线恢复。它与简单路径的数值主干同形：

```text
attack = attackerOverrideAttributes[2] ?? attacker.atk
scale = AtkScaleCalculation.atkScale.GetValue(actionBlackboard)
return new CalcResult(attack * scale)
```

但两条路径读取的是不同配置字段，不能合并数据语义。佩丽卡普通攻击样本的伤害单元为
`simpleCalculation = false`，使用嵌套 `AtkScaleCalculation.atkScale`，其
`BlackboardDouble` 指向黑板键 `atk_scale`；同一 hit 的失衡单元则为
`simpleCalculation = true`，直接使用 `DamageUnit.atkScale`。

`CalculationBase.GetAttribute(...)` 的通用规则也已确认：覆盖数组非空时直接按
`AttributeType` 数值索引读取，否则读取 `AbilitySystem.attributes` 中对应属性。

攻击/防御覆盖数组来自 `DamagePackData`：构造时先从双方 `Attributes` 导出完整数组；每次
`ApplyDamageModifer(timing)` 处理完双方 modifier 后又调用 `_DumpAttributes()` 刷新数组。
`InstantModifyAttribute` 会临时把属性 modifier 挂到指定一侧，因而 BeforeCalculation
刷新后的数组能参与随后计算，结算后再由 `ClearInstantAttributeModifier()` 清除。

`takeAtkSnapshot=true` 是另一层机制：`DamageAction.OnReset` 调用
`_TakeDamageCalculationSnapshot()`，在动作重置时计算并缓存该单元的基础
`CalcResult.value`；命中时直接取缓存，不再读取本次 `DamagePackData` 的覆盖数组。
`takeAtkSnapshot=false` 才在命中时走上面的简单或多态计算路径。该缓存不冻结最终伤害倍率、
防御、抗性与暴击。当前仍待确认混合快照/非快照 DamageUnit 时
`m_calcResultCache` 的下标不变量。

其余子类的主干公式已继续从同一运行时快照恢复：

| 类型 | 字段 | 返回值 |
| --- | --- | --- |
| `DefiniteValueCalculation` | `value`、`applyScale`、`valueScale` | `value`；启用时乘 `valueScale` |
| `MultiplyAttributeCalculation` | `valueSource`、`attributeType`、`multiplier`、`addition` | 来源属性乘 `multiplier` 后加 `addition` |
| `BreakingAttackCalculation` | `multiplier`、`atkScale` | 攻击者 `Atk`、目标 `BreakingAttackDamageTakenScalar` 与两个倍率之积 |

`ValueSource = 0` 使用攻击者及攻击者覆盖数组，非 0 分支使用目标及目标覆盖数组；已登记枚举的
两个值分别为 `AttackerOrHealer` 与 `Target`。`DefiniteValueCalculation.value` 调用
`GetDoubleValue`，而 `valueScale`、`multiplier`、`addition` 和破韧攻击的两个倍率均调用返回
float 的 `GetValue`。破韧攻击的实际舍入顺序为：

```text
scaledAttack = (float)(attackerAtk * defenderBreakingAttackDamageTakenScalar)
result = (float)atkScale * (float)multiplier * scaledAttack
CalcResult.value = (double)result
```

`PrimaryAttrCalculation` 与 `NormalAttackPoiseCalculation` 的函数入口已经取得，但前者还依赖
主/副属性到 `AttributeType` 的映射，后者乘用的运行时静态量在当前快照位置为零；在其来源闭环
前不写入可执行规格。

### 伤害倍率区间

`DamageScaleProcessorConfig.Zone` 已恢复出以下字段：

| 字段 | 含义 |
| --- | --- |
| `name` | 区间的稳定名称，数据配置中的 `zoneName` 用它寻址 |
| `alias` | 区间别名 |
| `isMultiplyZone` | 该区间是否采用乘算语义 |
| `mergeAttackerAndDefender` | 非乘算区间是否把攻防双方作为同一加算区合并 |
| `isDamageTypeZone` | 是否属于伤害类型专用区间 |
| `serverIndex` | 服务端使用的区间编号 |

伤害包构造时会按区间数量分别分配攻击方和防御方的区间数组，并把每个元素初始化为 `1.0`。随后 processor 修改对应数组元素。`_GetDamageScale(targetZone)` 先找到当前区间下标，再注入适用于该 hit 的角色属性加成，最后按以下规则合并：

`DamageScaleProcessor` 的数据字段统一名为 `addition`，但实际写入取决于目标区间：

```text
if targetZone.isMultiplyZone:
  sideZones[zoneIndex] *= 1 + addition
else:
  sideZones[zoneIndex] += addition
```

`side` 决定修改攻击方还是防御方数组。因此 `addition = -0.95` 在普通区是从当前值减去 `0.95`，在乘算区则是把当前值乘以 `0.05`。多个 processor 作用于乘算区时会逐次相乘，而不是先把所有 `addition` 相加。

```text
attacker = attackerDamageScaleZones[zoneIndex]
defender = defenderDamageScaleZones[zoneIndex]

if !targetZone.isMultiplyZone && targetZone.mergeAttackerAndDefender:
  zoneResult = attacker + defender - 1
else:
  zoneResult = attacker * defender

if zoneResult < 0 or isNaN(zoneResult):
  zoneResult = 0
```

`- 1` 表明合并型加算区两侧的基线都是 1。例如攻击方区间值为 `1.3`、防御方为 `0.8` 时，合并结果为 `1.1`；这等价于把双方相对基线的 `+0.3` 与 `-0.2` 相加。乘算区或不合并攻防双方的区间则得到 `1.3 * 0.8 = 1.04`。

当前客户端配置资源位于：

```text
assets/beyond/dynamicassets/gamedata/gameplayconfig/damagescaleprocessorconfig.asset
```

其 `allZones` 按最终计算顺序定义了 7 个区间：

| 顺序 | 名称 | 游戏内别名 | 乘算区 | 合并攻防 | 伤害类型区 | 服务端编号 |
| ---: | --- | --- | :---: | :---: | :---: | ---: |
| 0 | `ProdCalcZone` | 独立增减伤 | 是 | 否 | 否 | 0 |
| 1 | `NormalCalcZone` | 通用增减伤 | 否 | 否 | 否 | 1 |
| 2 | `AbnormalAndBurstIncrease` | 法术爆发与异常增伤 | 否 | 否 | 否 | 4 |
| 3 | `EnhancedDmgIncreace` | 增幅增伤 | 否 | 否 | 否 | 2 |
| 4 | `ComboCalcZone` | 连击增伤 | 否 | 否 | 否 | 5 |
| 5 | `VulnerableDmgIncreace` | 脆弱增伤 | 否 | 否 | 否 | 3 |
| 6 | `RaceCalcZone` | 竞速增减伤 | 否 | 否 | 否 | 0 |

`Increace` 是原始资源中的拼写，不应在读取配置时擅自修正。当前七区的 `mergeAttackerAndDefender` 均为否，所以实际规则是：

1. 同一侧的 `ProdCalcZone` processor 逐项乘算；
2. 同一侧其他区间的 processor 逐项加算；
3. 每个区间的攻击方值与防御方值相乘；
4. 七个区间的结果再次相乘。

因此“同类加成相加”只描述同一侧、同一非乘算区间内的聚合，不代表攻击方增伤与防御方减伤也放进同一个加法括号。`_GetFinalDamageScale()` 的行为完全由区间配置决定，而不是依赖中文名称的约定。

当前已定位的专用注入如下。它们会先加到匹配区间的一侧数组元素，再执行上面的攻防合并；后续实现不能把这些属性在总公式末尾重复乘一次。

| 区间用途 | 根据 hit 选择的属性 |
| --- | --- |
| `NormalCalcZone` | 伤害类型增伤：`Physical/Fire/Pulse/Cryst/Natural/EtherDamageIncrease`；`Real` 与 `LifeDrain` 不取这组属性 |
| `NormalCalcZone` | 技能类型增伤：`NormalAttackDamageIncrease`、`NormalSkillDamageIncrease`、`ComboSkillDamageIncrease`、`UltimateSkillDamageIncrease` |
| `NormalCalcZone` | 目标处于对应失衡状态时读取 `DamageToBrokenUnitIncrease` |
| `AbnormalAndBurstIncrease` | 按 `DamageDecorateMask` 累加 `Fire/Pulse/Cryst/NaturalBurstDamageIncrease` 与 `Fire/Pulse/Cryst/NaturalAbnormalDamageIncrease` 中匹配的项目 |
| `EnhancedDmgIncreace` | 按伤害类型读取 `Physical/Fire/Pulse/Cryst/Natural/EtherEnhancedDmgIncrease` |
| `VulnerableDmgIncreace` | 从防御方按伤害类型读取 `Physical/Fire/Pulse/Cryst/Natural/EtherVulnerableDmgIncrease` |

### 伤害装饰位映射

`DamageDecorateMask` 是底层类型为 `Int64` 的位枚举。1.4.4 metadata 中的单项位和组合值已经精确恢复；公式当前使用的技能类型映射如下：

| 命中包含的装饰位 | 位值 | 注入属性 |
| --- | ---: | --- |
| `PowerAttack`、`NormalAttack`、`PlungingAttack` 或 `DashAttack` | `0x4 / 0x80 / 0x400 / 0x20000` | `NormalAttackDamageIncrease` |
| `NormalSkill` | `0x100` | `NormalSkillDamageIncrease` |
| `ComboSkill` | `0x2000` | `ComboSkillDamageIncrease` |
| `UltimateSkill` | `0x200` | `UltimateSkillDamageIncrease` |

第一行在原生代码中以组合掩码 `NormalAttackDamageSet = 0x20484` 一次检查。因此某个 hit 即使没有 `NormalAttack` 位，只要带有重击、下落攻击或冲刺攻击位，仍会读取普通攻击伤害加成。

`AbnormalAndBurstIncrease` 区间的完整映射为：

| 命中包含的装饰位 | 位值 | 注入属性 |
| --- | ---: | --- |
| `FireBurst` | `0x400000` | `FireBurstDamageIncrease` |
| `PulseBurst` | `0x1000000` | `PulseBurstDamageIncrease` |
| `CrystBurst` | `0x800000` | `CrystBurstDamageIncrease` |
| `NaturalBurst` | `0x2000000` | `NaturalBurstDamageIncrease` |
| `FireAbnormalInitial` 或 `Burning` | `0x8 / 0x4000000` | `FireAbnormalDamageIncrease` |
| `PulseAbnormalInitial` | `0x10` | `PulseAbnormalDamageIncrease` |
| `CrystAbnormalInitial` 或 `Shatter` | `0x20 / 0x8000000` | `CrystAbnormalDamageIncrease` |
| `NaturalAbnormalInitial` | `0x100000` | `NaturalAbnormalDamageIncrease` |

这些检查彼此独立，匹配多项时会把对应属性全部相加。例如同时带两个爆发位的 hit 会读取两项爆发增伤，而不是只取一个“主类型”。组合常量 `Burst = 0x3c00000`、`SpellAbnormalInitial = 0x100038` 和 `IgniteDamageSet = 0xfd00038` 只是便于筛选的掩码，不是额外的倍率类别。

这里“灼热/异常增伤”是暂用的代码层归类：原生方法名为 `_GetAttackerDamageScaleIncreaseFromIgniteType`，但其实际读取同时覆盖 `*BurstDamageIncrease` 和 `*AbnormalDamageIncrease`。应以装饰位和属性字段为准，不应仅凭方法名把它收窄成火属性灼热。

`LifeDrain` 是明确的特殊分支：它在 `GetFinalAttackValue()` 中直接返回 `calcResult.value`，不执行 `AfterCalculation` damage modifier，也不乘 `GetFinalDamageScale()`。这不代表它跳过整个 `CalculateDamage()`，后续仍有独立分支需要核对。

## 属性抗性因子

`BattleFormula._GetDamageTypeResistanceValue()` 的原生跳转表覆盖 8 种 `DamageType`：

| 伤害类型 | 返回值 |
| --- | --- |
| `Physical` | `(1 - PhysicalResistance / 100) * PhysicalDamageTakenScalar` |
| `Real` | `1` |
| `Fire` | `(1 - FireResistance / 100) * FireDamageTakenScalar` |
| `Pulse` | `(1 - PulseResistance / 100) * PulseDamageTakenScalar` |
| `Cryst` | `(1 - CrystResistance / 100) * CrystDamageTakenScalar` |
| `LifeDrain` | `1` |
| `Natural` | `(1 - NaturalResistance / 100) * NaturalDamageTakenScalar` |
| `Ether` | `(1 - EtherResistance / 100) * EtherDamageTakenScalar` |

属性名称来自 `Beyond.GEnums.AttributeType`，数组下标与反汇编读取位置逐项一致。常量 `100.0` 和 `1.0` 直接来自运行时模块。调用方会将小于 0 的结果钳制到 0，因此这一因子本身不会产生负伤害。

`*DamageTakenScalar` 是运行时已汇总属性，不应再次按百分数除以 100。其默认值和同类加成的汇总规则属于 `Attributes/Modifier` 层。

## 防御因子

`BattleFormula._GetDefResistanceValue()` 读取目标的 `Def` 属性。`Real` 伤害直接返回 1；其他伤害使用以下分段曲线：

```text
if Def >= -0.00001:
  defFactor = 1 / (1 + k * Def)
else:
  defFactor = 2 - pow(1 - k, -Def)
```

其中 `k = BattleConst.efficiencyOfDEF = 0.01`，所以可写成：

```text
if Def >= -0.00001:
  defFactor = 1 / (1 + 0.01 * Def)
else:
  defFactor = 2 - pow(0.99, -Def)
```

`-0.00001` 是运行时常量，可视为零附近的浮点容差。两条分支在 `Def = 0` 时均为 1；正防御增加时因子趋近 0，负防御降低时因子趋近 2。

负防御分支在汇编中很长，是底层 `pow` 实现对负底数、整数指数、无穷和 NaN 的边界处理；主干语义是 `2 - pow(1 - k, -Def)`。相关数学调用中，`0x01D47B0` 是幂运算主体，`0x004F030` 是 NaN 检查。

该系数不是函数内常量，而是从本地 VFS 表加载：

```text
Table/Data/TableCfg/BattleConst.bytes
```

Common 元数据与反汇编访问链逐项对应：

1. `Beyond.Cfg.Tables.s_battleConst` 位于静态字段偏移 `0xC0`；
2. `BattleConst.efficiencyOfDEF` getter 的 RVA 为 `0x03B532A0`；
3. `_GetDefResistanceValue()` 取得该表对象后正是调用 `0x03B532A0`；
4. VFS 解密并按 SparkBuffer 解析后的字段值为 `0.009999999776482582`，即单精度 `0.01`。

同一张表还含 `efficiencyOfSTR = 5`、`efficiencyOfAGI = 0.001`、`efficiencyOfWISD = 0.001` 和 `recoverEfficiencyOfWILL = 0.001`，解释了为什么相邻属性换算方法共用同一配置入口。

## 暴击、庇护与格挡

`CalculateDamage()` 已确认存在以下原生流程：

1. 从攻击方属性数组读取 `CriticalRate`，调用概率判定函数，并写入输出参数 `isCritical`。
2. 仅在暴击成立时读取攻击方 `CriticalDamageIncrease`；暴击乘区为 `1 + CriticalDamageIncrease`。
3. 从 `DamagePackData.isBlocked` 读取格挡标记，并直接写入输出参数 `isBlocked`。公式函数本身不决定是否格挡。
4. 读取攻击方 `WeaknessDmgScalar` 和防御方 `ShelterDmgScalar`，分别进入最终乘算。已恢复的主干形态为 `WeaknessDmgScalar * (1 - ShelterDmgScalar)`。
5. 再乘防御因子、属性抗性因子及其他伤害类型/装饰标记对应的倍率。

因此，“格挡样式是否成立”与“实际减伤倍率是多少”是两条独立链路：前者是伤害包中的显示状态，后者来自伤害倍率区间或防御方运行时属性。`isBlocked` 本身不参与 `CalculateDamage()` 的数值运算，也不能被解释为一个固定比例的格挡减伤。

原生写入链已经定位：

```text
DamagePackData.ctor
  isBlocked = false

ProcessDamagePackData(AfterCalculation)
  DamageTextProcessor.ProcessDamagePackDataInternal
    if damageTextStyle == Block:
      packData.ModifyDamageTextStyle(Block)
      packData.isBlocked = true

BattleFormula.CalculateDamage
  output isBlocked = packData.isBlocked
```

`DamageTextProcessor.ProcessTiming` 的原生返回值是 `AfterCalculation = 0`；`DamageTextStyle` 只有 `Unchange = 0` 和 `Block = 1`。这说明 `isBlocked` 至少在原生主线中是由 hit 的 processor 配置标记，而不是在公式层根据攻击方向、碰撞或随机数计算。这里的 `AfterCalculation` 是 processor 的处理阶段，不表示它会修改已经算出的伤害值；该 processor 的函数体只修改显示样式和可选的显示数值来源。

当前已下载 BuffData 快照中共有 5 个配置文件包含 `DamageTextProcessor(Block)`：

| Buff | 实际数值机制 | 格挡显示机制 |
| --- | --- | --- |
| `buff_eny_0054_hsmino_chrdg_break_listen` | `NormalCalcZone` 的 processor 参数 `addition = -0.9` | `DamageTextProcessor(Block)` |
| `buff_eny_0077_agshield_hdg003_dmg_taken_down` | `ProdCalcZone` 的 `addition` 取黑板值 `dmg_taken_down`，样本值为 `-0.25` | `DamageTextProcessor(Block)` |
| `buff_eny_0081_ruanyi_partbuff` | `ProdCalcZone` 的 processor 参数 `addition = -0.95` | `DamageTextProcessor(Block)` |
| `buff_eny_0089_wgreflect_intensify` | `ProdCalcZone` 的 `addition` 取黑板倍率，配置回退值为 `-0.9` | `DamageTextProcessor(Block)` |
| `buff_eny_0113_jzogre_skill07_defense` | 五种属性伤害承伤倍率及失衡承伤倍率的 `BaseFinalMultiplier` 均设为 `0.05` | 按伤害类型条件注入 `DamageTextProcessor(Block)` |

最后一例尤其明确：伤害降至 5% 是由属性倍率完成，五个 `DamageTextProcessor` 只决定哪些伤害类型显示“格挡”。因此 Endaxis 若要模拟数值，应执行独立的倍率/属性修改；若要还原伤害文本，再额外投影 `isBlocked`。不能从其中一条链反推出另一条链。

这里的“5 个”仅指本地已下载的 BuffData 集合。仍需取得完整 SkillData/BuffData 后重新审计，并检查 IFix 是否添加其他 `isBlocked` 写入路径。

### 暴击随机数

暴击率使用 `[0, 1]` 比例，而不是 `[0, 100]` 百分数：

```text
if CriticalRate <= 0.00001:
  isCritical = false
else:
  sample = NextBattleRandom()  // [0, 1]
  isCritical = CriticalRate + 0.00001 >= sample
```

原生随机数发生器维护 56 项整数状态及两个循环下标，每次用两个状态值相减并写回，然后乘 `1 / 2147483647` 得到样本。这是有内部状态的确定性随机流，不是每次独立调用系统随机数。

因此应区分两种模拟目标：期望伤害可以直接按概率加权；逐击复现则必须取得或自行定义随机种子和状态推进顺序。当前尚未恢复战斗开始时的种子来源，也未确认暴击之外哪些系统共享同一随机流。

## 已定位的其他属性

`CalculateDamage()` 还会读取：

- 对应 `DamageDecorateMask` 分支的 `IgniteDamageScalar` 与 `PhysicalInflictionDamageScalar`；
- `defenderPoiseFactor`；
- 即时属性修正报告和服务器战斗报告字段。

原生主干可概括为：

```text
damage = finalAttackValue
       * (isCritical ? 1 + CriticalDamageIncrease : 1)
       * defResistanceFactor
       * damageTypeResistanceFactor
       * WeaknessDmgScalar
       * (1 - ShelterDmgScalar)
       * otherFactors
```

这不是最终可实现公式：`otherFactors` 尚包含按伤害类型和装饰标记选择的倍率，且 IFix 可以替换若干子入口。只有闭环这些分支后，才应将其实现到模拟器。

## 证据与复现

| 方法 | RVA |
| --- | ---: |
| `BattleFormula.CalculateDamage` | `0x03B53700` |
| `DamagePackData.GetFinalAttackValue` | `0x03B54760` |
| `BattleFormula._GetDefResistanceValue` | `0x03B54810` |
| `BattleFormula._GetDamageTypeResistanceValue` | `0x03B54E80` |
| `DamagePackData._GetFinalDamageScale` | `0x03B55B80` |
| `DamagePackData._GetDamageScale` | `0x03B55CA0` |
| `DamagePackData.ModifyDamageScaleZone` | `0x044B1670` |
| `DamageScaleProcessor.ProcessDamagePackDataInternal` | `0x044B14B0` |
| 暴击概率判定函数 | `0x04242800` |
| 战斗随机数取样函数 | `0x03771C70` |
| `DamageTextProcessor.ProcessDamagePackDataInternal` | `0x06D4C250` |
| `DamagePackData.ModifyDamageTextStyle` | `0x06D4BD74` |

离线分析产物位于忽略目录：

- `data/research-artifacts/combat-1.4.4/derived/runtime-snapshot-damage-formula.json`；
- `data/research-artifacts/combat-1.4.4/derived/runtime-snapshot-final-damage-scale.json`；
- `data/research-artifacts/combat-1.4.4/derived/damage-types.json`；
- `data/research-artifacts/combat-1.4.4/derived/attribute-types.json`。

用于命名枚举的终末地 metadata 与运行时快照尚未证明来自同一构建。仅在数组下标、分支立即数和运行时行为三者一致时采用其枚举名；方法地址、机器码和公式常量均来自 1.4.4 运行时快照。

## 下一步

1. 取得完整 SkillData/BuffData，重新审计格挡显示标记与实际减伤机制。
2. 追踪战斗随机数对象的构造与播种入口，并核对随机流共享范围。
3. 检查上述入口是否存在生效的 IFix Patch，并以 Patch 行为覆盖原生基线。
4. 用固定面板、固定敌人防御的游戏内样本验证最终值与舍入时点。
