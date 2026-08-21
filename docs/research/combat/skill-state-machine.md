# 技能请求、替换与生命周期状态机

## 研究范围

本文只记录通用技能状态机。角色专属强化、形态和被动效果应先映射到这里的通用节点，只有
无法由通用节点表达时才建立角色案例。

## 已确认的运行时主干

完整运行时快照中，`AbilitySystem.TryCastSkill(string, ...)` 的可达直接调用显示：

```text
检查组件状态
  -> _InitSkillIfNot(skillId)
  -> CheckCanInterruptCurSkill(newSkill)
  -> Skill.CanCastToTarget(...)
  -> Skill.TrySwitchToAddBuff(...)
  -> Ability.AssignSkillCastInfo(...)
  -> Skill.SaveTarget(...)
  -> AbilitySystem.BeforeCastStart(...)
  -> Skill.DoCast(...)
  -> Skill.OnTick(...)
  -> 必要时 Interrupt 当前技能
  -> 清理 OffsetModifier
```

这里确认的是可达调用集合和局部顺序。尚不能把它简化成一条无分支直线：目标检查、
SwitchToAddBuff、当前技能中断以及清理分支都有各自条件。

## 可用性检查的调用边界

`Skill.IsAvailable` 自身已确认按冷却、成本、标签和实体状态短路检查。但本轮对
`TryCastSkill` 与 `CanCastSkill` 的直接调用分析中，没有出现一个可清晰符号化为
`Skill.IsAvailable` 的直接调用。

因此当前只能确认两件事：

1. `IsAvailable` 是具有完整函数体的检查聚合入口，其内部语义可以确认；
2. 不能未经取证就声称所有 `TryCastSkill` 路径都在函数内部直接调用它。

全模块没有发现指向 `IsAvailable` 的 `call rel32`，只在方法表区域发现一个绝对函数指针。
可能解释包括：调用方先分别做检查、通过委托/方法指针调用、IFix 路径，或者该聚合入口并非
主释放路径。下一步应建立各个子检查与 `IsAvailable` 的调用者集合，并分别检查玩家输入、
AI、Timeline 内释放和调试入口。

## 玩家输入到实际施放

主控干员的输入链已经可以分成四层，不能把任意一层的返回值统称为“技能可释放”：

```text
CharacterMainCtrlSystem.OnExecutePlayerCommand(cmdType)
  -> AbilitySystem.RefreshNextSkillRequest(cmdType)
  -> ComboController.RefreshNextSkillRequest(cmdType)
       解析当前实际命令映射
       计算输入缓存时间
       保存 NextSkillRequest

ComboController.Tick(...)
  -> _AllowNextSkill(rawNextSkillId)
  -> AbilitySystem.CheckCanInterruptCurSkill(nextSkill)
  -> 将结果写入 NextSkillRequest.allowToCastNextSkill

CenterStateMachine._ToSkill()
  -> 读取仅在 allowToCastNextSkill=true 时可见的 nextSkillId
  -> 检查技能实例和目标输入
  -> CheckCanInterruptCurSkill(...)
  -> CanCastSkill(...)
  -> 将 SkillCastInputData 复制到 CenterBlackboard
  -> 返回 true，允许状态机进入 CenterSkillState

CenterSkillState.OnEnter(...)
  -> _DoCastSkill()
  -> AbilitySystem.TryCastSkill(...)
  -> AbilitySystem.ClearSkillRequest()
```

这里的 `NextSkillRequest` 同时保存原始技能 ID、缓存剩余时间、衔接许可结果、映射
modifier 身份和可选的目标输入。几个 getter 的语义不同：

| getter | 已确认语义 |
| --- | --- |
| `rawNextSkillId` | 只要请求存在就返回原始技能 ID，不要求当前已经可以衔接 |
| `nextSkillId` | 请求存在且 `allowToCastNextSkill=true` 时才返回技能 ID |
| `hasNextSkillRequest` | 名称容易误导，实际同样要求请求已获准 |
| `hasNextSkillRequestPrediction` | 不等待 Tick 写回许可，而是对原始请求即时调用 `_AllowNextSkill` |
| `nextSkillCastInputView` | 请求及其可选输入均存在时返回目标输入视图 |

`_AllowNextSkill` 本身只解析技能并调用 `CheckCanInterruptCurSkill`。它回答的是“这个请求
现在能否衔接当前技能”，不是冷却、资源、标签和实体状态都合法。

`CenterStateMachine._ToSkill` 也不直接施放技能。它把已经获准的请求转换为状态机迁移，
并把目标等输入复制到 `CenterBlackboard.skillInputData`；真正调用 `TryCastSkill` 的位置是
`CenterSkillState._DoCastSkill`。该方法在调用后会清除技能请求，目前没有观察到它根据
`TryCastSkill` 返回值决定是否保留请求。

除玩家主控路径外，已找到的直接请求生产者还包括：

- `CharacterAIComponent.TryCastNormalAttack`；
- `CharacterCastSkillBehavior._TryCastSkill`；
- `PlayerController.CastSkill`；
- 若干 AI `AttackState.OnUpdate`。

它们有的经请求缓存和状态机释放，有的会直接进入 `TryCastSkill`。因此后续必须按入口分类，
不能用玩家主控链代表所有技能释放。

## 开始阶段

`Skill.DoCast` 已确认包含以下职责：

- 重置持续和冷却相关计时器；
- 添加技能期间标签；
- 保存目标并调用 `Ability.BeforeCast`；
- 重置 offset skill 身份；
- 按配置给自身附加 Buff；
- 记录技能开始时的 ATB 与回复状态；
- 写入战斗记录器。

