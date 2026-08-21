# 战斗系统反推状态

## 当前阶段

主线已经完成“固定证据集、类型地图、配置到运行时桥接、技能可用性与扣费主干”，并已
开始恢复技能状态机、Hit、Buff 与伤害执行管线。注入式 Dumper 已取得完整的虚拟 RVA
运行时模块快照，已知方法可以离线提取和分析；当前重点是先还原通用解释器，再讨论任何
OperatorSheet 自动生成。

## 已确认

| 结论 | 证据 | 状态 |
| --- | --- | --- |
| 二进制 JsonData 可直接反序列化为同版本 `SkillData` | Wulfa 与佩丽卡本地 VFS 样本完整消费；47 个实例字段与 47 个顶层键对应 | 已确认 |
| ATB 是队伍共享资源 | `BattleManager.m_atb`、`CostAtb`、`GainAtb` 和回复状态 | 已确认结构，时序待验证 |
| USP 属于单个战斗实体 | `AbilitySystem.m_ultimateSp`、上限、变化事件和回能许可标签 | 已确认结构，公式待验证 |
| `CostType 0 = UltimateSp`、`1 = Atb` | Common 枚举、佩丽卡三技能本地解码和 AKEDB 枚举名交叉验证 | 已确认 |
| 连携冷却与窗口队列分离 | 连携 `CastData.cooldownTime`；`BattleManager` 独立维护 pending/event/pause 容器 | 已确认结构，排序待验证 |
| 技能实例可以运行时刷新 | `RefreshRuntimeData`、`SkillPatchData` 及等级/天赋/技能替换事件 | 已确认能力，刷新顺序待验证 |
| dump RVA 不是可靠磁盘函数入口 | 当前 PE 无 `il2cpp_method_get_pointer` 导出；Dumper 回退读取 `MethodInfo` 首指针；部分 RVA 落在 `.rdata` | 已确认 |
| 运行时 RVA 对应真实原生函数 | 11 个方法的 `MethodInfo +0/+8` 指向相同地址，页面权限为 `EXECUTE_READ`，入口均可稳定反汇编 | 已确认 |
| 冷却检查读取实际计时器 | `CheckCd` 的可达控制流读取 `MultiPeriodicTimer.oneReady`、`maxPassedTime` 和 `CastData.startCdTime` | 已确认调用结构 |
| 资源检查同时覆盖 ATB 与 USP | `CheckCost` 先检查 `atbValueThreshold`，再按 `costType` 比较 USP 或修正后 ATB 成本 | 已确认 |
| 实际扣费分别进入资源修改接口 | `_ApplyCost` 调用 `NewUltimateSp().Apply()` 或 `BattleManager.CostAtb()`，随后触发技能事件 | 已确认主干调用 |
| `IsAvailable` 的四项检查顺序 | 函数体依次执行冷却、资源、标签和实体状态检查 | 已确认函数语义，调用者待确认 |
| 玩家技能请求的生产与消费链 | `OnExecutePlayerCommand -> RefreshNextSkillRequest -> ComboController.Tick/_AllowNextSkill -> CenterStateMachine._ToSkill -> CenterSkillState._DoCastSkill` | 已确认主控路径，完整可用性门禁待确认 |
| 技能请求许可只表示可衔接 | `_AllowNextSkill` 解析目标技能后只调用 `CheckCanInterruptCurSkill`；`nextSkillId` 只暴露已获准请求 | 已确认 |
| 普攻下一段由临时命令映射选择 | `ComboCacheAction` 注入 mapping；结束时逐个调用 Handle 撤销映射并按配置清理请求 | 已确认主干，重置条件待验证 |
| 提前接续许可具有独立生命周期 | `AllowNextSkillAction` 创建 AllowedNextSkillPack，结束时从 ComboController 移除 | 已确认 |
| 实际扣费发生在技能 Tick 中 | `Skill.OnTick` 在 `passedTime >= startCdTime - epsilon` 时调用 `_ApplyCost` | 已确认时点，释放前门禁待确认 |
| 同一动作序列按配置数组顺序执行 | `SequenceAction.Init` 保持输入顺序；`Execute` 从索引 0 同步执行并递增 | 已确认 |
| 显式 Buff 与伤害没有固定类型优先级 | 前置且立即生效的 Buff 可参与随后伤害；后置 Buff 只能影响后续伤害 | 已确认通用规则，具体 Buff 待扫描 |
| 伤害基础值支持逐单元快照 | `_TakeDamageCalculationSnapshot` 缓存攻击者身份，并仅为 `takeAtkSnapshot=true` 的 DamageUnit 缓存基础 `CalcResult.value` | 已确认；缓存下标约束待补 |
| Buff 初始化与生效是两个阶段 | `Reset` 加载运行时对象；`isEnabled -> OnStart/OnEnable -> _AddModifier` 才注册属性与伤害修正 | 已确认 |
| Buff Finish 动作先于 Modifier 移除 | `MarkFinish` 先 `OnFinish`，再处理子 Buff、stacking group 和 `_RemoveModifier` | 已确认，递归事件待验证 |
| Buff stacking 分为多实例、单实例增强和优先级启停三类 | `StackBuff` 的 12 项跳转表；Stack/Enhance/HighPriority 分支控制流 | 已确认通用分支，实际 BuffData 待交叉验证 |
| Buff 持续时间存在刷新、延长和覆盖三种原语 | `max(old.lifeTime, new.duration)`、相加、直接覆盖 | 已确认，特殊无限时长标记待补充 |
| Buff 优先级具有稳定的三级排序键 | priority 降序、lifeTime 降序、instanceUid 升序；动态 priority 可来自 Blackboard | 已确认 |
| 优先级失活不会暂停 Buff 时钟 | disabled 时仍更新 passedTime/lifeTime 和 TimelineActionProcessor；paused 才将 delta 置零 | 已确认 |
| Modify 保留 Buff 运行进度并合并 Blackboard | `_Modify` Assign 到旧 ActionBlackboard，随后重算旧 BuffData 的属性 Modifier | 已确认；其他动态消费者按执行时读取 Blackboard |
| Buff 生命周期动作可同步嵌套 | `_ExecuteBuffAction` 顺序同步执行且不逐项复查状态；`MarkFinish.isFinishing` 只防重复结束 | 已确认主干，跨 AbilitySystem 事件待展开 |
| 驱散先执行专用事件再统一结束 Buff | 先按可驱散开关、等级及可选 `applyTags` 查询筛选，再执行 `OnBuffDispelled`，最后以 `Dispelled` 原因调用 `MarkFinish` | 已确认；返回值不表示实际命中数量 |
| AbilitySystem 事件存在四层同步消费者 | EventDispatcher 回调、优先 SequenceAction、Skill 实例、BattleManager 连携桥接依次执行 | 已确认主序；同优先级和递归边界待验证 |
| 伤害计算与落血事件是两层管线 | `OnBeforeCalculateDamage`、前后 DamageProcessor、公式计算、Modifier 前后事件、受击前/输出前、护盾、生命变化、受击后/输出后的主序已定位 | 已确认普通生命伤害主干；特殊目标与 IFix 待补 |
| 伤害倍率采用配置化区间 | 攻防双方区间数组以 1 初始化；`ProdCalcZone` 逐项乘算，其余六区同侧逐项加算；攻防两侧及七个区间最终相乘 | 算法与实际 `allZones` 顺序、开关均已确认 |
| 格挡显示与实际减伤相互独立 | `DamageTextProcessor(Block)` 只写 `isBlocked`；本地 BuffData 的减伤另由 DamageScale 或属性倍率完成 | 已确认原生主线和 5 个配置样本；全量数据与 IFix 待补 |
| 防御与暴击主公式已恢复 | 防御系数读取 `BattleConst.efficiencyOfDEF = 0.01` 并使用正负分段曲线；暴击率按 `[0,1]` 与确定性随机流比较 | 防御公式已闭环；随机播种待补 |
| 伤害装饰位与专用属性映射已恢复 | 技能类型、四类法术爆发、四类异常及 `Burning/Shatter` 合并语义均由位测试和属性下标交叉确认 | 原生主线已确认；IFix 待补 |
| 现有证据清单误收了一份《明日方舟》metadata | 文件哈希匹配清单，但类型体系为 `Torappu.*`，与终末地 `Beyond.Gameplay.*` 不兼容 | 已拒绝作为终末地证据并在清单标记 |
| 完整运行时快照可离线分析已知方法 | 283,627,520 字节虚拟 RVA 快照；186 个区域完整；88/88 探针字节一致 | 已确认 |
| Mode 变更会启停技能并修改输入映射 | `_ApplyModeChange` 调用 Skill Enable/Disable 与 `AddMappingModifier` | 已确认主干，优先级待验证 |

