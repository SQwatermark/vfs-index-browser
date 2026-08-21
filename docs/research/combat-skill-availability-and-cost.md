# 技能可用性与资源扣费调用链

## 研究范围

本文记录终末地 1.4.4 客户端中 `Skill.IsAvailable`、资源检查和实际扣费的
运行时证据。结论来自注入式 IL2CPP Dumper 捕获的进程内代码，而不是磁盘
`GameAssembly.dll` 的静态字节。

## 可用性检查顺序

`Skill.IsAvailable` 在确认技能拥有有效 owner，并通过一个尚未命名的 owner
级布尔门槛后，按以下顺序短路检查：

1. 冷却；
2. 资源；
3. 标签；
4. 实体状态。

运行时调用目标与方法本体的对应关系如下：

| 顺序 | `IsAvailable` 调用目标 | 对应检查 | 识别依据 |
| --- | --- | --- | --- |
| 1 | `0x03045040` | `CheckCd` | 读取 `cooldownTimer` 并调用 `MultiPeriodicTimer.oneReady`；冷路径继续处理计时器比较 |
| 2 | `0x02F56E70` | `CheckCost` | 读取 `BattleManager.atb`、`AbilitySystem.ultimateSp` 和 `CostData`；`_ApplyCost` 也调用同一入口 |
| 3 | `0x03046350` | `CheckTag` | 调用目标与元数据方法 RVA 直接一致 |
| 4 | `0x02F536B0` | `CheckState` | 字段读取和分支与 `CheckState` 本体一致，包括侵蚀、滞空和移动状态检查 |

前三个运行时包装入口中有 IFix 检查、远端冷代码块或间接跳转，因此不能仅按
连续函数边界反汇编。上表依据实际访问字段和被调用方法建立，不依赖调用顺序猜测。

## 资源检查

`CastData.CostData` 的结构为：

| 偏移 | 字段 | 含义 |
| --- | --- | --- |
| `0x10` | `costType` | `0 = UltimateSp`，`1 = Atb` |
| `0x14` | `costValue` | USP 或 ATB 的基础消耗值 |
| `0x18` | `atbValueThreshold` | 释放前必须达到的 ATB 门槛 |

省略空指针异常路径和浮点容差后，`CheckCost` 可表示为：

```text
if costData is absent:
    return true

if not hasReachedAtbThreshold:
    if BattleManager.atb < costData.atbValueThreshold:
        return false

if costType == UltimateSp:
    return owner.ultimateSp >= costData.costValue

if costType == Atb:
    return BattleManager.atb >= GetAtbCost(costData)

return true
```

实际比较会在需求值一侧减去一个很小的常量，用于避免浮点边界误判。
`hasReachedAtbThreshold` 为真时只跳过 ATB 门槛检查，不会跳过最终 USP/ATB
余额检查。

## 实际扣费

`Skill._ApplyCost(asSkillCast)` 的主干顺序为：

1. 若 `m_appliedCost` 已经为真，直接退出，避免重复扣费；
2. 再次调用资源检查入口 `0x02F56E70`；检查失败则退出；
3. 读取当前 `CostData`；
4. `UltimateSp` 路径通过 `Modifier.NewUltimateSp` 创建负值修改并调用 `Apply()`；
5. `Atb` 路径先调用 `_GetAtbCost`，再调用 `BattleManager.CostAtb`；
6. 标记 `m_appliedCost = true`，并清除 `m_hasReachedAtbThreshold`；
7. 继续记录扣费结果并触发技能相关事件。

ATB 扣费调用会返回 `nonReturnedAtbCost`，并将其写入技能所属 `Ability`。该值不是
“中断时将要返还的成本”，而是本次成本中没有被返还技力覆盖的部分。

### 返还技力与不可返还成本

`BattleManager` 同时维护总 ATB `m_atb` 和返还来源子集 `m_returnedAtb`。省略浮点
容差、统计和回复暂停后，`CostAtb(cost)` 的算术为：

```text
if currentAtb < cost:
    return false

m_atb = currentAtb - cost
consumedReturned = min(m_returnedAtb, cost)
m_returnedAtb = m_returnedAtb - consumedReturned
nonReturnedAtbCost = cost - consumedReturned
return true
```

因此，扣费会优先消耗返还来源子集，只有剩余部分进入
`nonReturnedAtbCost`。普通战技成本转化为全队终结技能量时使用的正是这个剩余值，
从而避免同一份被返还的技力再次产生终结技能量。

`GainAtb(value, atbSource, atbGainMethod, ...)` 先按来源和获得方式计算效率，经 ATB
上限约束后得到实际增量。精确枚举值为：

- `GainAtbMethod.Gain = 0`：只增加总 ATB；
- `GainAtbMethod.Return = 1`：总 ATB 增加后，再把实际增量加入 `m_returnedAtb`；
- `GainAtbType.Default/NormalAttack/PowerAttack/Skill = 0/1/2/3`。

全代码段对 `GainAtb` 的 `call rel32` 交叉引用只有三个未打补丁调用者：

1. `ObtainCostAction.ExecuteInternal`：从动作数据读取 `atbSourceType` 和
   `atbGainMethod`；
2. `GainBreakingAttackAtb.ExecuteInternal`：以 `PowerAttack + Gain` 增加技力；
3. `GainCostAction.ExecuteInternal`：以普通 `Gain` 增加技力。

