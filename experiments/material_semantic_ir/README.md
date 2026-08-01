# MaterialSemanticIR 草案

这里存放材质语义中间表示的独立实验，不接入当前模型导出和 Blender 导入主线。

## 文件

- `material-semantic-ir.schema.json`：0.1.0 结构草案；
- `examples/silk-stockings-b391.json`：领域节点折叠后的最小示例。
- `validate_ir.py`：补充验证 Schema 无法表达的跨引用和 DAG 约束。
- `character_npr_mapping.py`：将 Material Binding Resolution 编译为受限的
  CharacterNPR 语义图，并生成与 `bpy` 解耦的 Blender 参数计划；
- `test_character_npr_mapping.py`：端到端验证默认值、实例值、UV、采样器、变体和丝袜
  普通/高级分支。

验证示例：

```powershell
python -m unittest tests.test_material_semantic_ir_schema
python -m unittest experiments.material_semantic_ir.test_character_npr_mapping
```

## 约束

- 图必须是 DAG，引用的节点和输出必须存在；
- 颜色值必须声明颜色空间，空间向量必须声明坐标空间；
- 每个节点必须记录证据等级；
- 未知绑定、未知语法和无法表达的运行时输入必须进入 diagnostics；
- JSON Schema 只负责通用结构，算子签名由后续 operator registry 严格校验。

该格式当前没有兼容承诺。完成 b391 和面部样本验证后再考虑提升到正式 `schemas/`。

## CharacterNPR 映射边界

当前端到端原型只恢复 1.4.4 b391 中可独立验证的丝袜材质局部状态：基础色、阴影侧
上游颜色、粗糙度、覆盖率、视角影响、各向异性方向和高光强度。它不会生成 Blender
节点，也不会假装已经恢复动态浸润、丝袜独立 NDF、SpecRamp、角色主光或屏幕空间阴影。
`_SilkStockingsRainWetMaskScale` 与 `_SilkStockingsAlbedoAffectType` 虽作为源 Binding
保留，但尚未进入语义节点，因此不会出现在 Blender 节点组参数中；对应缺口以诊断暴露。

`build_blender_parameter_plan()` 输出的是节点组接口计划：每个插槽记录源 Binding、有效值、
纹理 UV/采样器或必须由宿主连接的运行时输入。后续 `bpy` 后端只能消费该计划，不应再次
按 Unity 属性名重复判断模式。

普通与高级丝袜模式只读取 `_SilkStockingsAdvance` 的有效绑定值。即使 Material 中残留
`_SilkStockingsMask`，开关为 `0` 时 IR 不会创建 Mask 采样节点，Blender 参数计划也会将
该插槽标记为 `disabled`。

完整命令行闭环位于 `tools/research/build_material_semantic_plan.py`。示例：

```powershell
python tools/research/build_material_semantic_plan.py `
  experiments/material_binding_resolver/examples/character-npr-subset.shader `
  experiments/material_binding_resolver/examples/stockings-material.json `
  --archive-version 1.4.4 `
  --material-id sample:pelica:stockings `
  --pass-name Sub0_Pass0 --blob 391 `
  --hlsl-uri shader-archive://1.4.4/characternpr/Sub0_Pass0_Fragment_b391.hlsl `
  --material-keyword _SILK_STOCKINGS `
  --material-keyword _METALLICSPECGLOSSMAP `
  --material-keyword _NORMALMAP `
  --texture-metadata experiments/material_binding_resolver/examples/texture-metadata.json `
  --texture-rules experiments/material_binding_resolver/examples/texture-rules.json `
  --output material-plan.json
```

输出文件同时包含 `bindingResolution`、`semanticIr` 与 `blenderParameterPlan`，便于逐层
检查信息在哪一步丢失。该工具不会打开 Blender，也不会写入生产缓存。
