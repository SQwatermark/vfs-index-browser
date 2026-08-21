# TimelineAction 执行模型

## 职责边界

技能动作系统可以分成三层：

1. `ActionGroupData` 保存一组时间轴动作和被动事件动作；
2. `TimelineActionProcessor` 根据技能经过时间启动、更新和结束 `TimelineAction`；
3. 每个 `TimelineAction` 内部的 `SequenceAction` 按配置顺序执行具体
   `AbilityAction`。

因此，Timeline 层主要回答“什么时候开始、持续到什么时候”；Sequence 层回答“这个
时间点内有哪些动作、按什么顺序执行”。

## 数据结构

`TimelineActionData` 包含：

| 字段 | 含义 |
| --- | --- |
| `_startFrame` | 编排逻辑中的起始帧 |
| `_endFrame` | 编排逻辑中的结束帧 |
| `_sequenceActionData` | 该时间区间内要执行的动作序列 |
| `forceSyncAnimData` | 时间跳转或动画偏移时的强制同步配置 |

运行时 `TimelineAction` 将其展开为：

- `startTime`、`endTime`；
- `startFrame`、`endFrame`；
- `action: SequenceAction`；
- `forceSyncAnimData`。

`TimelineActionProcessor` 维护：

- `m_rawTimelineActions`：当前技能的时间轴动作；
- `m_inputTarget`：释放时保存的输入目标；
- `m_passedTime`：技能已经经过的时间；
- `curFrame`：当前逻辑帧；
- `isCasting`：时间轴是否正在运行；
- 跳转相关状态与待结束动作列表。

逻辑帧会在加载时转换为 `startTime/endTime`。运行时调度以 `passedTime` 为准，而不是
要求逻辑帧与实际渲染帧一一对应。

## 生命周期

### 加载

`Ability.Init` 接收 `ActionGroupData`，通过 `_LoadActions` 将
`timelineActions` 交给 `TimelineActionProcessor.Load`。每个
`TimelineActionData` 会生成一个运行时 `TimelineAction`，其
`SequenceActionData` 同时展开为具体动作实例。

被动事件动作不进入这条时间轴；它们按 `AbilitySystem.Event` 注册，并在对应事件发生时
执行。

### 开始释放

`Ability.BeforeCast`/技能释放主干把目标传给 `TimelineActionProcessor.CastStart`：

- 保存本次输入目标；
- 重置经过时间和时间轴动作状态；
- 标记进入 casting；
- 处理起始时间已经到达的动作。

### 每次更新

`Skill.OnTick` 将技能经过时间与本次 `deltaTime` 传到 `Ability.OnTick`，随后进入
`TimelineActionProcessor.OnTick` 和 `_TickInternal`。

处理器会顺序扫描时间轴动作。对每个仍需检查的动作，主干行为可以表示为：

```text
if action has not started and passedTime reaches startTime:
    execute its SequenceAction

if action is running:
    tick its SequenceAction with deltaTime

if passedTime reaches endTime:
    end its SequenceAction
```

实际比较带有很小的时间容差，用于避免浮点边界漏触发。

如果一次游戏更新跨过多个逻辑帧，已经到点的动作会在该次更新中被补处理。因此逻辑帧
描述的是技能时间位置，不是“必须等待一个新的显示帧”。

### 结束与跳转

正常释放结束、取消或打断时，处理器结束尚未完成的动作并清理状态。

`JumpTo(passedTime)` 用于将技能时间轴跳到另一个位置。处理器单独记录
`m_jumpedInThisFrame`、剩余跳转时间和下一帧待结束动作，说明跳转不是简单覆盖
`m_passedTime`；它还需要处理被跨过的起止边界。该分支的完整规则仍需单独恢复。

`forceSyncAnimData` 用于时间轴位置和动画播放位置需要强制对齐的情况。它不是普通伤害、
Buff 顺序的决定因素。

## 瞬时动作与持续动作

当 `_startFrame == _endFrame` 时，`TimelineAction` 是一个瞬时节点。若内部动作都是即时
动作，它们会在处理器命中该时间点的同一次更新中串行执行并结束。

当 `_endFrame > _startFrame` 时，时间轴节点具有生命周期：

- 起点调用执行逻辑；
- 区间内调用 `SequenceAction.OnTick`；
- 终点调用 `SequenceAction.End`。

这类区间常用于特效、霸体、根运动、允许后续技能、武器显示和需要随动作结束而撤销的
Buff。起止帧不能简单理解成“延迟到结束帧才执行”。

## 佩丽卡普通战技示例

本地样本的关键时间轴包括：

| 起止帧 | 动作 |
| --- | --- |
| `0-155` | 播放动画、根运动、武器显示等 |
| `11-12` | 转换目标上下文 |
| `13-13` | 找目标、打断、附着、伤害、受击动画、镜头冲击、创建 Buff |
| `0-30` | 霸体 |
| `28-54` | 允许后续技能 |

当技能经过时间到达逻辑帧 13 时，`13-13` 节点在一次更新中启动。其内部
`SequenceAction` 依次执行：

```text
FindTarget
→ Interrupt
→ SpellInfliction
→ Damage
→ EnemyHurtAnim
→ CameraImpulse
→ CreateBuff
```

这些动作通常发生在同一次游戏更新中，但不是并行发生；每一步的状态变化都可以被后续
步骤观察到。

## 同一时间点的边界

同一个 `TimelineAction` 内部的动作顺序已经确认由 `SequenceAction` 数组决定。

多个独立 `TimelineAction` 如果具有相同 `startFrame`，处理器也会在同一次更新中逐个
处理，而不是并行运行。不过 `TimelineActionProcessor.Load` 是否对输入列表做额外整理、
相同起始帧的最终稳定排序规则，以及时间跳转时的同帧行为，当前尚未完成运行时取证。
在需要判断跨 `TimelineAction` 的同帧 Buff 与伤害先后时，不能仅凭 JSON 数组位置直接
下最终结论。

## 运行时证据

| 方法 | Token / RVA | 已确认行为 |
| --- | --- | --- |
| `TimelineActionProcessor._TickInternal` | `0x0600D2BE` / `0x033BCC10` | 扫描动作、比较起止时间、执行、Tick 和 End |
| `SequenceAction.OnTick` | RVA `0x033BC7C0` | 顺序更新正在执行的子动作 |
| `SequenceAction.End` | RVA `0x035EE2F0` | 时间区间结束时终止动作序列 |
| `TimelineAction.needCheck` | RVA `0x033BD6A0` | 已结束的动作不再进入时间检查 |
| `SequenceAction.Execute` | `0x0600D7D7` / `0x033BD9C0` | 同一节点内部按数组顺序执行动作 |

`_TickInternal` 的代码来自已捕获运行时窗口中的邻接方法，并通过独立入口、返回点和类型
符号重新执行可达控制流分析，不是把相邻字节线性误认为同一函数。
