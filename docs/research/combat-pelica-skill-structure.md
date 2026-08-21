# 佩丽卡技能数据结构还原

## 结论

佩丽卡的战斗内容可以概括为：**硬编码的通用战斗虚拟机，执行数据驱动的角色配置**。

- 技能时间、动作顺序、投射物、伤害类型、倍率键、失衡值、附着、Buff、天赋条件、潜能修正和镜头表现均由数据描述；
- `TimelineActionProcessor`、`SequenceAction`、`DamageAction`、`CreateBuffAction`、`IfElseAction`、目标选择器和资源系统等通用节点的执行语义写在客户端代码中；
- 当前 `Gameplay.Beyond` 类型信息中没有发现佩丽卡专用战斗类、方法或战斗 ID 分支。仅有的 `Pelica` 符号属于面部 Morph 和 NPC 配件种族枚举，与技能结算无关；
- 目前没有证据支持“佩丽卡技能主体由角色专属原生代码实现”。但也不能仅凭静态类型名排除某个通用系统内部比较技能 ID 或标签哈希，后续仍应抽样 Hook 验证。

换言之，角色设计不是一个 `PelicaSkill` 类，而是一张由通用动作节点组成的有向图。

## 证据与版本边界

本地客户端样本目录为：

`data/research-artifacts/combat-1.4.4/samples/skill/pelica/`

其中战技、连携技和终结技已经从本地 VFS 精确读取并完整消费：

| 技能 | 原始字节 | 顶层字段 | 时间轴组 |
| --- | ---: | ---: | ---: |
| `chr_0004_pelica_normal_skill` | 12354 | 47 | 25 |
| `chr_0004_pelica_combo_skill` | 8576 | 47 | 24 |
| `chr_0004_pelica_ultimate_skill` | 15421 | 47 | 33 |

三份本地样本用于确认运行时结构和关键行为。其余节点的拓扑盘点使用 AKEDB `SkillData`
清单。2026-08-02 查询时，本地证据与 AKEDB 最新 TableCfg 均属于 `1.4.4`；生成结果仍应
记录 AKEDB 的完整版本 `1.4.4@8764515-7`，避免后续 hotfix 改动被大版本号掩盖。

## 四层数据结构

```text
CharGrowthTable.skillGroupMap
  -> SkillDataBundle / skillIdList
    -> SkillData
      -> ActionGroupData
        -> TimelineActionData
          -> SequenceActionData
            -> AbilityActionData[]

PotentialTalentEffectTable
  -> SkillPatch / attachBuff
    -> SkillData.blackboard / BuffData.blackboard
```

### 展示技能组

成长表只向玩家展示四组技能：

| 技能组 | 直接包含的 SkillData |
| --- | --- |
| 普通攻击 | `attack1`、`attack2`、`attack3`、`attack4`、`power_attack`、`plunging_attack_end` |
| 战技 | `normal_skill` |
| 连携技 | `combo_skill` |
| 终结技 | `ultimate_skill` |

冲刺攻击、闪避、下落开始段、投射物命中、范围实体和被动节点不会作为独立技能显示，
但仍是完整执行图的一部分。

### 基础 SkillData

`SkillData` 定义不随等级变化的主体结构：

- 技能身份、规格、施放类型、目标策略和基础施放规则；
- `CastData` 中的冷却、资源类型、成本和转向规则；
- `durationFrame`、`exclusiveFrame`；
- `ActionGroupData.timelineActions`；
- 默认黑板、Buff 和技能附着配置。

### SkillPatch 与黑板

技能等级、天赋和潜能一般不复制整棵动作树，而是修改黑板或参数：

- 技能等级向 `atk_scale`、`poise`、`atb`、`usp` 等键写入对应等级数值；
- 潜能 1 将连携 `duration` 乘以 `1.75`；
- 潜能 2 将终结技能量成本乘以 `0.85`；
- 潜能 4 将连携 `extra_scaling` 设为 `1.33`；
- 潜能 5 向终结技 `crit` 加 `0.3`；
- 第二天赋将连携 `talent2` 从 `0` 改为 `1`，开启额外弹射分支。

因此运行时技能应理解为：

```text
最终技能 = 基础 SkillData + 等级 SkillPatch + 天赋/潜能修正 + 运行时黑板
```

