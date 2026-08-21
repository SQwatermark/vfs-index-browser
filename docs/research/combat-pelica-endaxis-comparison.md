# 佩丽卡游戏配置与 Endaxis OperatorSheet 对照

## 目的

本文审计 Endaxis：

`src/data/operators/perlica.ts`

与佩丽卡的 `SkillData`、`SkillPatchTable`、`BuffData` 和
`PotentialTalentEffectTable`，区分哪些字段可以直接生成、哪些需要规则换算，以及哪些仍是
人工解释。

版本边界见 [佩丽卡技能数据结构还原](combat-pelica-skill-structure.md)。战技、连携和终结技
的动作结构来自本地 `1.4.4` 完整解码；技能等级数组和成长修正来自 AKEDB
`1.4.4@8764515-7` TableCfg。生成物必须保留完整 hotfix 版本，便于后续复现和比较。

## 总体结果

现有 `perlica.ts` 的主要伤害数据与游戏配置高度一致：

- 七组等级倍率全部可以在 `SkillPatchTable` 中找到；
- 所有命中偏移均能对应投射物发射帧或伤害动作帧；
- 失衡、技力恢复、终结技能量恢复、技能成本和冷却均有明确字段来源；
- 战技附着和连携导电的 `beforeDamage` 与动作数组顺序一致；
- 两项天赋、五项潜能均能对应 Buff 或 Patch；
- 最明显的未闭环字段是各 Segment 的 `duration`，尤其四段普攻没有表现出一条完全一致的
  推导公式；终结技 `animationTime` 也不能由单个配置字段直接得到。

因此第一版生成器可以自动产出大部分 `OperatorSheet`，但必须把持续时间推导和未支持语义
列入诊断，不能悄悄采用经验值。

## 身份与面板字段

| Endaxis 字段 | 游戏来源 | 结论 |
| --- | --- | --- |
| `gameId: PERLICA` | `chr_0004_pelica` / `engName: Perlica` | 可按稳定 ID 映射 |
| `rarity: 5` | `CharGrowthTable.rarity` | 直接生成 |
| `weapon: arts-unit` | `weaponType=2` | 需要一张通用枚举映射表 |
| `element: electric` | `charTypeId=Pulse` | 需要 `Pulse -> electric` 映射 |
| `class: caster` | `profession=5` | 需要职业枚举映射 |
| `mainAttribute: intellect` | `mainAttrType=41` | 需要属性枚举映射 |
| `subAttribute: will` | `subAttrType=42` | 需要属性枚举映射 |
| `finisherElement`、`diveElement` | 重击/下落 DamageUnit 为 `Pulse` | 可从对应内部技能推导 |
| 六组属性成长数组 | Character/成长相关表 | 已属于面板数据管线，本轮未重新逐级验算 |

这些字段不是从 `SkillData` 得到，但同样属于结构化表格数据，不需要角色专属代码。

## 普通攻击

### 倍率与分段模式

Endaxis 使用 UI 展示的总倍率，并用 `multiplierMode: 'split'` 分配到多个 hit。游戏 Patch
同时保存实际计算倍率和展示倍率：

| 段 | 游戏一级实际倍率 | 游戏一级展示倍率 | 命中数 | Endaxis 一级倍率 | 结论 |
| --- | ---: | ---: | ---: | ---: | --- |
| 1 | 25% | 未单列 | 1 | 25% | 直接对应 |
| 2 | 15%/hit | 30% | 2 | 30% split | 直接对应 |
| 3 | 12%/hit | 37% | 3 | 37% split | 对应，展示值包含舍入 |
| 4 | 57% | 未单列 | 1 | 57% | 直接对应 |

四段等级 1～12 数组均与 `SkillPatchTable` 对应。生成规则应优先读取
`display_atk_scale`；没有该键时使用 `atk_scale × 命中数`，同时保留游戏提供的展示舍入值。

### 命中偏移

游戏逻辑帧按 30 FPS 换算：

| 段 | 发射/命中帧 | 换算秒数 | Endaxis |
| --- | --- | --- | --- |
| 1 | 8 | 0.2667 | `0.267` |
| 2 | 9、12 | 0.3、0.4 | `0.3`、`0.4` |
| 3 | 16、19、22 | 0.5333、0.6333、0.7333 | `0.53`、`0.63`、`0.73` |
| 4 | 27 | 0.9 | `0.9` |

命中偏移可以稳定生成，只有输出小数位数属于格式化策略。

### 第四段资源和失衡

`attack4` 等级 Patch 固定写入：

```text
poise = 15
atb = 15
```

命中节点包含对应的失衡计算和 `ObtainCostAction`。因此 Endaxis 的
`spRecovery: 15`、`stagger: 15` 可直接生成。

### Segment duration

现有值为 `0.53 / 0.63 / 0.9 / 1.467`，完整四段经录像验证约为 `3.53s`。直接使用
`AllowNextSkillAction.startFrame / 30` 会得到 `0.533 / 0.6 / 0.867 / 1.8`，合计 `3.8s`，
因此该字段不能单独解释 segment duration。

