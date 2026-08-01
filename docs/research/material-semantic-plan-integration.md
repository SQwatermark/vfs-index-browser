# 材质语义计划接入

## 数据边界

`ModelDocument` 仍只保存从 Unity 对象恢复的材质、纹理和 Shader 身份，不保存派生的
Blender 节点计划。生成 GLB 时，服务端按本地 Shader 归档即时编译受支持材质，并将
`BlenderNodeParameterPlan` 写入对应 glTF Material 的 `extras.endfieldMaterialPlan`。

这样划分有三点目的：

- Shader 源码和 HLSL 归档不进入模型快照、GLB 或 Git；
- 计划可以随转换器和 Shader 归档更新，不污染稳定的模型事实；
- Blender 只消费版本化计划，不再承担 ShaderLab 解析和变体选择。

当前只接入 `HGRP/CharacterNPR` 的丝袜材质局部状态。服务通过
`VFS_BROWSER_SHADER_ARCHIVE_ROOT` 定位 1.4.4 归档根目录，默认位置为
`data/shader-archives/1.4.4`。根目录下需要存在：

```text
Assets/packages/com.hg.render-pipelines/runtime/shaders/materials/
  characternpr/characternpr.shader
  characternpr/characternpr/Sub0_Pass0_Fragment_b391.hlsl
```

归档缺失时不会生成计划；归档目录存在但所需文件缺失、变体选择不唯一或材质绑定无法
解析时会直接报错。GLB 缓存身份包含归档路径、Shader 文件大小和修改时间以及计划构建器
版本，因此增加、移除或替换归档都会重建 GLB。

## Blender 消费

Blender 导入器验证计划的 `format`、`version` 和节点组名称后，读取当前近似后端已经能
表达的 Dry Color、Edge Color、Minimum/Maximum Affect 和 Silk Mask。存在有效计划时不再从
`endfieldSourceMaterial` 重复解释这些值，并在材质上记录
`endfieldMaterialPlanApplied`。

当前节点组仍是 Eevee 近似实现，尚未完整实现计划中的 Wet Color、独立丝袜 NDF、
SpecRamp 和场景运行时输入。后续应逐个扩展节点组接口，而不是绕过计划重新读取 Unity
属性。
