# 普攻连段、输入缓存与接续窗口

## 研究目标

本文解释一条攻击指令如何在当前技能尚未结束时被缓存，并在允许接续后变成下一段普攻。
这里有三种容易混淆但运行时语义不同的机制：

1. **命令映射**：按下 Attack 后应该请求哪个实际 `Skill`；
2. **输入缓存**：玩家提前按下的指令可以保留多久；
3. **接续许可**：当前技能尚处于独占阶段时，某个指定技能能否提前接入。

`exclusiveFrame` 只描述通用独占边界，不负责选择下一段技能，也不等于输入缓存窗口。

## 通用对象

`ComboController` 同时维护三层命令映射：

| 字段 | 作用 |
| --- | --- |
| `m_baseCommandMapping` | 角色默认的命令到技能映射 |
| `m_runtimeCommandMapping` | Timeline、形态或临时替换注入的候选映射队列 |
| `m_realCommandMapping` | 按优先级结算后的当前有效映射 |

有效映射使用 `MappingModifier` 表达，包含：

- `skillId`：命令当前指向的实际技能；
- `instId`：该次 modifier 的稳定实例身份；
- `priority`：候选映射的优先级；
- `cacheTime`：由该映射产生的请求可缓存的时间。

`NextSkillRequest` 则保存一次已经发生、但尚未真正施放的输入：

- `nextSkillId`；
- `remainingTime`；
- `allowToCastNextSkill`；
- `modifierInstId`；
- 可选的 `SkillCastInputData`。

因此“下一段是什么”和“玩家是否已经按下下一段”是两份不同状态。

## ComboCacheAction：临时选择下一段

技能时间轴中的 `ComboCacheAction.ExecuteInternal` 会遍历 `mappingDataList`，调用
`AbilitySystem.AddMappingModifier`。每条 `BattleCmdMappingModifierData` 包含：

| 字段 | 已确认用途 |
| --- | --- |
| `cmdType` | 被临时改写的战斗命令类型 |
| `skillId` | 该命令在窗口内映射到的技能 ID |
| `overrideCacheTime` / `cacheTime` | 是否覆盖以及覆盖后的输入缓存时长 |
| `cacheEndByAction` | 生成 Handle 时决定该 Action 结束时是否清除相应请求 |
| `clearOffsetTargetSkillIdOnEnd` | 移除映射时是否同时清除 offset 目标技能 |

`ExecuteInternal` 保存每次添加映射返回的 `MappingModifier.Handle`。本地 1.4.4 运行时快照
确认 `ComboCacheAction.OnEnd` 会：

```text
遍历 m_handles
  -> Handle.RemoveModifier()
清空 m_handles
```

`Handle.RemoveModifier` 再负责撤销对应的 runtime mapping，并可按 Handle 配置通过
`modifierInstId` 清理由该映射产生的技能请求。这说明下一段映射是一个有明确生命周期的
时间轴窗口，不是对角色基础普攻配置的永久修改。

## AllowNextSkillAction：允许提前接续

`AllowNextSkillAction.ExecuteInternal` 根据配置的技能列表创建 `AllowedNextSkillPack`，并添加到
`ComboController.m_allowedNextSkillPacks`。`ComboController._AllowNextSkill` 最终通过
`AbilitySystem.CheckCanInterruptCurSkill` 判断当前请求能否接入。

本地运行时快照确认 `AllowNextSkillAction.OnEnd` 会取回此前保存的 Pack，并调用：

```text
AbilitySystem.RemoveAllowedNextSkillPack(pack)
```

因此它只在 Action 覆盖的帧区间内授予提前接续许可。它既不改变 Attack 命令映射到哪个技能，
也不检查冷却、资源、标签和实体状态等完整释放条件。

## 玩家输入的完整协作过程

把两种 Timeline Action 与玩家请求链合在一起，可得到目前已确认的主干：

```text
当前普攻段开始
  -> ComboCacheAction 生效：Attack 临时映射到下一段 Skill
  -> 玩家按 Attack
  -> RefreshNextSkillRequest 读取当前有效映射并保存请求与缓存时间
  -> AllowNextSkillAction 生效，或当前技能越过 exclusiveFrame
  -> ComboController.Tick 更新 allowToCastNextSkill
  -> CenterStateMachine 将获准请求转换为技能状态迁移
  -> CenterSkillState 调用 TryCastSkill
  -> 请求被消费并清除

窗口结束但请求尚未消费
  -> ComboCacheAction.OnEnd 撤销映射
  -> 按 Handle 配置清除该映射产生的缓存请求
```

这也解释了为什么不能仅用一个 `duration` 字段完整表达普攻段：动画/动作持续时间、可以提前
输入的窗口、输入能缓存多久以及实际允许切入下一段的时刻，是不同维度。

## `OnSkillCastStart` 不负责普通连段递进

`ComboController.OnSkillCastStart(skillId)` 会记录当前技能 ID，并在存在
`OffsetModifierPack` 时更新该 Pack 的触发和持续状态。其字段包括 `triggerType`、
`triggerSkillId`、`duration`、`reduceDuration` 和 `skillCasted`。

现有反汇编没有显示它推进“普攻第几段”。因此普通连段的核心应当描述为 Timeline 注入映射、
缓存输入、授予接续许可和窗口结束清理；offset mapping 是另一套短期输入偏移机制，不能拿来
解释所有普攻段数变化。

## 佩丽卡样本

已解码的佩丽卡普通攻击数据表现为四个实际 Skill 之间的映射链：

```text
attack1 -> attack2 -> attack3 -> attack4 -> attack1
```

每段都分别配置 `ComboCacheAction` 与 `AllowNextSkillAction`，且二者覆盖的帧区间并不相同。
这与通用机制一致：前者决定 Attack 当前指向哪一段并管理缓存，后者决定何时可以提前切入。

佩丽卡样本可以证明这种配置方式确实用于角色普攻，但不能单独证明所有角色都具有四段、末段
回到第一段，或使用相同的窗口时点。

## 尚未闭环

1. `MappingModifier.Priority` 的精确数值顺序，以及同优先级候选的稳定选择规则；
2. `cacheEndByAction=false` 时请求由哪条后续路径清理；
3. 移动、切人、受击、闪避、超时和未命中分别是否重置连段；
4. 长按攻击与重击如何在命令映射层替换普通下一段；
5. AI 普攻是否复用相同缓存窗口，还是直接按行为树调用对应 Skill；
6. 同一逻辑帧内 Action 开始/结束、输入采样和 `ComboController.Tick` 的稳定先后顺序。

这些问题应优先通过两种方式验证：静态追踪所有 mapping Handle 的创建和移除调用；运行时记录
每帧的有效 Attack 映射、`NextSkillRequest`、当前 Skill、exclusive 状态和输入命令。

## 运行时证据

- 客户端：1.4.4；
- `ComboController._UpdateRealEffectedMapping`：RVA `0x3318040`；
- `ComboController.OnSkillCastStart`：RVA `0x47368c0`；
- `ComboCacheAction.OnEnd`：RVA `0x3317130`；
- `AllowNextSkillAction.OnEnd`：RVA `0x461BDD0`；
- 可复现报告：
  `data/research-artifacts/combat-1.4.4/derived/runtime-snapshot-combo-lifecycle.json`。
