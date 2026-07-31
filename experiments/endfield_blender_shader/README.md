# 终末地角色 Shader 的 Blender 实验

## 目标与边界

本目录是独立实验，不修改项目现有的模型导出、网页预览或 Blender 导入流程。

当前目录同时保留早期近似节点和后续公式级研究。早期节点用于验证 Blender 表达能力，
不再代表正式 Shader 复刻方向：

- `EF_CharacterSurface_v0_1`：保留 Blender PBR 受光，并增加可控的角色环境光下限。
- `EF_SilkStockings_v0_1`：复用终末地丝袜遮罩的 RGBA 语义，以原皮肤底色、视角相关覆盖和各向异性高光近似黑丝。

后续 HLSL 数据流研究见：

- `face-b225-recovery-boundary.md`
- `silk-stockings-b391-recovery.md`
- `silk_stockings_b391_reference.py`
- `trace_hlsl_dataflow.py`

这些资料确认正式实现必须区分源 Material、运行时输入和 Blender 近似。未知输入不得用
Ambient Floor、自发光或手工染色掩盖；早期节点中的这些参数只作为历史实验保留。

## 已确认的渲染结构

### 角色并非简单自发光

终末地角色会接受场景主光、阴影和材质 PBR 参数，但又有一套角色专用光照输入。当前资源和 Shader 共同表明：

1. 标准材质仍读取主光方向、主光颜色、阴影、法线、金属度、粗糙度和高光。
2. `_CharacterParams0..16` 注入角色主光混合、环境光、阴影染色、边缘光和曝光相关参数。
3. `charoverridevolumeprofile.asset` 为角色设置独立环境光：
   - `charAmbientLightBaseIntensity = 1.0`
   - `charAmbientLightDirIntensity = 0.6`
   - `charAmbientLightDirParam = 0.15`
4. 配套 `charoverridevolume.prefab` 是优先级 `10000`、权重 `1` 的全局 Volume。
5. 角色还使用独立 Cubemap：
   `assets/beyond/arts/entity/common/hdris/t_hdri_reflection_char_01.exr`

因此“暗场里角色不会黑成一团”更接近以下组合，而不是一个不受场景影响的纯 Emission：

```text
场景主光与阴影
+ 角色专用环境光和方向性补光
+ 角色反射 Cubemap
+ 曝光与暗部 Ramp 重映射
+ 材质分区的 PBR/NPR 混合
```

Blender 通用节点无法直接读取终末地运行时的 `HGCharacterVolume`，所以早期实验采用：

- Principled BSDF 负责绝大部分场景受光；
- `Character Ambient Floor` 只混入少量带环境色的基色，近似角色光照下限；
- 建议将 `Character Ambient Tint` 调成当前场景环境光的综合色，而不是固定纯白；
- 有 HDRI 时仍应给 Blender World 使用 HDRI，不能只靠 Ambient Floor。

这个 Ambient Floor 在实现上使用 Emission BSDF，只是一条已废弃的低权重预览近似，
不代表游戏角色是自发光体，也不应进入公式验证后端。

### 身体和服装不是统一 Toon Shader

当前材料可以按功能理解为：

- 衣物、武器：PBR 为主，保留法线、金属度、粗糙度、清漆和各向异性。
- 皮肤/脸：PBR 高光与风格化漫反射混合，脸部还使用 SDF、Diff Ramp、Shadow LUT。
- 头发：独立漫反射/高光法线与各向异性。
- 眼睛：独立散射、高光和染色逻辑。
- OverlayShadow：额外乘算 Pass，不能当普通透明贴图。

这也是当前节点组只先覆盖“通用角色表面”和“丝袜”的原因。把脸、头发和眼睛塞进同一个万能 Principled 节点，会很快变成不可维护的参数堆。

## 黑丝为什么看起来通透

游戏的丝袜不是简单透明材质。反编译 Shader 和 1.4.4 属性表共同确认：

```text
_SilkStockingsMask:
R = 各向异性高光强度
G = 各向异性方向/锐利度
B = 湿润时的光滑度
A = 透肉覆盖度
```

核心综合色近似为：

```text
facing = saturate(dot(N, V))
affect = lerp(
  MinAffect,
  MaxAffect,
  min(pow(1.05 - facing, 2 * coverage), 1)
)

frontColor = skinBase * dryTint
silkColor = lerp(frontColor, edgeColor, affect)
```

