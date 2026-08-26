# 诀资源：部分初始化快照与 MemoryPack 解码

## 已确认结果

本批仅修资源解码，不修改游戏规则、Endaxis 转换产物或服务默认 schema。

- 1.4.4 AbilityAction union 注册表有 386 项；当前 runtime snapshot 仅 49 个类型槽已初始化。
  原工具要求全部初始化，因此缺 tag 173 不是“游戏没有这种动作”。
- 已初始化槽通过既有映射和 wrapper token 校准出 TypeDefinition stride `0x5c`；
  不能仅凭 metadata version 29 假定为常见的 88 字节。
- 未初始化槽按 Il2CppType usage 解析索引，再精确匹配 metadata 的 byvalTypeIndex、wrapper
  全名和 token。tag 173 的槽 RVA `0x0E32DFE0` 为 `0x4003F0EB`，对应
  `Beyond.Gameplay.Core.FinishOwnerAction.Data`。tag 0 是 BoneAttach，不是 null/IfElse。
- 新 `--metadata` 路径恢复 AbilityAction 386、Finder 22、Validator 11、PostProcessor 10、
  CalculationBase 6 个映射。所有已有对应映射必须一致；错版本、错 stride、缺 wrapper、
  冲突校准和非 Il2CppType token 均拒绝，不按声明顺序猜类型。
- 泛型 wrapper 使用 dump 中实际类名匹配，不能仅把 runtime 名里的点替换成下划线。
  schema 提取器新增 `--union-map`，将已恢复派生类型显式作为根；只加递归深度不能补齐
  dump 继承索引未覆盖的嵌套类型。

元数据公共 header 与 type definition 字段参考
[Il2CppDumper 的格式定义](https://github.com/Perfare/Il2CppDumper/blob/master/Il2CppDumper/Il2Cpp/MetadataClass.cs)。
本机 92 字节 stride 来自运行时锚点而非该通用格式。未解析其余游戏定制字段。

## ObtainCostAction 的真实格式

`ObtainCostAction.DataForMemoryPack.Deserialize` RVA `0x03E61450`：
`0x03E61583` 请求连续 5 字节，`0x03E61599` 读取 bool，`0x03E6159C` 读取后续 int32，
后者经 `0x03E616CF` 传给 `__uspRecoverTag__` setter `0x03E61AD0`。
因此 `useUspRecoverTag + uspRecoverTag` 是 bool + inline int32，没有 GameplayTag object header。

修复前零 tag 被读成空对象，只消耗 1 字节，随后序列错位并报 invalid boolean 190。
修复仅增加该字段的 raw tag 覆盖；既有非零测试和新增零 tag/后续 union 哨兵测试均通过。
不能用 reference JSON 错位推导出的 tag 0=IfElse 来“修正”原生注册映射。

## 来源与边界

本机路径：`D:/Projects/IL2CPP-Dumper/x64/Release/IL2CPP_GameAssembly.runtime.bin`，
配套 JSON 标明 `virtual-rva`、moduleBase `0x7FFDB9650000`；
dump 为 `Arknights Endfield 1.4.4/IL2CPP_Dump_AI`。

SHA256：

- GameAssembly.dll：`0C5573679BC6DEC2D068A14335466DB7CCF20AF9BAE2B983FB9D45677D80FFCE`
- global-metadata.dat：`90C58E26E87C7227A85DDA3FEDF6CE5ED0B06DC1F76E0ABBE75AB20750ADF97E`
- seal_total 原始载荷：`91C06C62CF7480E1A93D35ADED13944F310BCF058C824B74D44E8BE3122DBAD8`
- combo_skill 原始载荷：`C4395DB3487065840003D31B32E40F0455A87840F9C5EE712B93171867937841`

经 `/api/raw` 读取 Effective 资源；seal_total id 692109，完整消费 15710 字节；combo_skill
id 778753，完整消费 24320 字节。旧索引列出的 combo 长度为 23729，不应拿索引旧长度替代
实际下载响应长度。二者没有使用 reference JSON 推断 union。控制 Buff 仍两次读取
`EntityBB_consumed_type`，技能只声明不同键 `consumed_type=0`，未新增写入证据。

扩大到诀 42 BuffData + 24 SkillData：64/66 完整解码。剩余 `...combo_skill_seal2` 与
`...train_showhp` 在 `BuffData.applyTags[0]` 格式处拒绝。这两份不计入“无写入”的本地二进制
证据；未覆盖角色实体模板、IFix 或运行时 Patch，不能断言客户端缺陷或读分支不可达。

## 可复现入口

在 Endaxis 目录运行，所有生成结果放入已忽略的 `tmp/`，不提交二进制、schema、union map 或审计。
先对下列五个 base 逐次运行；第一次使用 VFS 仓库已跟踪的
`schemas/memorypack-known-unions.json`，后续以前一次 output 为输入：

```powershell
python D:/Projects/vfs-index-browser/tools/extract_memorypack_unions.py `
  --runtime D:/Projects/IL2CPP-Dumper/x64/Release/IL2CPP_GameAssembly.runtime.bin `
  --dump-root 'D:/Projects/IL2CPP-Dumper/Arknights Endfield 1.4.4/IL2CPP_Dump_AI' `
  --metadata 'D:/Hypergryph Launcher/games/Endfield Game/Endfield_Data/il2cpp_data/Metadata/global-metadata.dat' `
  --union-map D:/Projects/vfs-index-browser/schemas/memorypack-known-unions.json `
  --base-class Beyond.Gameplay.Core.AbilityAction.AbilityActionData `
  --output tmp/arcane-tool-unions.json
```

其余 base：`Beyond.Gameplay.Core.Selector.Finder.Data`、`...Selector.Validator.Data`、
`...Selector.PostProcessor.Data`、`Beyond.Gameplay.Core.CalculationBase`。省略 `--metadata`
仍保持旧工具的全初始化要求。随后：

```powershell
python D:/Projects/vfs-index-browser/tools/extract_memorypack_schema.py `
  --dump-root 'D:/Projects/IL2CPP-Dumper/Arknights Endfield 1.4.4/IL2CPP_Dump_AI' `
  --union-map tmp/arcane-tool-unions.json --output tmp/arcane-tool-schema.json
python D:/Projects/vfs-index-browser/tools/decode_memorypack_json.py `
  --binary tmp/arcane-seal-total-vfs.bin --class Beyond.Gameplay.Core.BuffData `
  --schema tmp/arcane-tool-schema.json --union-map tmp/arcane-tool-unions.json `
  --output tmp/arcane-seal-total-rebuilt.json
```

解码 CLI 当前 exit 0 不代表完整；必须检查 `__meta.complete=true`、无 error 且 consumed=bytes。
本批聚焦测试 8 项通过；MemoryPack 全组 11 通过，仍有两项既有失败：SuperArmor 黑板值底层类型、
Enemy AI marker unmanaged layout。不为本任务顺带改动未核实的格式。
已单独加载提交前 HEAD 解码器，确认这两项与 ObtainCost 测试原先都失败；本批只修复后者。