不能只读取一份基础 JSON 就认为已经得到最终技能数值。

## 27 个 SkillData 节点

AKEDB 清单中共有 27 个 `chr_0004_pelica*` 节点，可以按职责重组如下。

| 子系统 | 节点 |
| --- | --- |
| 四段普攻 | `attack1`～`attack4` |
| 普攻命中 | `attack1_projhit`、`attack1_projhit02`、`attack2_projhit`、`attack3_projhit`、`attack4_projhit`、`attack4_projhit_no_effect` |
| 其他基础动作 | `dash_attack`、`dash_attack_projhit`、`power_attack`、`plunging_attack_start`、`plunging_attack_end`、`plunging_attack_projhit`、`dodge` |
| 战技 | `normal_skill` |
| 连携技 | `combo_skill`、`combo_skill_projhit` |
| 终结技 | `ultimate_skill`、`ultimate_skill_abilityrange` |
| 被动与内部占位 | `drone_fx_passive`、`extra_attack`、`talent_0`、`talent_1`、`talent_1_1` |

部分零时间轴或空时间轴节点可能是配置占位、由外部 Projectile/Buff 配置触发的命中入口，
或供 Patch/技能集合引用。仅凭自身为空不能认定它没有运行时用途。

## 普通攻击链

四段普攻由 `ComboCacheAction` 和 `AllowNextSkillAction` 显式连接：

```text
attack1 -> attack2 -> attack3 -> attack4 -> attack1
```

每段本体主要负责动画、位移、朝向、武器显示、投射物和衔接窗口；伤害放在独立的
`*_projhit` 中处理。

| 段 | 投射物帧 | 下一段缓存区间 | 允许切入下一段 | 基础倍率键 |
| --- | ---: | --- | --- | ---: |
| 1 | 8 | 5～27 | 16～27 | `atk_scale=0.25` |
| 2 | 9、12 | 0～28 | 18～28 | `atk_scale=0.29` |
| 3 | 16、19、22 | 8～40 | 26～40 | `atk_scale=0.23` |
| 4 | 27 | 29～64 | 54～64 | `atk_scale=1.07` |

这里是基础 `SkillData` 黑板值，不是满级最终倍率。第二、三段发射多个投射物，倍率由
命中次数分担；第四段命中节点还包含失衡、技力恢复、HitStop 和主控条件分支。

重击不是凭空生成的独立 hit。`power_attack` 是独立 `SkillData`，由输入/普攻链系统切入，
其帧 35～44 执行：

```text
DamageAction
  -> 条件分支
  -> 击倒、受击、镜头、重击技力恢复等通用动作
```

冲刺攻击和下落攻击也采用同样的“表现本体 + 命中节点”拆分。`dodge` 在底层确实是一个
`CharacterDodge` SkillData，但这不等于游戏或排轴编辑器需要把闪避建模为用户技能块。

## 战技

本地同版本战技的核心时间轴为：

```text
0～2 帧    选目标，并按主控/AI分支处理站位
11～12 帧  ConvertToTargetContext
13 帧      FindTarget
            -> Interrupt
            -> SpellInfliction(Pulse)
            -> DamageAction(Pulse HP + Poise)
            -> EnemyHurtAnim
            -> CameraImpulse
            -> CreateBuff(buff_common_obtain_ultimate_sp)
28～54 帧  AllowNextSkillAction
```

这确认电磁附着在伤害之前执行，终结技能量 Buff 在伤害之后创建。技能本体同时包含
主控与 AI 不同的选点、传送、镜头和朝向分支，但都由通用 `IfElseAction` 和选择器表达。

## 连携技

`combo_skill` 负责施法动作和发射
`projectile_chr_0004_pelica_combo_skill`；命中后转入 `combo_skill_projhit`。

投射物命中的同一条 `SequenceAction` 顺序为：

```text
条件额外弹射
  -> CameraImpulse
  -> Interrupt
  -> CreateBuff(教程标记)
  -> CreateBuff(buff_common_pulse_pulse_conduct_triggered)
  -> DamageAction
  -> EnemyHurtAnim
  -> HitStop
  -> ObtainCostAction
```

因此连携施加的导电 Buff 位于伤害之前。第二天赋没有专用代码，而是使用以下数据分支：