资源实际扣除由 `_ApplyCost` 处理。当前已确认它并不由 `DoCast` 直接调用，而是在 `Skill.OnTick`
中于 `passedTime >= startCdTime - epsilon` 时调用。`_ApplyCost` 会再次执行 `CheckCost`，失败时
不扣费并保持 `m_appliedCost=false`。随后 `OnTick` 仍继续处理 exclusive 与 `Ability.OnTick`，
因此这次二次检查本身不是一个可证明会阻止 Timeline 推进的释放前门禁。

这意味着当前仍缺少一段关键证据：冷却、资源、标签和状态检查究竟在哪个生产者或间接调用
点阻止非法请求进入实际施放。不能因为 `_ApplyCost` 会复查资源，就假定技力不足的技能会在
该处被取消。

## Tick 与 exclusive

`Skill.OnTick` 会检查 `canInterrupt`，达到 exclusive 边界后执行 exclusive 回调；之后还会
推进 Ability/ActionGroup。`Skill.get_canInterrupt` 的一般时间条件为：

```text
passedTime > exclusiveFrame / 30 + epsilon
```

这说明配置的 `exclusiveFrame` 本身仍处于独占区间，越过边界后才由通用路径放行。与此同时，
`AllowedNextSkillPack` 可对特定下一技能提前放行，两条路径在
`AbilitySystem.CheckCanInterruptCurSkill` 汇合。

普通攻击下一段的身份由另一条机制决定：`ComboCacheAction` 在技能时间轴内临时向
`ComboController` 注入 Attack 命令映射，`AllowNextSkillAction` 只负责允许指定下一技能提前
接续。两个 Action 结束时会分别撤销映射 Handle 和 AllowedNextSkillPack。完整拆分见
[普攻连段、输入缓存与接续窗口](basic-attack-chain.md)。

## 结束和中断

`Skill.Interrupt` 会进入 `Skill.CastEnd`。`CastEnd` 的可达调用显示它至少负责：

- 结束 `TimelineActionProcessor`；
- 移除技能期间标签和根运动相关状态；
- 根据 `CastData.startCdTime` 与计时器状态处理冷却；
- 结束技能附带 Buff；
- 调用结束回调并写入战斗记录。

`AbilitySystem._OnCastEnd` 随后构造 `CastSkillContext` 并触发 AbilitySystem 事件。资源返还、
不同 FinishType 的冷却差异和事件触发条件尚未逐分支闭环。

## 形态与技能替换

`AbilitySystem._ApplyModeChange` 是当前已找到的核心入口。它不是单纯修改模式字段，而会：

- 清理 ComboController offset modifier；
- 移除旧 modifier；
- 查询额外被动技能；
- 查找、禁用和启用 Skill 实例；
- 通过 `ComboController.AddMappingModifier` 替换命令到技能的映射；
- 更新移动、旋转、模型、动画、武器和挂点；
- 根据参数决定是否中断当前技能；
- 发送模式变化相关事件。

这为“强化技能/形态技能不是在释放时临时改名，而可能通过运行时映射切换实际 Skill 实例”
提供了结构证据。仍需继续还原 `ModeData` 的字段、mapping modifier 优先级和移除语义。

除 Mode 整体切换外，`ComboController.ChangeSkillMapping` 提供按技能槽替换的第二条通用路径：

1. 先撤销同一槽位已有的 `ChangeSkillHandle`；
2. 可读取原技能冷却进度；
3. 启用目标 Skill，并可调整其运行时技能类型；
4. 添加带优先级的 `MappingModifier`；
5. 按配置将旧技能冷却进度写入新技能；
6. 创建记录当前技能、恢复技能、持续时间和槽位的 `ChangeSkillHandle`；
7. Handle 在 Tick 到期后恢复映射。

因此特殊技能替换至少要区分：

- ModeData 驱动的整套状态与映射切换；
- `ChangeSkillAction` 驱动的单槽临时替换；
- AllowedNext 只改变当前技能能否衔接，并不替换技能身份；
- offset mapping 是受特定动作触发的短期输入偏移，也不等同于形态技能。

这四者在 Endaxis 中可能呈现为相似的“技能变体”，但游戏运行时语义不同。

## 当前未决问题

1. `IsAvailable` 的完整调用者和不同释放入口是否都使用同一套检查；
2. 玩家命令在进入 `RefreshNextSkillRequest` 前，是否通过方法指针、IFix 或命令层间接执行可用性检查；
3. 普攻链在命中、移动、切人、受击和超时条件下如何重置；
4. MappingModifier 的优先级和同优先级选择规则；
5. 技能实例在 Mode/Patch 更新时是启停、刷新还是重建；
6. FinishType/InterruptReason 对冷却、成本、Buff 和事件的逐分支影响。

## 可复现分析

```powershell
python -m tools.analyze_runtime_snapshot `
  <IL2CPP_GameAssembly.runtime.bin> `
  <IL2CPP_GameAssembly.runtime.json> `
  data/research-artifacts/combat-1.4.4/derived/indexes/gameplay-types-ai.json `
  --match 'Beyond\.Gameplay\.Core\.(Skill|AbilitySystem|AbilitySystem\.ComboController)::.*(TryCastSkill|CanCastSkill|DoCast|CastEnd|Interrupt|OnTick|_ApplyModeChange)' `
  --output data/research-artifacts/combat-1.4.4/derived/runtime-snapshot-skill-state-machine.json
```