对首批探针的磁盘 PE 基线显示：`IsAvailable`、`CheckCost`、
`CheckCooldown`、`CheckState` 和连携队列候选点落在 `.rdata`；另一部分 RVA
虽落在可执行节，但字节序列不在稳定指令边界上。这与 Dumper 的
`MethodInfo` 首指针回退路径一致，不能用“落在可执行节”单独判定入口
有效。

同一批 RVA 在运行时映像中位于更大的可执行映射内，磁盘 `.rdata` 结论仅描述静态文件，
不描述进程内最终代码。`analyze_runtime_method_probes.py` 对每个 16 KiB 入口采样执行
可达基本块遍历，遇到返回、陷阱和间接跳转即停止，避免把紧邻的下一个函数误认为当前
函数。符号表同时收集普通方法和类型索引中只保留在属性签名里的访问器 RVA。

仓库内现有 `data/endfield_research_kit/tools/endfield-il2cpp/`
可从 `Il2CppCodeGenModule.methodPointers` 正向映射元数据方法。但对当前
1.4.4 磁盘镜像使用旧版 `CodeRegistration` 地址会解出明显无效的计数和
指针，因此该路线要与运行时注册表定位结合，不能直接复用旧版常量。

## 尚未确认

1. `IsAvailable` 的调用者边界及最前面的 owner 级布尔门槛；
2. 玩家命令、AI 与直接施放入口在何处阻止冷却或资源不足的请求；
3. `_GetAtbCost` 的成本修正公式及 `CostAtb` 的不可返还成本语义；
4. 启动失败、主动取消和被打断时的返还规则；
5. 连携窗口的排序键、同帧优先级和过期/释放后的队列推进顺序；
6. `SkillPatchData` 如何将等级、潜能、天赋和形态修正写入技能实例；
7. 各技能命中序列中的 Buff、附着和条件动作如何具体影响当前伤害；
8. 失衡、处决及其与伤害事件的完整执行管线。
9. 战斗随机种子及 IFix 公式覆盖。