```text
talent2 == 1
AND 当前目标具有破防标签
AND EntityBB_bounced == 0
  -> 标记已弹射
  -> 在 15 米内寻找并排除当前目标
  -> 再发射一个同类型 projectile
```

新投射物写入 `EntityBB_bounced=1`，避免递归弹射。

## 终结技

终结技本体负责动画、镜头、选敌和直接伤害：

```text
0～50 帧   UltimateTimeAction
0～52 帧   HideUIAction
58～63 帧  FindTarget -> Interrupt -> Damage -> CameraImpulse -> EnemyHurtAnim
55～58 帧  SpawnAbilityEntity
63～90 帧  AllowNextSkillAction
```

生成的能力实体使用 `chr_0004_pelica_ultimate_skill_abilityrange`，后者负责持续特效、声音和
定时 `FinishOwnerAction`。这说明“终结技落雷/范围表现”不是写死在终结技函数里，而是另一
个由数据创建并独立运行时间轴的能力实体。

## 天赋和潜能

### 第一项天赋：失衡增伤

成长节点通过 `PotentialTalentEffectTable` 挂载
`buff_chr_0004_pelica_talent_0`，并将 `dmg` 写成 `0.2` 或 `0.3`。Buff 使用：

```text
CheckPoiseValue(target == 0)
  -> DamageScaleProcessor(NormalCalcZone, addition=dmg)
```

条件、倍率和生效区均为数据。

### 第二项天赋：连携额外弹射

成长节点不挂专用执行类，只把连携黑板 `talent2` 设为 `1`。真正的条件和额外投射物位于
`combo_skill_projhit` 的 `IfElseAction` 中。

### 潜能 3：施加导电后攻击提升

潜能挂载监听 Buff：

```text
OnOutputBuff
  -> CheckBuffIdInContext(导电标签)
  -> CreateBuff(buff_chr_0004_pelica_potential_3_atkup)
```

攻击 Buff 使用 `Atk / BaseMultiplier`，持续 5 秒，`EnhanceAndRefresh`，最多 2 层。
`0.2`、`5` 和 `2` 均由潜能表注入黑板，而不是原生代码中的佩丽卡常量。

## 数据驱动与硬编码的准确边界

### 数据驱动的部分

- 哪些技能属于佩丽卡，以及普攻链如何衔接；
- 逻辑帧、动作数组顺序、投射物与能力实体引用；
- 伤害、失衡、附着、Buff 和资源动作的先后顺序；
- 技能倍率、等级成长、天赋开关、潜能倍率和持续时间；
- 条件分支、目标选择、标签检查、是否主控、镜头与动画；
- Buff 的事件监听、叠层、刷新和属性修正。

### 通用硬编码的部分

- `TimelineActionProcessor` 如何按时间推进节点；
- `SequenceAction` 如何依次执行动作；
- 每种 `AbilityAction`、Condition、Finder、Validator 的具体语义；
- 伤害公式、属性区、Buff 生命周期、标签和事件系统；
- ATB/USP、冷却、技能状态和 AbilityEntity 的通用管理。

这是一种常见的解释器式设计：客户端代码提供指令集，角色配置提供程序。

## 尚未补齐

1. 从本地同版本 VFS 补取其余 24 个 SkillData，消除 AKEDB 最新版带来的版本不确定性；
2. 读取佩丽卡完整 `SkillDataBundle`，确认 27 个清单节点中哪些实际挂到战斗实体，哪些仅由
   Projectile/AbilityEntity 间接引用；
3. 补取相关 ProjectileData 和全部 BuffData，建立无悬空边的完整调用图；
4. 验证 `attack1_projhit02`、`attack4_projhit_no_effect`、`extra_attack` 和空命中节点的入口；
5. Hook `Skill._OnCreate/RefreshRuntimeData`，记录基础 SkillData、SkillPatch 和最终黑板，确认
   等级/天赋/潜能的实际合并顺序；
6. 对 `SpellInfliction -> Damage` 与 `CreateBuff -> Damage` 做运行时状态采样，验证前置效果是否
   在同次伤害计算中已经可见。

现有 Endaxis 配置的逐字段审计见
[佩丽卡游戏配置与 Endaxis OperatorSheet 对照](combat-pelica-endaxis-comparison.md)。
