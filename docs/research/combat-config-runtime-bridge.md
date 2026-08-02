# 技能配置到运行时对象

## 已确认的同版本桥接

当前客户端 `Beyond.Gameplay.Core.SkillData` 的 IL2CPP 类型包含 `48` 个字段，其中一个是
静态字段 `DEFAULT_DUMMY_POSITION_OFFSET`，因此实例字段正好为 `47` 个。本地样本
`chr_0028_wulfa_ultimate_skill.json` 经 MemoryPack 解码后：

- 完整消费 `311917` 字节，没有尾部剩余；
- 顶层对象正好包含 `47` 个键；
- `__meta.class` 为 `Beyond.Gameplay.Core.SkillData`；
- 字段覆盖 `castData`、`actionGroupData`、Buff、黑板、标签、目标选择和 UI 提示等运行时
  `SkillData` 成员。

这条证据把“VFS 中的二进制 JsonData”与“IL2CPP 运行时 SkillData”直接连了起来。AKEDB
可以用于提供枚举名称和快速抽样，但不是这条桥接成立的前提。

## 配置与实例创建候选路径

### 干员技能集合

`Beyond.Gameplay.Core.SkillDataBundle` 保存一个战斗实体可用技能的稳定 ID：

- 全部普攻、主动技能和被动技能；
- 当前普通攻击链和可用重击；
- `normalSkillId`、`ultimateSkillId`、`comboSkillId`；
- 下落攻击与内部 `dodgeSkillId`；
- 连携条件、优先级、黑板与默认输入映射；
- 特定技能 ID 的运行时 `SkillType` 覆盖。

`AbilitySystemData.skillDataBundle` 将这份技能集合挂到实体配置上。运行时
`AbilitySystem.m_skillDataBundle` 保留该引用，并将创建后的 `Skill` 放入 `m_skills` 和
`m_activeSkillMap`。

### 单个技能创建

已确认的输入结构为：

```text
CreateSkillOptions
  skillId
  level?
  skillData?
  skillType
  skillSource
  extraBlackboard
  allowFrameSplitInit

SkillInitParam
  skillData
  patchData
  extraBlackboard
  serverInstId?
```

候选方法按数据形状可以连接为：

```text
AbilitySystem._InitSkills()
  -> AbilitySystem._CreateSkill(CreateSkillOptions)
  -> CreateSkillOptions.GetSkillData()
  -> Skill.Create(SkillInitParam, owner, skillType, skillSource, allowFrameSplitInit)
  -> Skill._OnCreate(SkillData, SkillPatchData, Blackboard)
```

这仍是由签名和字段关系建立的候选对象流，不是已从函数体恢复的直接调用图。需要继续确认
`GetSkillData()` 的资源注册表、`_CreateSkill()` 的分支，以及 `SkillPatchData` 的应用顺序。

### 运行时刷新

`Skill.RefreshRuntimeData(SkillData, SkillPatchData, Blackboard)` 和
`AbilitySystem._RefreshCharActiveSkillData(skillId)` 表明技能实例可以在不更换身份的情况下
刷新配置。`AbilitySystem` 还监听技能等级、服务端技能、天赋被动和普通技能替换事件。

因此后续语义模型需要区分：

- 稳定身份：技能 ID、实例 ID、来源和所属实体；
- 配置输入：基础 `SkillData`；
- 角色构筑修正：`SkillPatchData`、额外黑板、等级和潜能/天赋触发的替换；
- 运行时状态：冷却、持续时间、当前目标、是否已扣费和技能中 Buff。

不能把最终运行时技能扁平化为一份永不刷新的静态 JSON。

## 首个配置样本：Wulfa 终结技

本地同版本解码样本的关键字段为：

| 字段 | 值 |
| --- | --- |
| `skillId` | `chr_0028_wulfa_ultimate_skill` |
| `level` | `1` |
| `durationFrame` | `311` |
| `exclusiveFrame` | `155` |
| `castData.costType` | `0` |
| `castData.costValue` | `100` |
| `castData.cooldownTime` | `0` |
| `actionGroupData.timelineActions` | `106` 组 |

AKEDB 同名数据将 `costType` 枚举化为 `UltimateSp`。另取佩丽卡的配置做快速交叉抽样：

| 技能 | `costType` | `costValue` | `cooldownTime` |
| --- | --- | ---: | ---: |
| `chr_0004_pelica_attack1` | `UltimateSp` | 0 | 0 |
| `chr_0004_pelica_normal_skill` | `Atb` | 40 | 3 |
| `chr_0004_pelica_combo_skill` | `UltimateSp` | 0 | 35 |
| `chr_0004_pelica_ultimate_skill` | `UltimateSp` | 100 | 0 |

这些样本支持两个后续验证方向：

1. `CostType` 的默认枚举值可以是 `UltimateSp`，但成本为零时不会实际扣除；不能仅凭
   `costType` 判断某技能消耗终结技能量。
2. 连携技的 `cooldownTime` 位于通用 `CastData`，而连携窗口和队列资格另由
   `BattleManager` 管理；“窗口合法”与“技能自身冷却结束”是两个独立概念。

随后已从本地同版本 VFS 精确读取并解密三份佩丽卡文件。补齐 Cinemachine 类型 dump 与
`BlackboardSuperArmorValue.value -> Int32` 的泛型类型后，三份文件均完整消费：

| 本地文件 | 消费字节 | 顶层字段 | `costType` 原始值 |
| --- | ---: | ---: | ---: |
| `chr_0004_pelica_normal_skill` | 12354 / 12354 | 47 | 1 |
| `chr_0004_pelica_combo_skill` | 8576 / 8576 | 47 | 0 |
| `chr_0004_pelica_ultimate_skill` | 15421 / 15421 | 47 | 0 |

结合 Common 程序集中的 `CostType` 成员顺序与 AKEDB 枚举名，可确认当前样本使用
`0 = UltimateSp`、`1 = Atb`。AKEDB 参考 JSON 在此用于识别多态 union 类型和核对枚举
名称；最终成本、冷却与完整消费结果来自本地客户端字节。

## 下一步验证

1. 在运行时采集 `GetSkillData`、`_CreateSkill`、`Skill.Create` 和 `_OnCreate` 的入口字节；
2. 若入口可 Hook，记录单次创建时的 `skillId`、`SkillData*`、`SkillPatchData*` 与返回
   `Skill*`，验证对象流；
3. 对同一技能改变等级、潜能或天赋，比较 `SkillData` 指针、patch 内容和运行时刷新事件；
4. 再进入 `CanCastSkill`、`CheckCost` 与 `_ApplyCost` 的释放链验证。
