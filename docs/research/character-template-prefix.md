# CharacterTemplate 的有界字段导出

2026-08-26。入口 `tools/export_character_template.ps1`，输出格式
`character-template-prefix-v1`，**总体始终 partial**，不是完整 CharacterTemplate API。
前序原始资源取得过程见 [诀二进制研究](memorypack-arcane-2026-08-26.md)。

## 同日后续：14/14 条件叶子载荷完整

新增 UnityTargetSettingsDecoder，按 TargetSettings/SelectorData/DirectionSettings 字段顺序读
嵌套目标、RID 列表、方向参数；嵌套 RID 保留字符串，包括 -2 空引用。非空嵌套引用仅保存
身份，不代表递归解释了其语义。新增 CheckObjectTypeMatch、CheckBuffStackNumByTag、DebugPrint
叶子解码，复用既有 GameplayTagQuery 读取器，保留 signed tag ID，不从数值猜显示名。

真实样本现在 **14/14 直接叶子完整消费**；目标均为 Context/trigger 或 DebugPrint 的 Target，
selector/advancedDirection 引用均为 -2，列表为空；没有非空子引用遗留。模板总体仍 partial，
后缀 908 字节不变。最新输出 `tmp/arcane-character-conditions-complete.json`，脚本参数同下。
新增 3 项合成测试；UnityWorker **32 通过、4 外部资产跳过**。不启动外部 GUI，不提交 raw/JSON。

原生动作执行、标签目录身份和 trigger 与 input 分离的证据已进入 combat-spec
`docs/combo-condition-leaves.md`。本工具不推断冷却门禁或跨条件顺序，不声称 Next 模拟已通。
下文的 6 complete/8 raw 是上一批快照。

## 2026-09-01：连携条件引用闭包

角色模板导出不再只列 `comboSkillCheckAction.actionData` 的第一层 RID。已完整解码的条件叶子
若包含 TargetSettings，会继续收集 selector 的 finder/validator/postProcessor 与高级方向的
source/target 引用，并将这些 managed reference 一并写入 `conditionReferences`。闭包中的未知
类型仍只输出类型、RID、偏移、长度和 `rawBase64`；**被导出不等于已解释语义**。

当前完整解码叶子为 CheckSpellInflictionType、CompareFloat、CheckObjectTypeMatch、
CheckBuffStackNumByTag、DebugPrintAction、CheckDamageDecorateMask、CheckTargetsEqual、
CheckBuffStackNumAdvanced、ModifyDynamicBlackboard、CheckPhysicalInflictionType。已知类型必须完整
消费载荷，否则导出失败。`CheckBuffIdInContextAdvanced` 同样按原生字段顺序完整导出
`checkType / BlackboardBuffId[] / GameplayTagQuery / blackboardKey`；不在 VFS 中解释标签语义。
原生连携生命周期和枚举语义只由 combat-spec 文档维护；本页只定义
VFS 导出格式和证据边界。

桌面端服务已用该实现重导 `chr_0027_tangtang` 的实际 8896 字节角色资产。重导前后根
`sourceSha256=7fdfa2f96443489d39c497117e8660e2471f3cc3c3c571858321af171df98f4c` 不变，
而 `conditionReferences` 从含 raw 叶子变为 4/4 完整解码；其中
`CheckBuffIdInContextAdvanced` 明确得到 `checkType=1`、空 `BlackboardBuffId[]`、
`HasAny(2025186574)` 和空 `blackboardKey`。这证明变化来自解码能力而非替换来源资产；标签 ID 的
可读语义仍由下游同版本 GameplayTag 目录解析。

同一导出链也重导了 `chr_0004_pelica` 的实际 8144 字节角色资产，根
`sourceSha256=d3e8abd97fcc0b4411dfc422309f8a638681d6f8fd77a898e23e0af5cb9eae82` 与旧快照
一致。`CheckTargetsEqual.secondTargetSettings` 的嵌套闭包现明确包含零载荷
`Selector/CharacterTeamFinder/Data` 与 `Selector/MainCharacterValidator/Data`。VFS 对这类节点仍保留
`decodeStatus=raw`、空 Base64 和长度 0：零载荷类型身份已经完整，不应伪造一个 `data` 对象；下游
适配器可在同时校验类型、程序集和严格空载荷后还原公共 selector 类型。

