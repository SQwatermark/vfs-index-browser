# AbilitySystem 事件分发与动作优先级

本文记录客户端 1.4.4 中 `AbilitySystem.TriggerEvent` 的未打 Patch 路径。这里的事件包括技能、伤害、
Buff、投射物、失衡、资源变化等跨系统通知，是技能配置中的 `abilityEventAction` 被执行的公共入口。

## 总体顺序

调用 `owner.TriggerEvent(eventType, context)` 后，运行时按以下主干同步执行：

```text
context.eventType = eventType

owner.m_eventDispatcher.Dispatch(eventType, context)

target = DoesEventHaveTarget(eventType)
  ? context.eventSource
  : owner

owner.m_actionContainer.ExecuteInstant(eventType, target, context)

for skill in owner.m_skills:
  skill.OnAbilitySystemEvent(eventType)

BattleManager.TriggerComboSkillEvent(eventType, target, context, ...)
```

因此同一个 AbilitySystem 事件至少有四类消费者，且顺序固定为：

1. 代码注册到通用 `EventDispatcher` 的回调；
2. Buff、技能或其他 `IActionEnvironment` 注册的数据驱动 `SequenceAction`；
3. AbilitySystem 持有的每个 `Skill` 实例（入口签名只直接接收事件枚举值）；
4. `BattleManager` 的连携技事件桥接。

这些调用都发生在同一个 `TriggerEvent` 调用栈中，并非排到下一逻辑帧。某层触发的新事件可以同步嵌套
进入另一轮 `TriggerEvent`；当前尚未发现覆盖整个事件系统的通用递归深度限制。

需要特别区分“调用层存在”与“技能层行为已知”。`Skill.OnAbilitySystemEvent` 和
`Skill.NeedTriggerAbilityEvent` 的原生方法体都是 IFix 桩：未安装补丁时，前者直接返回，后者固定返回
`false`；安装补丁时才转入动态方法。因此当前快照能证明 TriggerEvent 会逐个调用技能入口，却不能说明
1.4.4 实际补丁中的事件筛选和技能响应内容。这部分必须读取 IFix 补丁或通过运行时追踪补齐。

## 事件目标

`EventContext` 至少保存：

- `eventSource`：事件来源 AbilitySystem；
- `eventType`：由 `TriggerEvent` 在分发前写入；
- `instanceUid`：事件上下文实例身份。

`DoesEventHaveTarget(eventType)` 决定数据动作收到的 `TargetHandle`。需要目标的事件使用
`context.eventSource`，其他事件使用触发事件的 owner 自身。生成器不能笼统地把事件来源、事件持有者和
动作目标合并成同一个实体。

## ActionContainer

`ActionContainer` 维护两类集合：

- `m_actions`：全部已注册 `SequenceAction`，用于统一 Tick、移除和释放；
- `m_eventActionMap`：从 `AbilitySystem.Event` 到
  `DoubleBufferedPriorityQueue<SequenceAction>` 的映射。

注册动作时会先 `SequenceAction.Reset`，加入总集合，再加入对应事件的优先队列。首次注册某事件时才
创建该队列。注销按 `IActionEnvironment` 或动作实例进行，而非仅按事件枚举值清空。

事件执行时，容器取得对应队列并依次处理有效的 `SequenceAction`。每个序列执行前，会把当前
`EventContext` 压入其内部每个 AbilityAction 的 `IActionEnvironment.Context`；序列返回后再逐项弹出：

```text
for sequence in eventQueue:
  if !sequence.isValid:
    continue

  for action in sequence.actions:
    action.context.PushEventContext(eventContext)

  sequence.Execute(targetHandle, resetAfterExecute = true)

  for action in sequence.actions:
    action.context.PopEventContext()
```

这使条件、Blackboard 取值和具体动作能读取当前事件携带的数据，同时避免事件上下文残留到下一次执行。
`SequenceAction.Execute` 仍可启动需要后续 Tick 的动作；`ActionContainer.OnTick` 负责推进总集合中的运行
动作。因此方法名 `ExecuteInstant` 表示“立即发起这一批事件动作”，不保证其中所有子动作都在该调用栈
内完成。

## 优先级

事件队列使用 `SequenceAction.CompareTo` 排序。其未打 Patch 的比较规则只有一个键：

```text
this.priority > other.priority  => this 排在前面
this.priority < other.priority  => this 排在后面
priority 相等                  => CompareTo 返回 0
```

目前还不能从比较器单独证明相同 priority 的稳定顺序。`DoubleBufferedPriorityQueue` 这一容器选择说明注册
或注销与当前消费批次之间存在隔离意图，但其泛型实现未出现在现有类型索引中；在取得实现或运行时实验
前，不把“事件执行期间新注册动作何时可见”写成已确认事实。

## 对战斗解释器的约束

1. 事件不是单一回调表，必须保留四层消费者的先后关系。
2. `abilityEventAction` 应进入按 priority 排序的动作队列，不能按配置文件扫描顺序直接执行。
3. 条件求值必须能访问当前 `EventContext`，不能只读取角色和 Buff 的常驻 Blackboard。
4. 技能实例监听发生在数据动作之后；数据动作造成的同步状态变化可能被后续技能监听读取。
5. 连携触发桥接位于本轮 AbilitySystem 本地消费者之后。

## 尚未闭环

1. `DoubleBufferedPriorityQueue` 的同优先级稳定性和写缓冲切换时点；
2. `m_eventDispatcher` 内多个代码回调的注册顺序与删除期间行为；
3. IFix 中 `Skill.OnAbilitySystemEvent` 与 `NeedTriggerAbilityEvent` 的实际实现，以及是否会间接读取当前 context；
4. `BattleManager.TriggerComboSkillEvent` 内部的筛选、排队和同帧顺序；
5. 事件递归时是否存在仅在特定 EventContext 或 Action 类型中的局部保护。

## 运行时证据

| 方法 | RVA |
| --- | ---: |
| `AbilitySystem.TriggerEvent` | `0x030B5150` |
| `ActionContainer.RegisterAction` | `0x03C72C00` |
| `ActionContainer.ExecuteInstant` | `0x03C72D20` |
| `ActionContainer.ContainsEvent` | `0x03C734D0` |
| `SequenceAction.CompareTo` | `0x0389EBD0` |
| `Skill.OnAbilitySystemEvent` | `0x03EBC0D0` |
| `Skill.NeedTriggerAbilityEvent` | `0x03DC8230` |

可重建报告位于忽略目录：

- `runtime-snapshot-ability-events.json`；
- `runtime-snapshot-sequence-priority.json`；
- `runtime-snapshot-skill-ability-events.json`。
