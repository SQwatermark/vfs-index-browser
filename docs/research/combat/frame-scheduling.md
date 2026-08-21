# 战斗帧调度与同帧顺序

## 研究目的

本文记录终末地 1.4.4 客户端中与技能推进、延迟施放请求和同帧顺序直接相关的原生证据。
目标是区分三种不同顺序：单个 `AbilitySystem` 内部顺序、技能列表顺序，以及多个组件在全局
Tick 调度器中的顺序。三者不能用一个笼统的“注册顺序”代替。

## AbilitySystem 所在阶段

`AbilitySystem` 继承 `TickComponent`，并覆写 `preLateTickGroup` 返回数值 `1`；基类默认返回
`0`。`TickComponent._CreateTickFunctions` 会根据 `m_tickOption` 为各 Tick 阶段创建独立的
`TickFunction`，其中 PreLateTick 函数保存到偏移 `0x130`。

共享技力自然恢复不在该阶段执行。`BattleManager.frameTickGroup` 返回数值 `7`
（`FrameTickGroup.BattleManager`），`PlayerController.frameTickGroup` 返回数值 `8`
（`FrameTickGroup.PlayerController`）；`BattleManager.Tick`（RVA `0x03A5C750`）先调用
`_TickSquadInFight`，随后在调用点 `0x03A5C79A` 直接调用 `_UpdateAtb`。因此一帧内已确认的
跨阶段顺序为：

```text
BattleManager Frame Tick: _UpdateAtb
  -> PlayerController Frame Tick: 玩家输入
  -> AbilitySystem PreLate Tick: Buff -> skills -> deferred cast -> actions
```

这意味着自然恢复在本帧技能费用边界之前生效。若恢复后刚好达到技能费用，随后到达费用帧的
`_ApplyCost` 可以使用这部分技力；技能扣费设置的恢复暂停则从之后的 BattleManager Tick 开始生效。

`TickComponent._StartTickFunctions` 随后分别遍历组件 Tick 函数列表和额外 Tick 函数列表，
调用每个 `TickFunction.Start()`。真正的全局执行容器是 `TickRoot -> TickGroup`：

- `TickRoot` 按组保存 `TickGroup`；
- `TickGroup` 维护 `m_tickFunctions` 和 `m_pendingAddFuncs` 两个列表；
- `TickFunction` 持有 `TickOwnerInfo`、组、类别、独占标签和回调；
- `AbilitySystem.ResetTickOwnerInfo` 在基类重置后，将
  `TickOwnerInfo.m_useUnscaledDeltaTime` 设为真。

完整运行时转储进一步确认了调度容器的顺序规则：

1. `TickRoot.DoTick` 按 `m_groups` 的列表索引递增遍历 TickGroup；
2. `TickGroup.StartTickFunction` 不直接修改活动列表，而是把尚未工作的函数追加到
   `m_pendingAddFuncs`，并将状态改为 Pending；
3. `TickGroup.Tick` 在本组 Tick 开始时先执行 `_FlushPendingAddFuncs`；
4. `_FlushPendingAddFuncs` 按 pending 列表索引递增处理，将普通函数追加到
   `m_tickFunctions`，或追加到对应独占队列，然后清空 pending 列表；
5. `_TickFunctions` 固定读取进入循环时的活动列表数量，按索引递增调用 `TickFunction.Tick`；
6. 本组普通与独占函数处理完毕后，再执行一次 `_FlushPendingAddFuncs`。

因此，同一个 TickGroup 内确实保持 TickFunction 的启动顺序。若某函数在本组 Tick 执行过程中
才启动，它会在组末刷新时进入活动列表，但不会回头参与已经完成的本轮遍历，只会从下一帧
开始执行。被释放或停止的函数则会在正向遍历中移除，并同步修正当前索引和本轮剩余数量。

### TickFunction 注册来自组件启动

