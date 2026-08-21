# 战斗系统知识库

本目录是终末地战斗系统反推的主题入口。目标不是把配置字段逐项翻译成 Endaxis 字段，
而是先还原游戏使用这些配置的运行时解释器：对象怎样创建、条件怎样判断、动作怎样调度、
数值何时读取、事件如何传播，以及取消、替换和分支如何改变执行路径。

## 阅读路径

1. [系统总图](system-map.md)：先理解配置、运行时对象、调度器、结算与事件之间的关系。
2. [研究矩阵](research-matrix.md)：查看每个领域已经确认什么、还缺什么、下一步如何验证。
3. [证据与结论规范](evidence-methodology.md)：理解“已确认”“高可信推导”和“假设”的区别。
4. 按专题阅读现有案例文档。

## 专题导航

| 主题 | 当前文档 | 当前成熟度 |
| --- | --- | --- |
| 配置与实例化 | [配置到运行时桥接](../combat-config-runtime-bridge.md) | 基础配置到运行时对象的结构桥接已确认 |
| 技能合并与 Patch | [SkillData 合并、覆盖与运行时 Patch](skill-patch-merge.md) | 等级、天赋、潜能、运行时 Patch 与 Mode 顺序已闭环；IFix 激活状态待证 |
| 技能状态机与替换 | [技能请求、替换与生命周期](skill-state-machine.md) | 请求与 Mode 主干已定位，调用边界待闭环 |
| 普攻连段与输入 | [普攻连段、输入缓存与接续窗口](basic-attack-chain.md) | 临时映射和窗口清理已确认，重置条件待闭环 |
| 释放条件与资源 | [技能可用性与资源扣费](../combat-skill-availability-and-cost.md) | 检查主干已确认，取消与返还未闭环 |
| 时间轴与动作 | [TimelineAction 执行模型](../combat-timeline-action-execution.md) | 30 Hz 调度和 Sequence 顺序已确认 |
| 战斗帧调度 | [同帧顺序、技能列表与延迟施放](frame-scheduling.md) | TickGroup、组件启动优先级与分组列表顺序已确认，玩家跨实体插入顺序待闭环 |
| Hit、Buff 与伤害 | [单次命中执行顺序](../combat-hit-action-order.md) | 动作、两阶段计算与普通落血事件主序已确认 |
| 伤害公式 | [伤害公式的已确认分层](damage-formula.md) | 抗性、防御曲线、暴击、七个倍率区间及核心配置值已恢复；装饰位和 IFix 待补 |
| Buff 生命周期 | [Buff 创建、启用、失活与结束](buff-lifecycle.md) | 激活、叠层、驱散与移除主序已确认，特殊 Buff 边界待闭环 |
| AbilitySystem 事件 | [事件分发与动作优先级](ability-event-dispatch.md) | 四层消费者主序已确认，双缓冲队列边界待闭环 |
| 技能样本 | [佩丽卡技能结构](../combat-pelica-skill-structure.md) | 配置图较完整，部分运行时分支待验证 |
| 配置差异 | [佩丽卡与 Endaxis 对照](../combat-pelica-endaxis-comparison.md) | 仅作为差异样本，不作为游戏事实来源 |
| 投射物 | [ProjectileComponentData](../projectile-component-data.md) | 可按 ID 读取，运动与碰撞解释尚不完整 |
| 类型词典 | [运行时类型地图](../combat-runtime-type-map.md) | 核心类型已建立，事件与属性类型仍需扩充 |
| 当前进度 | [反推状态](../combat-reversing-status.md) | 持续更新 |

## 文档边界

- 这里记录游戏事实、证据和未知项，不保存为了模拟方便而作的近似。
- AKEDB、技能描述和现有 Endaxis 配置只能提供检索线索，不能单独证明运行时语义。
- 自动生成器在对应规则达到“闭环”前不得输出该字段；缺失比猜测更有价值。
- 每个结论都应能追溯到客户端版本、配置路径、类型/方法身份或可重复实验。

## 当前主线

当前优先级是：

1. 技能请求、可用性、替换、连段与中断状态机；
2. TimelineAction、SequenceAction、事件回调和同帧执行顺序；
3. Hit 内伤害、Buff、附着、投射物和 AbilityEntity 的统一执行模型；
4. 属性快照、面板数值、伤害公式和各类 Modifier 的代入顺序；
5. 天赋、潜能、等级 Patch、形态和特殊技能替换如何改变上述对象；
6. 多名干员与运行时实验交叉验证；
7. 最后才定义可自动生成的 Endaxis 战斗 IR 与 TS lowering。
