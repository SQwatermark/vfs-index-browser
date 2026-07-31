# 角色材质恢复管线

本文约束 NPC 与可操控角色的材质恢复边界。两类角色可以采用不同的资源入口，
但不得各自实现材质解释、纹理绑定或导出格式。

## 目标

- 保留 Unity Material 的原始 Shader 指针、纹理槽、浮点值、整数值和颜色；
- 用统一的预览语义生成网页 GLB 和 Blender 输入；
- 允许 Prefab、AvatarMesh 等不同装配方式复用同一后半段；
- 对无法恢复的 Shader、纹理或引用形成诊断，不用猜测结果掩盖缺失信息。

## 两类入口

### Prefab 角色

可操控角色通常能从 `GameObject -> Renderer -> Mesh/Material` 引用图恢复完整对象图。
`load_animestudio_objects()` 读取带 `$animestudio` 元数据的对象，保留真实
`sourceFile + pathId` 身份和 PPtr 关系。

### AvatarMesh NPC

部分 NPC 没有可直接提取的运行时 Prefab。AvatarMesh 负责选择 LOD、Mesh 和材质槽，
Avatar 提供骨架。独立导出的 Material JSON 不带 `$animestudio` 元数据，因此先由
`build_standalone_material_objects()` 补成标准 `AnimeStudioObject`：

- Material 使用确定性的合成身份；
- TexEnv 中的非空 Texture2D 指针转换为标准 PPtr reference；
- AvatarMesh 只负责把材质按原顺序挂到 Renderer 的 `m_Materials[n]`。

从这一步开始，NPC 和 Prefab 角色的数据结构相同。

## 共享阶段

```text
Prefab object graph ───────────────┐
                                   ├─ AnimeStudioObject graph
AvatarMesh + standalone materials ─┘
    -> attach_mesh_geometry()
    -> infer_character_material_role()
    -> collect_material_textures()
    -> attach_texture_images()
    -> ModelDocument
    -> GLB / Blender / other backends
```

`attach_mesh_geometry()` 是当前共享的 Mesh 与 Material 发射阶段。它将原始 Shader、
TexEnv、Int、Float 和 Color 写入 `materials[].sourceMaterial`，将可移植的近似结果写入
`materials[].previewPbr`。
原始值和预览值不得相互覆盖。

角色材质类别根据 Shader 属性签名判断，而不是依赖不稳定的资源名称。当前优先级为：

1. `_UseGrayAsAlpha == 1`：`overlayShadow`；
2. `_SDFLightmap`：`skin`；
3. `_StrokeMap`：`hair`；
4. `_EnableRealisticLighting` 或 `_ClearCoat`：`cloth`；
5. `_EyeHighLight`：`eye`；
6. 其他角色材质：`generic`。

SDF 必须先于 `_EyeHighLight` 判断，因为终末地的皮肤和面部 Shader 也会声明眼部高光
属性。Deathgirl 样本曾因此把身体和面部误判为 `eye`。

## Shader 身份

Material 中的 Shader PPtr 不等于 Shader 已成功解析。当前 Deathgirl 的五种实际 Shader
对象会在 AnimeStudio 读取 `Shader` 数据时发生越界，独立 Material JSON 因此只能保留
`m_FileID + m_PathID`，名称为空。

这种情况下：

- 材质属性和纹理仍可用于预览；
- `shader` 暂为 `Unknown`；
- ModelDocument 写入 `MATERIAL_SHADER_UNRESOLVED`；
- 不根据材质名虚构 Shader 名称。

后续支持新 Shader 序列化结构后，应让入口适配器提供真实 Shader PPtr 身份；共享材质
阶段和导出结构不需要改变。

## 当前验证

Deathgirl LOD0 已验证：

- 9 个 Mesh、9 个 Skin、338 个 Avatar 骨架节点；
- 10 个可用 Material 中有 9 个被 LOD0 实际引用；
- 32 张依赖纹理完成解析和嵌入；
- 身体/面部为 `skin`，两套衣物为 `cloth`，头发为 `hair`，虹膜/眉毛为 `eye`；
- 可生成不含动画的独立材质 GLB。

下一阶段先用 NPC 与可操控角色各增加一个样本，验证同一属性签名产生相同
`previewPbr`，再继续处理 Shader 细节和 Blender 近似。动画保持为独立输入，不进入
当前材质验收范围。
