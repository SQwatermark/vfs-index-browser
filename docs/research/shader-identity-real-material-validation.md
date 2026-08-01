# Shader 身份与真实材质转换验证

## 结论

角色材质的 Shader 身份可以从 Unity 对象引用精确恢复，不需要根据材质属性签名猜测。
佩丽卡材质 `M_actor_pelica_cloth_04` 已验证为 `HGRP/CharacterNPR`，其引用链为：

```text
Material.m_Shader
  -> FileID 3
  -> CAB-8e64a7d61483ea16539b04f304be9ed7
  -> dependency-450067.ab
  -> Shader PathID -7822190029627442914
  -> HGRP/CharacterNPR
```

该 Shader Bundle 已包含在模型的 manifest 依赖闭包中。此前显示 `Unknown` 并非依赖缺失，
而是 AnimeStudio 按公开 Unity 布局读取终末地 Shader 编译载荷尾段时越界，导致整个
Shader 对象未进入对象表。

## 底层处理

`AnimeStudioObjectSnapshot/1.0.0` 现在额外导出序列化文件的 `externalFiles` 表，使未解析
PPtr 也能对应到原始 FileID、CAB 名称和加载状态。终末地 Shader 在
`SerializedShader` 已成功读取、后续编译载荷读取越界时，会保留名称和结构元数据并标记
`m_CompiledDataIncomplete`。该降级只表示编译载荷不完整，不宣称字节码已经完全解析。

常规模型快照只需要 Shader 对象提供引用目标名称，不应把完整 Shader 对象导出成 JSON。
后者会包含巨量编译数据，在真实样本中造成数 GB 内存占用。

## 转换验证

精确 Shader 名称写入 `ModelDocument.materials[].sourceMaterial.shader` 后，佩丽卡真实材质已
通过以下流水线：

```text
Material + ShaderLab 默认值
  -> MaterialBindingResolution
  -> MaterialSemanticIR
  -> BlenderNodeParameterPlan
```

真实数据还暴露并修复了一项契约问题：Unity `Material.m_Colors` 同时保存 Color 和 Vector，
Vector 在对象快照中也使用 `r/g/b/a`，不能要求其以 `x/y/z/w` 存储。转换器现在同时接受
Shader 语义通道和 Unity Material 存储通道，并统一输出四元素值。

佩丽卡丝袜材质生成了无 error 诊断的语义 IR 和
`EF_SilkStockings_MaterialState_v1` Blender 参数计划。仍存在的 warning 主要是当前有限
映射未消费 CharacterNPR 的其他属性，不代表丝袜子图输入无效。

## 当前边界

- Shader 名称和材质实例参数已经来自真实对象引用。
- ShaderLab/HLSL 归档仍是独立输入，不把大型 Shader 编译数据塞进模型快照。
- 当前语义映射只覆盖 CharacterNPR 丝袜状态，Blender 后端仍是可解释近似实现。
- 下一步应建立版本化 Shader 归档注册表，并让 Blender 后端消费参数计划，而不是再次读取
  `sourceMaterial` 并手工映射同一组字段。