结合 `exclusiveFrame` 后，佩丽卡四段出现了统一关系：

| 段 | AllowNext 起始帧 | exclusiveFrame | 较早边界 | 现有 duration 对应帧 |
| --- | ---: | ---: | ---: | ---: |
| 1 | 16 | 15 | 15 | 16 |
| 2 | 18 | 22 | 18 | 19 |
| 3 | 26 | 29 | 26 | 27 |
| 4 | 54 | 43 | 43 | 44 |

即现有值恰好符合 `(min(AllowNextSkillAction.startFrame, exclusiveFrame) + 1) / 30`。但这不应
解释为“前三段使用 AllowNext、末段使用 exclusiveFrame”：庄方宜第五段恰好相反，
`AllowNext=50`、`exclusiveFrame=55`，现有 `duration=1.67` 选择的是较早的 AllowNext。
庄方宜前四段也全部接近两者的较早值：`15 / 15 / 26 / 17` 帧。

运行时类型关系为该候选规则提供了机制依据：

```text
AllowNextSkillAction.ExecuteInternal
  -> ComboController.AddAllowedNextSkillPack

Skill.OnTick
  -> 到达 SkillData.exclusiveTime
  -> Skill._ExecuteExclusiveCallback
  -> AbilitySystem.OnSkillExclusiveTimeExecuted
  -> ComboController.OnSkillExclusiveTimeExecuted
```

`AbilitySystem.CheckCanInterruptCurSkill`、`ComboController._AllowNextSkill` 和
`AllowedNextSkillPack.skillList` 表明这里存在两条并行放行路径：特定的下一技能可以由
`AllowNextSkillAction` 提前放行；当前技能到达 `exclusiveFrame` 后则进入一般可打断阶段。
因此“玩家能够实际进入下一段的最早时点”为两者较早值，是目前最有解释力的候选模型。

尚未闭环的是端点计数。佩丽卡现有值与录像支持较早边界后加一帧；庄方宜现有值则直接按边界帧
除以 30。可能原因包括版本数据的一帧差异、时间轴闭区间语义或既有配置精度差异。需要读取上述
运行时函数体，并对庄方宜完整普攻录像逐帧校准后，才能将该规则接入生成器。在此之前生成器应
省略 segment `duration` 并报告缺失。

## 战技

| Endaxis 字段 | 游戏配置 | 结论 |
| --- | --- | --- |
| `duration: 0.93` | `AllowNextSkillAction` 从第 28 帧开始 | `28/30=0.9333`，可推导 |
| `offset: 0.43` | 第 13 帧执行命中序列 | `13/30=0.4333`，可推导 |
| 十二级倍率数组 | `normal_skill` SkillPatch 的 `atk_scale` | 完全对应 |
| `stagger: 10` | Patch 黑板 `poise=10` | 完全对应 |
| `electric` | DamageUnit 和 SpellInfliction 均为 `Pulse` | 枚举映射后对应 |
| `applyTiming: beforeDamage` | `SpellInfliction -> DamageAction` | 动作顺序直接证明 |
| 隐式战技成本 100 | SkillPatch `costType=Atb, costValue=100` | Endaxis 系统默认值也是 100 |

基础 `SkillData.castData.costValue=40` 与最终 Patch 的 `100` 不同，证明生成器必须构造最终
运行时配置，不能只读取基础 SkillData。

## 连携技

| Endaxis 字段 | 游戏配置 | 结论 |
| --- | --- | --- |
| `duration: 0.83` | 第 24 帧发射投射物，第 25 帧进入允许后续技能区间 | 合理对应，需统一 duration 规则 |
| `offset: 0.8` | 第 24 帧发射命中投射物 | `24/30=0.8`，直接对应 |
| 十二级倍率数组 | Patch `atk_scale=0.8...1.8` | 完全对应 |
| `stagger: 10` | Patch `poise=10` | 完全对应 |
| `ultEnergyGain: 10` | Patch `usp=10`，命中末尾 `ObtainCostAction` | 完全对应 |
| 导电持续 5 秒 | Patch/命中节点 `duration=5` | 完全对应 |
| `beforeDamage` | `CreateBuff(导电) -> DamageAction` | 动作顺序直接证明 |
| 冷却 `20...19` | SkillPatch `coolDown` | 十二级全部对应 |

`comboSkill.ultimateEnergyGain: 0` 与命中效果中的 `ultEnergyGain: 10` 不冲突：前者表示技能
本身没有额外的固定回能，后者表示投射物成功命中后的回能。

### 连携窗口

现有配置为全局最后一击开启 5 秒窗口。持续 5 秒与角色技能描述一致，但连携窗口入口位于
`SkillDataBundle/BattleManager` 一侧，不在本轮已经完整解码的根 SkillData 中。因此：

- `duration: 5`：有文本与现有模型支持；
- `onFinalStrike + global`：语义合理，但仍需从本地同版本 SkillDataBundle 精确验证。

不能仅靠连携技动作树生成该字段。

## 终结技