正面观察时保留更多 `skinBase * dryTint`，掠射角逐渐靠近深色丝袜边缘色。这样会产生“能看到肉色，但仍是一层织物”的观感，而不需要真实 Alpha 透明。

高光另走切线空间的各向异性瓣；湿润度还会降低粗糙度。第一版 Blender 节点保留了这些视觉关系，但使用 Principled 的各向异性模型替代游戏的定制 NDF，因此不能视为逐指令一致。

## Blender 使用方式

支持 Blender 4.3。

### 在当前文件中安装节点组

1. 打开 Blender 的 Scripting 工作区。
2. 打开 `endfield_shader_nodes.py`。
3. 点击 Run Script。
4. 在材质节点编辑器中添加 Group：
   - `EF_CharacterSurface_v0_1`
   - `EF_SilkStockings_v0_1`

脚本只会新建带版本号的节点组；同名节点组已存在时直接复用，不会覆盖已有材质。

丝袜节点组需要拆开连接 `_SilkStockingsMask`：

```text
贴图 Color -> Stocking Mask RGB
贴图 Alpha -> Stocking Coverage
```

Blender 4.3 的 `Separate Color` 节点只拆分 RGB，因此不能在节点组内部从 Color Socket 取出 Alpha。将 Coverage 单独暴露也让通道语义更明确；没有独立 Alpha 时可保持默认值 `1`。

### 命令行验证

```powershell
& "C:\Program Files\Blender Foundation\Blender 4.3\blender.exe" `
  --background `
  --factory-startup `
  --python validate_shader_nodes.py
```

验证脚本检查接口、关键节点、连接关系和重复安装的幂等性，并在本目录生成：

```text
endfield_shader_nodes.blend
```

### 推荐参数起点

通用衣物：

```text
Character Ambient Floor = 0.04 ~ 0.10
Character Ambient Tint  = 场景环境综合色
Roughness               = 使用原贴图
Metallic                = 使用原贴图
```

黑丝：

```text
Dry Tint                = 深灰紫或深棕灰，不建议纯黑
Edge Color              = 接近黑色但保留少量综合色
Min Affect              = 0.03 ~ 0.10
Max Affect              = 0.75 ~ 0.90
Anisotropy Strength     = 0.55 ~ 0.85
Base Roughness          = 0.35 ~ 0.55
Wet Roughness           = 0.05 ~ 0.12
Character Ambient Floor = 0.03 ~ 0.08
```

不要把丝袜材质设置成普通 Alpha Blend。除非原网格确实存在独立的皮肤层，否则真实透明会看到腿内部、背面或错误的排序关系。

## 证据来源

本地直接证据：

- `data/research/ShiyumeMeguri/FractalMiner/Assets/Project/EndField/AllShader_1.4.4.7z`
- `data/research/ShiyumeMeguri/RuriRipperImporterSubstance/shader/EndField_Uber.glsl`
- `data/research/ShiyumeMeguri/RuriRipperImporterSubstance/unity_material.py`
- `docs/research/endfield-rendering.md`

公开资料：

- Apple Developer 对终末地技术团队的采访确认项目在 Unity 上自研了渲染管线、图形抽象层和 ECS：
  https://developer.apple.com/news/?id=cpt08xv8
- FractalMiner 保存的各版本 Shader 归档：
  https://github.com/ShiyumeMeguri/FractalMiner/tree/main/Assets/Project/EndField
- Blender `Shader to RGB` 仅支持 EEVEE，并会破坏部分 PBR 特性：
  https://docs.blender.org/manual/zh-hans/4.1/render/shader_nodes/converter/shader_to_rgb.html
- Blender Principled BSDF 提供各向异性、Sheen、Coat 和 Emission 层：
  https://docs.blender.org/manual/nb/5.0/render/shader_nodes/shader/principled.html

## 下一步

正式路线以 `docs/design/integrated-roadmap.md` 为准：

1. 先稳定源 Material 数据契约，避免丝袜参数被提前烘入 PBR 预览值。
2. 用纯函数验证 b391 丝袜和 b138 面部的 HLSL 中间值。
3. 将 Blender 后端拆为快速预览、公式验证和 Cycles 参考三类。
4. 恢复 CharacterVolume、场景主光、阴影和曝光后，再做最终截图对照。