`Skill.CastEnd`、`Skill.Interrupt` 与 `Ability.CastEnd` 均不直接调用 `GainAtb`。
`Interrupt` 只是以 `FinishType.Interrupted` 进入 `Skill.CastEnd`，而
`Ability.CastEnd` 负责结束 Timeline 与 Action。因此，通用技能中断不会隐式返还
ATB；返还来自显式配置 `atbGainMethod: Return` 的动作。当前已获取的 Buff 样本中，
莱万汀战技返还与黎祈衍连携相关动作属于这种配置。

这项结论覆盖当前未启用 IFix 补丁的原生主干。若目标版本对上述入口启用了热补丁，仍需
单独分析补丁实现。

### 同帧共享资源竞争

`CheckCost` 与随后实际扣费都在同一次 `_ApplyCost` 同步调用中完成。ATB 分支在复查成功后
只计算本次成本，然后直接调用 `BattleManager.CostAtb`；这段控制流中没有事件分发、动作
调度或其他可让第二个技能插入执行的边界。因此，不能把“复查与扣费之间被其他行为抢走
资源”当作当前已确认的竞态。

真正存在的边界发生在多个技能分别进入 `_ApplyCost` 时。`AbilitySystem.PreLateTick` 会按
`m_skills` 列表顺序逐个推进正在施放的技能；前一个技能可能先消耗共享 ATB，后一个技能随即
在自己的 `_ApplyCost` 中重新执行 `CheckCost`。若余额不足，后一个技能只会保持
`m_appliedCost = false` 并返回，当前函数不会主动结束该技能。

这给模拟器带来两个要求：

1. 实际扣费必须在成本帧重新检查资源，不能只相信施放请求进入时的检查结果；
2. 在释放前门禁调用者尚未闭环前，不能自行规定“成本帧扣费失败必然取消技能”，因为原生
   `_ApplyCost` 没有这样做。

ATB 路径中的 `BattleManager.CostAtb` 返回 `bool`，但 `_ApplyCost` 当前原生主干没有读取该
返回值，而是使用其 `out nonReturnedAtbCost` 并继续设置扣费状态。结合紧邻的 `CheckCost`，
这更支持“检查与扣费按单线程同步段处理”，但 `CostAtb` 返回值的其他调用者语义仍需单独恢复。

## 扣费时点与释放门禁

`_ApplyCost` 不由 `Skill.DoCast` 直接调用。`Skill.OnTick` 会读取技能已经经过的时间和
`CastData.startCdTime`，在满足以下边界时调用 `_ApplyCost(asSkillCast=true)`：

```text
passedTime >= startCdTime - epsilon
```

调用后，`OnTick` 继续处理可中断边界、exclusive 回调与 `Ability.OnTick`。当 `_ApplyCost`
内部的 `CheckCost` 失败时，它只是不扣费并保持 `m_appliedCost=false`；当前控制流中没有看到
它直接结束技能或阻止本 Tick 的 `Ability.OnTick`。

因此必须区分：

- `IsAvailable/CheckCost`：候选的释放前合法性检查；
- `_ApplyCost/CheckCost`：到配置时点执行的防重复实际扣费；
- `CanCastSkill`：当前已确认只覆盖技能解析、衔接与目标距离/角度，不等同于完整可用性检查；
- `TryCastSkill`：执行施放生命周期，不应被假定为内部必然调用 `IsAvailable`。

目前全模块未发现对 `Skill.IsAvailable` 的直接 `call rel32`。对主控请求、`CanCastSkill`、
`TryCastSkill` 和 `RefreshNextSkillRequest` 的完整可达控制流补查后，也未发现未记录的间接
`call`。释放前门禁可能位于更上游的输入/UI 系统、IFix 路径或尚未定位的方法指针调用中，
需要运行时调用跟踪或构造“资源不足仍发出命令”的实验才能最终确认。

在本轮虚拟 RVA 快照中，`IsAvailable` 的运行时入口为 `0x06CA7760`；其绝对地址只在
`0x0EA4F928` 的非执行方法表区域出现一次。该事实支持“存在方法指针注册”，但单凭方法表
槽位仍无法还原最终间接调用者。后续追踪应以这个入口设断点或注入日志，而不是继续把
`CanCastSkill` 当作完整可用性入口。

## 当前边界

已确认的是检查顺序、字段来源和主要扣费接口。以下内容尚未完成：

- `IsAvailable` 最前面的 owner 级布尔门槛具体对应哪个标签或状态；
- 不同施放入口在何处执行真正的释放前可用性门禁；
- `_GetAtbCost` 对基础 ATB 成本施加了哪些修正；
- `GainAtb` 的各来源效率、活动修正和表现事件分支；
- `CostAtb` 的回复暂停和战斗统计分支；
- 多个 `AbilitySystem` 的 TickFunction 注册先后由哪一层决定；同组容器已确认保持注册顺序；
- 各干员显式返还动作的触发条件、数值来源和时点；
- IFix 热补丁生效时是否会替换上述方法的局部实现。

## 可复现证据

忽略区中的原始报告：

- `data/research-artifacts/combat-1.4.4/IL2CPP_MethodProbes.raw-rvas.json`
- `data/research-artifacts/combat-1.4.4/derived/runtime-method-analysis.raw-rvas.json`
- `data/research-artifacts/combat-1.4.4/derived/runtime-snapshot-skill-finish-cost.json`
- `data/research-artifacts/combat-1.4.4/derived/runtime-snapshot-gain-breaking-atb.json`
- `combat-runtime-dumps/1.4.4/runtime-1/atb-gain-chain.analysis.json`

可使用 `tools/analyze_runtime_method_probes.py` 和同版本
`gameplay-types-ai.json` 重新生成控制流与符号化直接调用报告。