`Entity.TryStart -> ComponentContainer.TryStart -> _StartAllComponents` 是实体启动组件的主链。
`_StartAllComponents` 不直接按 `m_components` 的添加顺序启动，而是依次读取
`m_startCompIndexes`。该索引表由 `_InitComponentOrders` 生成：

1. 遍历 `m_components`，记录组件原始索引；
2. 通过 `ECClassCollection.GetStartPriority(componentType)` 取得启动优先级；
3. 按优先级数值升序排序；
4. 把排序后的原始索引写入 `m_startCompIndexes`；
5. `_StartAllComponents` 先按同一索引表完成 `PreStart()`，再按表调用 `_StartComponent`；
6. `_StartComponent` 调用 `BaseComponent.Start()`，其中虚调用组件的 `InternalStart()`；
7. `TickComponent.InternalStart()` 在基类逻辑后调用 `_StartTickFunctions()`，从这里把函数追加到
   TickGroup 的 pending 列表。

`ECClassCollection` 的启动优先级不是手写常量表。它收集 `StartDependencyAttribute`，通过
`_TopologicalSort` 和 `_CalculatePriorities` 建立 `s_startPriorities`。因此同一实体内部的
TickComponent 注册顺序，本质上来自组件启动依赖图的拓扑优先级。

### 多个实体之间的启动顺序

实体启动还会经过 `EntityGroupProcessor`，并非所有 Entity 都从一个全局列表同时启动：

- 无依赖分组的节点可由 `_TryStartNode -> EntityNode.StartEntity -> Entity.TryStart` 直接启动；
- ScriptEntityGroup 把节点放在 `m_waitingStartNodes`，`StartWaitingEntities` 按列表枚举顺序调用
  `Entity.TryStart`；
- DependencyEntityGroup 使用 `m_spawnedEntityNodes`，满足组条件后按列表枚举顺序启动节点；
- 等待世界启动的节点另存于 `m_waitForWorldStartNodes`。

这些容器目前看到的都是保序 List，故同一路径内的节点插入顺序会继续影响 TickFunction 的
pending 顺序。尚未闭环的是玩家队伍的四个角色分别经过哪条启动路径，以及其插入顺序是否
稳定等于队伍序号。当前模拟器“按装配层显式注册顺序更新”与原生容器语义一致，但不能把该
顺序直接宣称为队伍序号或实体 ID 顺序。

## 单个 AbilitySystem 内部顺序

`AbilitySystem.PreLateTick(float)` 当前已恢复的主干顺序为：

1. 推进 Buff 容器；
2. 按 `m_skills` 的列表索引顺序遍历技能；
3. 对每个启用技能更新冷却；
4. 若技能正在施放，进入技能 Tick，在成本边界执行 `_ApplyCost`，随后继续 Ability/Action；
5. 技能循环结束后处理一个 `m_postSkillTryCastRequest`；
6. 在更后面推进并清理 `ActionContainer`。

技能 Tick 前会先推进独立的 `m_durationTimer`。计时器在本帧到期时不会跳过本帧逻辑：成本、
独占回调及 `Ability.OnTick` 完成后，`PreLateTick` 才调用 `CastEnd(Completed, Default)`。
该边界来自 `durationFrame`；`exclusiveFrame` 是另一条可打断边界，二者不可互换。

这说明同一个角色的多个技能在同帧到达成本边界时，资源竞争顺序由 `m_skills` 的顺序决定，
而不是由技能块在编辑器中的创建顺序决定。

## m_skills 的构造顺序

`AbilitySystem._InitSkills()` 按以下顺序读取 `SkillDataBundle`：

1. `allNormalAttackId`，保持配置列表索引顺序；
2. `allActiveSkillId`，保持配置列表索引顺序；
3. `allPassiveSkillId`，保持配置列表索引顺序；
4. 最后创建通用技能。