| Endaxis 字段 | 游戏配置 | 结论 |
| --- | --- | --- |
| `offset: 1.93` | 第 58 帧开始伤害序列 | `58/30=1.9333`，直接对应 |
| `duration: 2.1` | 伤害 TimelineAction 到第 63 帧 | `63/30=2.1`，对应当前语义 |
| 十二级倍率数组 | Patch `atk_scale=4.45...10` | 完全对应 |
| `stagger: 20` | Patch `poise=20` | 完全对应 |
| `ultimateEnergyCost: 80` | SkillPatch `costValue=80` | 完全对应 |
| `cooldown: 10` | SkillPatch `coolDown=10` | 完全对应 |
| `animationTime: 1.583` | 未找到单一直接字段 | 尚不能自动生成 |

基础 SkillData 中成本为 100、冷却为 0，最终值再次来自 SkillPatch。终结技时间轴还生成
独立 AbilityEntity 负责范围表现，但它当前不产生额外伤害，因此 Endaxis 单次伤害段没有
遗漏该实体的伤害。

`animationTime=1.583` 与动画总长、`UltimateTimeAction` 的 0～50 帧和伤害起始帧均不完全
相等，可能涉及终结技停时、镜头时钟或已有人工实测。需要运行时确认后才能形成通用规则。

## 天赋

### 失衡增伤

Endaxis 第一项天赋：

```text
enemyStaggered -> 全元素 dmgBonus +20%/+30%
```

游戏 Buff：

```text
CheckPoiseValue(target == 0)
  -> DamageScaleProcessor(addition=0.2/0.3)
```

二者完全对应。元素数组只是 Endaxis 用“全部当前伤害元素”表达无元素限制的方式。

### 连携额外弹射

Endaxis 第二项天赋只有 `levels: 1`，没有效果。游戏配置实际存在：

```text
talent2 == 1
AND 目标破防
AND 尚未弹射
  -> 排除当前目标
  -> 选择 15 米内另一目标
  -> 发射第二枚连携投射物
```

这不是数据遗漏，而是**当前 Endaxis 单目标模型没有表达多目标弹射伤害**。因为游戏明确
排除原目标，该天赋不会增加当前单体目标伤害；保留空项在单目标模拟范围内是合理降维，
但生成器应输出“省略了多目标效果”的诊断，而不是静默丢弃。

## 潜能

| 潜能 | 游戏数据 | Endaxis | 结论 |
| --- | --- | --- | --- |
| 1 | 连携 `duration × 1.75` | `durationExtension=5×0.75` | 等价 |
| 2 | 终结技成本 `×0.85` | 成本降低 15% | 等价 |
| 3 | 施加导电后攻击 +20%，5 秒，最多 2 层 | 同条件和数值 | 完全对应 |
| 4 | 连携 `extra_scaling=1.33` | 导电 `effectiveness=1.33` | 语义对应 |
| 5 | 终结技 `crit += 0.3` | 终结技暴击率 +30% | 完全对应 |

潜能 3 的事件链和叠层策略均可由 BuffData 生成；潜能 1、2、4、5 可由
`PotentialTalentEffectTable` 的修改类型生成。

## 差异分类

### 可以直接生成

- 身份、星级、武器/职业/属性/元素枚举映射；
- 技能等级倍率数组；
- hit 数量和偏移；
- 伤害元素、失衡、技力和终结技能量；
- 成本、冷却；
- 附着/Buff/伤害的先后顺序；
- 第一项天赋和五项潜能；
- 第二项天赋的条件图，以及“因单目标模型而不落地”的诊断。

### 有明确规则但需要跨资源解析

- `split`：结合发射次数、`atk_scale` 和 `display_atk_scale`；
- 连携反应：从 Projectile SkillData 和 BuffData 合并；
- 等级最终值：基础 SkillData 与 SkillPatch 合并；
- 天赋潜能：成长表、效果表、BuffData 和目标技能黑板联结；
- 连携窗口：从 SkillDataBundle/连携条件配置生成。

### 尚不能可靠生成

- 普攻四段 `duration` 的统一选择规则；
- 终结技 `animationTime`；
- 某些表现时间轴是否会改变实际可操作时间；
- 多目标效果在 Endaxis 单目标抽象中的统一降维策略。

## 对生成器的直接要求

第一版佩丽卡生成器应同时产生三份结果：

1. `CharacterCombatIR`：保留游戏原始身份、帧、动作顺序和引用来源；
2. 候选 `OperatorSheet`：只输出已经有确定映射的字段；
3. 诊断报告：列出 duration、animationTime、多目标弹射和所有无法映射的动作。

与现有 `perlica.ts` 比较时，应把字段标为：

```text
exact       游戏数据直接一致
derived     经过公开规则换算后一致
curated     现有值合理，但来源尚未闭环
omitted     游戏有语义，Endaxis 当前领域模型主动忽略
unsupported 游戏语义尚无对应抽象
```

这套分类可以直接复用于后续所有干员，避免生成器用“看起来差不多”的值掩盖缺失能力。