## 本地证据目录

忽略区 `data/research-artifacts/combat-1.4.4/` 按以下边界保存：

| 目录 | 内容 | 是否可重建 |
| --- | --- | --- |
| `binaries/` | 同版本 `GameAssembly.dll` | 应保留原件 |
| `dumps/normal/` | 普通 C# IL2CPP dump | 可由同版本运行时重建 |
| `dumps/ai/` | AI structured IL2CPP dump | 可由同版本运行时重建 |
| `logs/` | Dumper 与后续探针日志 | 每次采样独立保留 |
| `samples/skill/pelica/` | VFS 切片、解密 payload、参考与解码结果 | 原始切片和 payload 应保留 |
| `derived/indexes/` | 类型索引与引用图 | 可重建 |
| `derived/schemas/` | 样本 schema | 可重建 |
| `derived/runtime-method-analysis.json` | 运行时方法可达控制流与直接调用报告 | 可由入口采样重建 |
| `derived/runtime-method-analysis.hit-order.json` | 69 个动作、Buff 与伤害方法的控制流报告 | 可由同轮入口采样重建 |

提交库只保存工具、测试、结论和原始文件哈希，不提交游戏二进制与大体积派生产物。

## 下一执行序列

1. 对玩家命令、AI 和直接施放三类入口分别闭环完整可用性门禁；
2. 运行时记录普攻有效映射、缓存请求和输入，闭环超时及命中、移动、切人、受击后的重置状态机；
3. 展开 `_ApplyModeChange`、ModeData 和 MappingModifier，确认强化技能替换语义；
4. 还原 `CastEnd` 各 FinishType 对冷却、资源、Buff 和事件的分支；
5. 追踪 IFix 对伤害公式入口的覆盖，并确认抗性上下限配置的实际应用层；
6. 每个通用规则至少用两个机制不同的技能及一个外部行为样本交叉验证。