`AbilitySystem._CreateSkill` 调用 `Skill.Create(...)` 后，以 `m_skills`（偏移 `0x258`）作为
容器、以新建 Skill 作为元素调用列表添加入口；若 SkillData 允许进入 active map，再向
`m_activeSkillMap`（偏移 `0x260`）写入映射。由此可确认常规初始化阶段的技能 Tick 顺序来自
配置列表分类与索引，而不是字典枚举。

`m_pendingInitSkills`（偏移 `0x500`）只负责分帧初始化，不能与最终 Tick 使用的 `m_skills`
混为一谈。

## Action 中发起的延迟施放

`TryCastSkillDuringAction(...)` 不会立刻施放技能。它把请求写入
`m_postSkillTryCastRequest`（偏移 `0x1B0`），请求中包含：

| 偏移 | 字段 |
| --- | --- |
| `0x10` | `skillId` |
| `0x18` | `targetHandle` |
| `0x20` | `skipApplyCost` |
| `0x28` | `skillCastInfo` |

在当帧完整技能循环结束后，`PreLateTick` 会：

1. 复制该请求；
2. 立即清空原字段；
3. 若当前技能存在，以中断原因数值 `7` 中断当前技能；
4. 走 `PlayerController.CastSkill` 或 `AbilitySystem.TryCastSkill`；
5. 释放复制出的目标句柄。

该字段是单个 `Nullable<PostSkillTryCastRequest>`，不是队列。已看到的写入路径会覆盖字段，
所以同一次排空前若发生多次写入，最后一次写入会成为待处理请求。是否存在更上游避免重复写入
的约束仍待确认。

## 对新版模拟器的约束

- 技能 Tick 顺序应由角色技能运行时列表显式定义，不能依赖映射表或对象哈希顺序。
- Action 中请求施放技能应进入“技能循环结束后”的单槽延迟区，而不是立即递归施放。
- 同帧多个延迟施放请求不能擅自建模成无界 FIFO 队列。
- 全局模拟器应使用显式、稳定、可测试的阶段表；同一阶段按装配层给出的实体注册顺序执行。
  在玩家实体启动路径闭环前，不能擅自把该顺序解释为队伍序号、实体 ID 或技能块创建顺序。
- 资源扣费应遵循每个技能成本帧的重新检查；扣费失败后的生命周期不能自行补成取消。

## 尚未闭环

1. 玩家四名角色进入 EntityGroupProcessor 的具体路径、节点插入顺序及其与队伍序号的关系；
2. `TickOwnerInfoGroupType`、独占 Tick 队列对战斗对象顺序的影响；
3. `m_postSkillTryCastRequest` 的所有写入者及是否存在覆盖保护；
4. IFix 对 Tick 调度和技能成本路径是否存在生效 Patch。

## 可复现证据

- `data/research-artifacts/combat-1.4.4/derived/runtime-snapshot-ability-prelate.json`
- `data/research-artifacts/combat-1.4.4/derived/runtime-snapshot-tick-registration.json`
- `data/research-artifacts/combat-1.4.4/derived/runtime-snapshot-battle-manager-tick-atb.json`
- `data/research-artifacts/combat-1.4.4/derived/runtime-snapshot-battle-player-frame-order.json`
- `data/research-artifacts/combat-1.4.4/derived/runtime-snapshot-skill-registration-order.json`
- `data/research-artifacts/combat-1.4.4/derived/runtime-snapshot-tick-scheduler.json`
- `data/research-artifacts/combat-1.4.4/derived/runtime-snapshot-component-start-order.json`
- `data/research-artifacts/combat-1.4.4/derived/runtime-snapshot-component-priorities.json`
- `data/research-artifacts/combat-1.4.4/derived/runtime-snapshot-entity-start-order.json`
- `data/research-artifacts/combat-1.4.4/dumps/normal/Common.Beyond.dll.cs`
- `data/research-artifacts/combat-1.4.4/dumps/normal/Gameplay.Beyond.dll.cs`

其中 Runtime Snapshot 证明具体控制流；C# dump 只用于确认类型、字段偏移和方法身份。
