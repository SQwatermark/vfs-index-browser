# 缓存角色导出 Blender 实验

本实验将 VFS Browser 已缓存的角色 GLB 转换为可继续编辑的 `.blend`：

- 保留模型层级、骨架、蒙皮、材质分区和纹理；
- 复用 `tools/blender_import_model.py`，不维护第二套材质恢复逻辑；
- 将引用贴图打包进 `.blend`；
- 为面部 SDF、身体 Skin、头发、衣物、丝袜和覆盖阴影选择独立后端；
- 重新打开产物，检查材质路径、节点连接和贴图打包状态。

## 使用

```powershell
python experiments/endfield_blender_export/export_character_blend.py `
  data/local-validation/perlica-base.glb `
  experiments/endfield_blender_export/pelica-embedded.blend `
  --render experiments/endfield_blender_export/pelica-embedded-preview.png `
  --framing portrait
```

Blender 不在默认位置时，可传入 `--blender` 或设置 `BLENDER_EXE`。

`--main-light-direction X Y Z` 表示从角色指向预览主光的 Blender 空间方向。
它与 CharacterVolume 的环境光方向是两个不同概念；`--lighting` 只负责环境光
Profile 和环境贴图。

## 证据边界

佩丽卡面部材质匹配的静态主变体是 `b225`。当前面部路径只复刻其中与相邻变体
共享的 SDF 核心载体：

- 根据主光左右方向镜像 SDF 的 U 坐标；
- 使用 SDF Lightmap 的 R、G 通道计算方向阈值；
- 使用 SDF Mask 的 G 通道混合 SDF 与法线信号；
- 将结果作为 DiffRamp 横坐标。

身体 Skin 使用 `b191` 中可确认的 `N dot L -> DiffRamp` 路径。面部与身体不再
共用一套经验 Ramp。

尚未接入的 Normal、Emotion、Highlight、Shadow LUT 分支，以及运行时
`CharacterParams`、面部相机侧补偿和游戏阴影系统，会在材质的
`endfieldPreviewDiagnostic` 中明确记录。Blender 的场景阴影接收层只是兼容
预览，不应被解释为游戏 Shader 原公式。

在这些最终调色分支完成前，面部 SDF 节点会保留并保持连接，但最终 Surface
使用 BaseMap 回退。这样避免把偏暗的中间 DiffRamp 结果误当成完整面部颜色。

默认主光方向 `(0, -1, 0)` 是便于检查角色正面的预览设置，不是从
CharacterVolume 环境光方向推导出的游戏事实。

## 自动验证

生成后默认运行 `validate_embedded_blend.py`，至少检查：

- 场景中存在 Mesh、Armature 和有效材质输出；
- 面部使用 SDF 后端，且 SDF Lightmap、SDF Mask、DiffRamp 均已接入；
- 身体 Skin 与头发使用各自的后端；
- 丝袜材质在存在对应元数据时使用专用节点组；
- 所有被节点引用的图片均已打包。

可用 `--skip-validation` 跳过重新打开验证，仅用于调试失败产物。