## 字段与引用边界

- 从 MonoBehaviour 头读根 RID，只接受头部结束位置的 version 2 registry；核对真实 root
  类型和 expectedId，完整消费 CharacterTemplateData 的继承字段。
- 所有 component RID 必须存在，且恰好引用一个 AbilitySystemData；未引用的同类型对象
  不能作为该角色数据。RID 输出字符串，避免 JavaScript number 丢失 64 位精度。
- 按 Unity 声明字段序依次读取 shape、ModeConfig、SkillDataBundle、UI、三组 BuffInput、
  plunge、battleRoot、poise/mark 等字段，最后读 entityBlackboard。**不搜索键或挑最大候选**。
- DataPair.valueDouble 为 float64；BuffInput.AssignPair.numericValue 为 float32，字段
  targetKey/inputValueKey/useDirectValue/directValueType/numericValue/stringValue，不可混用。
- 完整解码的叶子清单以上一节为准；动作基类为
  bool32 isEnable、int32 priorityLevel/priorityOffset/serverActionIndex。CompareFloat 按
  Unity 的 valueA/compare/valueB 顺序，而非 MemoryPack 顺序；嵌入 BlackboardDouble 的
  序列化字段为 bool32 useKey、float32 value、aligned UTF8 key。
- 未知条件及其已发现的引用闭包保留 rawBase64/类型/RID/偏移，未知 AbilitySystem 后缀也保留字节；其他组件只列
  引用元数据，没有宣称完整解码或重建。已知叶子截断/尾随字节直接拒绝，不降级成“成功”。

输出是取证切片，不是可直接送入模拟的规则模型；UI 的向量字段目前使用点分字段键。
数值枚举保留原值，未擅自绑定显示名或解释未知语义。版本适用性目前只验证 1.4.4 本地样本。

## 可复现

从 VFS 仓库根构建（避免 unity-worker/global.json 的本机缺失 SDK 9.0.200）：

```powershell
dotnet build unity-worker/src/Endfield.Extensions/Vfs.Endfield.Extensions.csproj -c Release
dotnet test unity-worker/tests/Vfs.UnityWorker.Tests/Vfs.UnityWorker.Tests.csproj -c Release
```

从 Endaxis 根使用已经导出的原始 MonoBehaviour：

```powershell
pwsh -NoProfile -File D:/Projects/vfs-index-browser/tools/export_character_template.ps1 `
  -InputPath tmp/arcane-character-raw/objects/0000-pAD03AA3777D0B4C2.dat `
  -ExpectedId chr_0032_lizhiyan -OutputPath tmp/arcane-character-prefix-verified.json
```

脚本完成解码后才创建输出，拒绝覆盖已有文件。重复研究使用新的输出名；所有产物留在被
忽略的 tmp/，不要提交游戏载荷。无外部 AnimeStudio/GUI 进程，不改变服务默认 schema。

## 真实样本结果

raw 13724 字节，SHA256
`33934515EA8B90EFDF35F3FAE4901124ED54FC16C087A9755574D8DB58DCA0BC`。
根 `[168,544)`，26 个组件；AbilitySystem `[4092,7368)`，前缀消费 2368 字节至 6460，
剩余 908 字节显式保留。实体四键与上一批局部证据一致。

SkillDataBundle 的 5 条 comboSkillConditions 均 event=121，immediately/main/guard=false；
enableComboSkillBlackboard=true，独立局部 consumed_layer/type=0。
14 个直接引用中 **6 个完整解码、8 个原始保留**。第五条按配置顺序：
DebugPrintAction（RID 尾号 9833）、CompareFloat（9834）、CheckSpellInflictionType（9835）。
CompareFloat 比较 EntityBB_wisd_greater_will 与常量 1，compare=0；下一项 mask=15、
savedKey=EntityBB_consumed_type。原生执行与枚举解释由 combat-spec 研究承接。

新增 9 项合成测试（不含游戏载荷）；UnityWorker 全组 **29 通过、4 项需外部资产的测试跳过**。
另执行上述真实 raw 导出，校验 hash、最终偏移与两项完整叶子。没有据此放行 Endaxis 的 8 场
失败；剩余目标/标签条件、DebugPrint、完整组件后缀与运行时事件门禁仍需继续研究。
