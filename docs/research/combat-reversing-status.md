# 战斗系统反推状态

## 当前阶段

主线已经完成“固定证据集、类型地图、配置到运行时桥接和首批方法入口采样”，正在恢复
第一条真实释放与扣费调用链。注入式 Dumper 已从运行中的客户端取得可执行方法字节；
当前重点从“入口是否有效”转为“恢复控制流、补齐运行时包装符号并验证调用时序”。

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
| 资源检查同时覆盖 ATB 与 USP | `CheckCost` 的可达控制流读取 `BattleManager.atb` 和 `AbilitySystem.ultimateSp` | 已确认调用结构，比较公式待标注 |
| 实际扣费分别进入资源修改接口 | `_ApplyCost` 调用 `NewUltimateSp().Apply()` 或 `BattleManager.CostAtb()`，随后触发技能事件 | 已确认主干调用 |

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
1.2.4 磁盘镜像使用旧版 `CodeRegistration` 地址会解出明显无效的计数和
指针，因此该路线要与运行时注册表定位结合，不能直接复用旧版常量。

## 尚未确认

1. `IsAvailable` 中受 IFix/运行时包装影响的三个未命名布尔入口与 `CheckCd`、`CheckCost`、`CheckState` 的对应关系；
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
| `derived/runtime-method-analysis.json` | 运行时方法可达控制流与直接调用报告 | 可由入口采样重建 |

提交库只保存工具、测试、结论和原始文件哈希，不提交游戏二进制与大体积派生产物。

## 下一执行序列

1. 对 `IsAvailable` 的三个未命名入口做短函数特征匹配，恢复四项检查的真实短路顺序；
2. 标注 `CheckCost`、`_ApplyCost` 和 `CostAtb` 的关键分支、字段偏移及返回语义；
3. 扩充探针到 USP setter、技能取消和打断路径，确认扣费与返还时点；
4. 用佩丽卡 40 ATB 战技、35 秒连携和 100 USP 终结技做第一组行为样本；
5. 对连携 pending 队列执行同样的 CFG 与字段访问分析；
6. 形成首条调用链案例报告，记录成功、余额不足、冷却中和打断四种路径。

运行时采样前先生成同一探针集的磁盘基线：

```powershell
python tools/inspect_probe_pe.py `
  data/research-artifacts/combat-1.2.4/binaries/GameAssembly.dll `
  docs/research/combat-runtime-probes.json `
  --output data/research-artifacts/combat-1.2.4/derived/static-pe-probes.json
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
  data/research-artifacts/combat-1.2.4/IL2CPP_MethodProbes.full.json `
  data/research-artifacts/combat-1.2.4/derived/indexes/gameplay-types-ai.json `
  --output data/research-artifacts/combat-1.2.4/derived/runtime-method-analysis.json
```

逆向工具的可选依赖统一位于 `requirements-research.txt`，不加入网页服务的运行依赖。

## 文档导航

- [战斗系统反推计划](combat-system-reversing-plan.md)：总体原则、领域和阶段。
- [运行时类型地图](combat-runtime-type-map.md)：技能、资源、连携与候选入口。
- [配置到运行时桥接](combat-config-runtime-bridge.md)：MemoryPack、技能集合和实例创建。
- [证据清单](combat-evidence-manifest.json)：版本、来源、大小和哈希。
- [运行时探针](combat-runtime-probes.json)：首批版本化 RVA 采样点。
