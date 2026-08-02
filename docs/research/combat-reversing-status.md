# 战斗系统反推状态

## 当前阶段

主线已经完成“固定证据集、类型地图、首批配置到运行时桥接”，正在进入“恢复第一条真实
释放与扣费调用链”。目前不是缺少技能配置，而是客户端保护层使 `MethodInfo` 首指针不能
直接当作磁盘 PE 函数入口，需要进程内证据确认。

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

对首批 12 个探针的磁盘 PE 基线显示：`IsAvailable`、`CheckCost`、
`CheckCooldown`、`CheckState` 和连携队列候选点落在 `.rdata`；另一部分 RVA
虽落在可执行节，但字节序列不在稳定指令边界上。这与 Dumper 的
`MethodInfo` 首指针回退路径一致，不能用“落在可执行节”单独判定入口
有效。

仓库内现有 `data/endfield_research_kit/tools/endfield-il2cpp/`
可从 `Il2CppCodeGenModule.methodPointers` 正向映射元数据方法。但对当前
1.2.4 磁盘镜像使用旧版 `CodeRegistration` 地址会解出明显无效的计数和
指针，因此该路线要与运行时注册表定位结合，不能直接复用旧版常量。

## 尚未确认

1. `TryCastSkill`、`CanCastSkill`、`IsAvailable` 与四项检查的真实调用和短路顺序；
2. `CheckCost` 是纯检查、预留资源，还是会更新额外门槛状态；
3. `_ApplyCost`、`CostAtb` 和 USP setter 的精确扣费时点；
4. 启动失败、主动取消和被打断时的返还规则；
5. 连携窗口的排序键、同帧优先级和过期/释放后的队列推进顺序；
6. `SkillPatchData` 如何将等级、潜能、天赋和形态修正写入技能实例；
7. Hit、伤害、Buff、附着、失衡和处决的执行管线。

## 本地证据目录

忽略区 `data/research-artifacts/combat-1.2.4/` 按以下边界保存：

| 目录 | 内容 | 是否可重建 |
| --- | --- | --- |
| `binaries/` | 同版本 `GameAssembly.dll` | 应保留原件 |
| `dumps/normal/` | 普通 C# IL2CPP dump | 可由同版本运行时重建 |
| `dumps/ai/` | AI structured IL2CPP dump | 可由同版本运行时重建 |
| `logs/` | Dumper 与后续探针日志 | 每次采样独立保留 |
| `samples/skill/pelica/` | VFS 切片、解密 payload、参考与解码结果 | 原始切片和 payload 应保留 |
| `derived/indexes/` | 类型索引与引用图 | 可重建 |
| `derived/schemas/` | 样本 schema | 可重建 |

提交库只保存工具、测试、结论和原始文件哈希，不提交游戏二进制与大体积派生产物。

## 下一执行序列

1. 游戏运行后执行 `combat-runtime-probes.json`，确认各方法首指针所在区域及前导字节；
2. 扩展 Dumper 诊断，输出 `MethodInfo` 地址、首部指针、解析来源和内存权限；
3. 判断入口属于原生代码、跳板还是保护描述符，再选择反编译或最小 Hook；
4. 先恢复 `CheckCost -> _ApplyCost -> CostAtb/USP`，再向上连接 `CanCastSkill`；
5. 用佩丽卡 40 ATB 战技、35 秒连携和 100 USP 终结技做第一组运行时样本；
6. 形成首条调用链案例报告，记录成功、余额不足、冷却中和打断四种路径。

运行时采样前先生成同一探针集的磁盘基线：

```powershell
python tools/inspect_probe_pe.py `
  data/research-artifacts/combat-1.2.4/binaries/GameAssembly.dll `
  docs/research/combat-runtime-probes.json `
  --output data/research-artifacts/combat-1.2.4/derived/static-pe-probes.json
```

逆向工具的可选依赖统一位于 `requirements-research.txt`，不加入网页服务的运行依赖。

## 文档导航

- [战斗系统反推计划](combat-system-reversing-plan.md)：总体原则、领域和阶段。
- [运行时类型地图](combat-runtime-type-map.md)：技能、资源、连携与候选入口。
- [配置到运行时桥接](combat-config-runtime-bridge.md)：MemoryPack、技能集合和实例创建。
- [证据清单](combat-evidence-manifest.json)：版本、来源、大小和哈希。
- [运行时探针](combat-runtime-probes.json)：首批版本化 RVA 采样点。