运行时采样前先生成同一探针集的磁盘基线：

```powershell
python tools/inspect_probe_pe.py `
  data/research-artifacts/combat-1.4.4/binaries/GameAssembly.dll `
  docs/research/combat-runtime-probes.json `
  --output data/research-artifacts/combat-1.4.4/derived/static-pe-probes.json
```

`probe_runtime_rvas.py` 除了记录探针地址本身，还会对采样字节中的
8 字节对齐指针生成 `pointerTargets`：其中包含目标内存权限、目标前导字节、
模块内 RVA 和目标中的二级模块指针。这些字段用来判断 `.rdata` 样本是
单纯数据、跳板描述符还是指向运行时解密代码的节点。

当前 Windows 客户端进程名是 `Endfield.exe`。高权限游戏进程会拒绝受 UAC
限制的 SSH 会话读取模块列表；即使显式启用 `SeDebugPrivilege`，当前保护层仍拒绝外部
模块快照。因此对该客户端应使用注入式 Dumper 采样，外部 Python 探针仅保留给未受
保护进程和后续诊断。

运行时入口采样完成后生成控制流报告：

```powershell
python -m tools.analyze_runtime_method_probes `
  data/research-artifacts/combat-1.4.4/IL2CPP_MethodProbes.full.json `
  data/research-artifacts/combat-1.4.4/derived/indexes/gameplay-types-ai.json `
  --output data/research-artifacts/combat-1.4.4/derived/runtime-method-analysis.json
```

逆向工具的可选依赖统一位于 `requirements-research.txt`，不加入网页服务的运行依赖。

## 文档导航

- [战斗系统反推计划](combat-system-reversing-plan.md)：总体原则、领域和阶段。
- [运行时类型地图](combat-runtime-type-map.md)：技能、资源、连携与候选入口。
- [配置到运行时桥接](combat-config-runtime-bridge.md)：MemoryPack、技能集合和实例创建。
- [技能可用性与资源扣费](combat-skill-availability-and-cost.md)：检查顺序、成本字段和实际扣费主干。
- [普攻连段、输入缓存与接续窗口](combat/basic-attack-chain.md)：下一段映射、输入缓存、提前接续和窗口清理。
- [单次命中的动作、Buff 与伤害执行顺序](combat-hit-action-order.md)：动作数组顺序、伤害流水线与同命中增益边界。
- [Buff 创建、启用、失活与结束生命周期](combat/buff-lifecycle.md)：实例分配、Modifier 注册、优先级失活与 Finish 顺序。
- [AbilitySystem 事件分发与动作优先级](combat/ability-event-dispatch.md)：事件四层消费者、目标选择、上下文压栈与优先队列。
- [伤害公式的已确认分层](combat/damage-formula.md)：前后修正阶段、属性抗性公式、防御曲线边界与待恢复项。
- [TimelineAction 执行模型](combat-timeline-action-execution.md)：逻辑帧调度、动作生命周期与 SequenceAction 边界。
- [佩丽卡技能数据结构还原](combat-pelica-skill-structure.md)：技能节点调用图、天赋潜能 Patch 与数据驱动边界。
- [佩丽卡与 Endaxis 配置对照](combat-pelica-endaxis-comparison.md)：现有 OperatorSheet 的字段来源、差异和可生成性。
- [证据清单](combat-evidence-manifest.json)：版本、来源、大小和哈希。
- [运行时探针](combat-runtime-probes.json)：首批版本化 RVA 采样点。
