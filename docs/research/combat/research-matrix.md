# 战斗系统研究矩阵

## 状态说明

- `A`：主干已确认，可继续扩展边界。
- `B`：结构已确认，关键时序或分支未闭环。
- `C`：只有数据或类型线索。
- `D`：尚未系统研究。

## 总矩阵

| 领域 | 状态 | 已有事实 | 关键缺口 | 下一验证动作 |
| --- | --- | --- | --- | --- |
| 技能实例化 | B | SkillData、SkillPatchData、Blackboard、RefreshRuntimeData 均存在 | Patch 合并顺序与实例重建条件 | 反编译 `_OnCreate`、`RefreshRuntimeData`，运行时记录最终黑板 |
| 释放条件 | B | `IsAvailable` 的冷却、成本、标签、实体状态短路顺序已定位；玩家请求与状态迁移链已闭环 | 主控、AI、直接施放入口在何处真正调用完整门禁 | 追踪命令生产前检查、方法指针/IFix 调用和失败请求的运行时行为 |
| 技能替换 | C | ComboController 有基础、运行时、实际映射和派生技能结构 | 更新时点、优先级、旧实例处理 | 追踪替换事件与映射 getter/setter |
| 普攻连段 | B | ComboCache 临时选择下一段并管理缓存，AllowedNext 与 exclusive 负责两条放行路径；两种 Action 的退出清理已确认 | 超时、命中/移动/切人重置及重击分流 | 运行时记录有效 Attack 映射、请求、当前 Skill 与输入，构造中断实验 |
| 资源扣费 | B | ATB/USP 检查和 `_ApplyCost` 主干已定位；扣费发生在 `OnTick` 的 `startCdTime` 边界 | 释放前门禁、成本修正、返还、取消和打断 | 先闭环非法资源请求为何不能施放，再追踪 `_GetAtbCost` 与 `CostAtb` |
| 冷却 | B | MultiPeriodicTimer 与 startCdTime 被读取 | 冷却起算帧、层数、缩减和重置 | 追踪计时器写入及属性修正来源 |
| Timeline 调度 | A | 固定 30 Hz、floor 帧、动作生命周期、Sequence 顺序 | 同起点多个 TimelineAction 的稳定排序 | 检查 Load 阶段容器与相同 startTime 样本 |
| Hit 执行 | B | 显式动作按数组顺序执行；多目标采用目标外层、DamageUnit 内层；计算前事件、两阶段 processor、Modifier 前后、护盾/生命和受击后事件主序已确认 | 额外目标、特殊生命类型、递归动作和死亡中断 | 追踪 `m_extraTargets` 来源与普通/独立生命/环境伤害分支差异 |
| 伤害公式 | B | 前后 damage modifier、最终倍率乘积、六类属性抗性因子及真实/生命流失旁路已确认 | 防御曲线配置、暴击/格挡/失衡/附着倍率与舍入时点 | 恢复全局防御公式对象并标注 CalculateDamage 临时值 |
| Buff 生命周期 | B | 创建、启用/禁用、Tick、Modifier 注册/移除、Finish、驱散、12 种 stacking、优先级、Modify 参数合并及 Finish 重入保护已确认 | delta 配置分布、跨 AbilitySystem 事件嵌套、联网同步参数 | 追踪 AbilitySystem 广播，再分析属性代入链 |
| 事件系统 | B | EventDispatcher、优先 SequenceAction、Skill 实例、连携桥接的四层同步顺序已确认 | 同优先级稳定性、双缓冲切换、递归与跨实体传播 | 追踪具体伤害事件两端及 DoubleBufferedPriorityQueue 实现 |
| 投射物 | B | ID、移动段、速度曲线和发射动作可读取 | 初始位置、寻敌、碰撞、命中回调和多目标 | 追踪 ProjectileComponent 初始化与碰撞回调 |
| AbilityEntity | C | Spawn 动作能引用独立 SkillData | Tick、生命周期、命中频率和目标选择 | 解码实体配置并追踪 Spawn 到执行对象 |
| 面板属性 | B | 已有多组游戏 UI 快照可验证最终值 | 四维转换、装备与武器词条的完整运行时公式 | 对照属性类型与构筑计算入口建立公式图 |
| 战斗属性 | C | DamageAction 会在执行期读取运行时状态 | 静态面板到动态 Attributes/Modifier 的桥接 | 追踪角色入场初始化和 Buff Modifier 应用 |
| 天赋与潜能 | C | 表、Buff、Patch 和技能刷新均有数据入口 | 每种效果究竟属于静态 Patch、Buff 还是事件 | 全量分类效果类型，再各选一例闭环 |
| 连携窗口 | B | pending 队列、canCast 与窗口剩余时间分离 | 同帧排序、排队、过期和多段窗口身份 | 反编译 pending 更新/释放并构造双窗口实验 |
| 失衡与处决 | C | 配置和现有模拟有线索 | 实际值、阈值、状态转换和处决资格 | 从受击后的 poise/weakness 调用链向下追踪 |
| 元素异常 | C | 附着动作可在伤害前后显式出现 | 积累、触发、覆盖、持续和事件顺序 | 选燃烧与腐蚀各一条完整链路 |
| AI 与主控差异 | C | SkillData 有 AI exclusive 字段，AbilitySystem 有主控身份 | AI 释放规则、普攻行为和共享资源竞争 | 对照同一技能的玩家与 AI 入口 |

## 第一批闭环顺序

第一批不按干员划分，而按运行时解释器划分：

1. **技能状态机**：请求、映射、可用性、连段、中断、开始、结束。
2. **动作调度器**：逻辑帧、TimelineAction、SequenceAction、同帧顺序。
3. **Hit 管线**：目标、Buff、附着、伤害、受击、事件。
4. **属性与 Buff**：静态面板、动态 Modifier、读取时点。
5. **载体对象**：投射物和 AbilityEntity。
6. **构筑修正**：等级、天赋、潜能、形态和技能替换。

每批完成后使用至少两个机制不同的干员交叉验证。佩丽卡可作为简单投射物法术样本，庄方宜
可作为多段普攻与复杂载体样本；后续还需加入具有形态替换、强化技能和周期 Buff 的样本。
