# CharacterNPR Skin b225 面部复刻边界

## 目标

本文只记录可以从终末地归档 Shader、真实材质数据和已有逐行移植代码中核对的内容。
它不把视觉调参当作游戏公式，也不把中间颜色当作最终屏幕颜色。

当前匹配佩丽卡面部材质的静态主变体是：

```text
Assets/packages/com.hg.render-pipelines/runtime/shaders/materials/
characternpr/characternpr_skin/Sub0_Pass0_Fragment_b225.hlsl
```

对应关键字：

```text
_DIFF_RAMP_ON
_EMOTION_MAP
_HIGHLIGHT_MAP
_NORMALMAP
_OUTLINE_MASK
_SDFLIGHTMAP
_SHADOW_LUT_TEX
```

## 1.4.4 交叉复核

FractalMiner 的 1.4.4 归档已经包含同一关键字组合的更新变体：

```text
characternpr_skin/Sub0_Pass0_Fragment_b138.hlsl
```

1.4.4 的反编译结果保留了资源名和较多向量表达式，比 1.3.3 的纯临时寄存器
形式更适合作为当前复刻基准。交叉检查确认以下主干仍然存在：

- BaseMap 与 EmotionMap 混合；
- 线性 RGB 转 sRGB 后查询铺平的 Shadow LUT；
- SDF 左右镜像、R+G 距离场和 B 通道伪法线；
- SDF Mask G 混合 SDF/普通法线分支；
- DiffRamp、NPR 漫反射、HighlightMap、GGX、皮肤高光和 CP14 二级高光；
- 附加光列表、VFX 调色、曝光和雾。

但 1.4.4 不能与 1.3.3 按寄存器编号直接对照，并且存在必须按版本处理的状态：

- 匹配变体编号由 `b225` 变为 `b138`；
- Shadow LUT 在 1.4.4 中明确使用 `sampler_LinearMirror`，不能继续把
  1.3.3 的 `sampler_PointClamp_BumpMap` 别名当作跨版本固定采样状态；
- 1.4.4 使用 `_ExposureWithMiscParams.y` 乘到最终颜色，1.3.3 使用
  `_ExposureParams.x` 的倒数形式，二者的常量缓冲语义需要分别保留；
- 1.4.4 直接读取 `_ScreenSpaceShadowMask` 的 R/G 通道并接入主光和角色阴影，
  不能由 Blender 普通灯光阴影自动等价替代。

因此后续 Blender 实验应以 1.4.4 `b138` 为公式基准，同时保留 1.3.3
`b225` 作为交叉证据，不应继续扩展一个假定跨版本不变的单一公式。

## 已确认的数据流

### 基础色与 Shadow LUT

`b225` 先采样 BaseMap，并按 EmotionMap 的 alpha 混合表情颜色。随后把线性 RGB
逐通道转换为 sRGB，用作铺平的 `32 x 32 x 32` Shadow LUT 坐标：

```text
R/G -> 当前蓝色切片内坐标
B   -> 相邻两个切片编号和插值权重
```

LUT 尺寸为 `1024 x 32`，坐标常量为：

```text
31 / 1024
0.5 / 1024
31 / 32
0.5 / 32
1 / 32
```

两个版本都会手动对相邻 B 切片插值。1.3.3 `b225` 的反编译采样器别名表现为
Point + Clamp；1.4.4 `b138` 则明确是 Linear + Mirror。采样状态应跟随目标
Shader 版本，不能从坐标公式反推。LUT 输出仍只是后续漫反射合成的输入，
不是最终面部颜色。

### SDF 与 Diff Ramp

面部 SDF 使用物体右轴、前轴和调整后的主光方向。其核心过程已经能与
`b225` 的寄存器表达式逐项对应：

1. 将主光投影到面部右轴和前轴。
2. 根据左右方向镜像 SDF UV。
3. 使用 SDF Lightmap 的 R+G 构造距离场值。
4. 使用 B 通道构造面部伪法线。
5. 使用 SDF Mask G 在伪法线和普通法线之间混合。
6. 将 SDF 信号或几何 `NdotL` 映射到 DiffRamp 的横坐标。

SDF Mask 的已知语义：

| 通道 | 作用 |
| --- | --- |
| R | 面部边缘光遮罩 |
| G | SDF 信号与法线信号混合 |
| B | 平坦球谐/面部边缘光区域选择 |
| A | 视角相关皮肤高光门控 |

### 最终颜色并非 BaseMap 乘 Ramp

`b225` 的主输出链至少包含：

```text
Base/Emotion
  -> Shadow LUT
  -> SDF + DiffRamp
  -> NPR diffuse composition
  -> GGX + HighlightMap
  -> skin specular
  -> subsurface edge term
  -> CP14 secondary specular
  -> optional VFX color adjustment
  -> exposure division
  -> optional fog
```

1.3.3 的最终局部主光结果位于 `_4148/_4150/_4152`；1.4.4 的对应主色先形成
`_1905`，再经过附加光循环进入 `_1931`，最后由 `_2826` 执行可选 VFX 调色。
把 DiffRamp 采样结果或 Shadow LUT 结果直接接到材质输出，都会跳过后续能量合成，
因此可能让脸变灰、变粉或整体发黑。

## `_CharacterParams` 的使用边界

以下是从 `b225` 和逐行移植的 `EndField_Uber.glsl` 共同确认的用途，不代表
当前已经恢复了每个运行时值：

| 参数 | 已确认用途 | 当前值来源 |
| --- | --- | --- |
| CP0.y | 总漫反射倍率 | 需要 CharacterVolume 打包结果 |
| CP0.z | 阴影/LUT 混合基线 | 需要 CharacterVolume 打包结果 |
| CP0.w | 环境亮度倍率 | 需要 CharacterVolume 打包结果 |
| CP1.x | 环境亮度分支混合 | 需要 CharacterVolume 打包结果 |
| CP1.y | 阴影强度 | 需要 CharacterVolume 打包结果 |
| CP1.z | 主光阴影忽略混合 | 需要 CharacterVolume 打包结果 |
| CP1.w | 场景主光与角色主光方向混合 | 需要 CharacterVolume 打包结果 |
| CP3.rgb | Face 环境光颜色 | 需要 CharacterVolume 打包结果 |
| CP4.rgb | Face 角色主光颜色 | 需要 CharacterVolume 打包结果 |
| CP6.xyz | 方向性环境光方向 | Profile 可提供方向，但仍需确认打包变换 |
| CP7.xyz | 方向性环境光偏移、倍率、基线 | Profile 有强度参数，仍需确认精确打包 |
| CP8/CP9 | 皮肤视角高光 | 需要 CharacterVolume 打包结果 |
| CP11.xyz | 角色主光方向 | 需要 CharacterVolume 打包结果 |
| CP11.w | Ramp 偏置 | 需要 CharacterVolume 打包结果 |
| CP12.x | Ramp/相机侧补偿开关或权重 | 需要 CharacterVolume 打包结果 |
| CP12.y | 场景光与角色光颜色混合 | 已知参与方式，值仍需运行时恢复 |
| CP12.z | `charIgnoreSceneAdditionalLights` 的反值 | 已有二进制研究结论 |
| CP12.w | 环境曝光/雾分支混合 | 已知参与方式，值仍需运行时恢复 |
| CP13.w | GGX 项开关/倍率 | 需要 CharacterVolume 打包结果 |
| CP14/CP15.z | 面部第二高光与 SDF 阈值 | 需要 CharacterVolume 打包结果 |

不能直接把第三方 Substance 预览器中为了可调试而配置的默认值当作游戏运行时值。

## Substance 移植代码的证据等级

`data/research/ShiyumeMeguri/RuriRipperImporterSubstance/shader/EndField_Uber.glsl`
对面部公式很有价值，因为 `shadeFace` 将反编译临时寄存器整理成了可读表达式。
但该文件开头也明确列出 Substance Painter 缺失的引擎能力：

- 屏幕空间主光阴影被固定为全亮；
- 主光方向和颜色改由 Painter UI 提供；
- ObjectToWorld 被替换为单位矩阵；
- 自定义 AO 被跳过；
- 环境反射来自 Painter HDRI，而非游戏预积分角色 Cubemap；
- 部分天气、深度、逐实例和后处理输入改成手动参数。

所以采用规则是：

1. 数学表达式必须回查 1.4.4 `b138` 或 1.3.3 `b225`。
2. `H1` 到 `H20` 标出的替代输入不能移植为 Blender 的“游戏默认值”。
3. Painter 参数面板中的 `_CharacterParams` 默认值只能用于诊断节点连线，
   不能作为角色导出的正式运行时常量。

## Blender 接入原则

### 可以直接接入

- BaseMap、EmotionMap、SDF Lightmap、SDF Mask、DiffRamp、HighlightMap、
  Shadow LUT 的真实纹理和采样方式。
- 已经与 `b225` 对照的数学公式。
- 材质中明确序列化的颜色、倍率、开关和 UV 参数。
- Texture2D 的原始 `m_ColorSpace`，应从资源元数据传递，不能按文件名猜测。

### 必须显式暴露或保持中性

- 场景主光方向、颜色和强度。
- CharacterVolume 打包后的 `_CharacterParams0..15`。
- 曝光、角色专用环境光、角色 Cubemap。
- 主光阴影、附加光列表、角色阴影图和屏幕空间阴影。

### 暂不启用

- 缺少运行时输入时的完整面部最终输出。
- 用任意常数替代 CP11.w、CP12.x、CP14、CP15。
- 为了“看起来接近”而添加未在原 Shader 中出现的亮度、对比度或颜色补偿。

在这些输入恢复前，正式 Blender 导出保持 BaseMap 面部回退是更诚实的默认行为；
完整公式应作为独立、默认关闭的实验分支存在。

## 面部暗侧的独立交叉证据

本地 `endfield_research_kit` 的详细记录还包含一项与“脸发黑”直接相关的源代码级修正。
其 Wulfa/Zhuang 方仪面部 SPIR-V 片元和 Li 的法线贴图面部片元共同证明：

- 角色主光存在一份未乘强度的方向光 RGB；
- 强度缩放后的 RGB 只进入相邻的直接光亮度/色度项；
- 环境 `lightBlend` 使用未缩放的方向光 RGB；
- 若把已经缩放的颜色同时用于两处，在 `_CharacterParams12.y = 1` 时会等价于重复应用
  主光强度，使暗侧结构被压平或过度驱动。

因此后续移植不能把“主光颜色”和“主光颜色乘强度”合并成一个 Blender 输入。至少应分为：

```text
MainLightColor
MainLightColorScaled
```

并按原片元中的消费者分别连接。在完整消费者尚未逐项翻译前，不能通过提高环境光、
降低阴影强度或添加自发光来掩盖重复乘强度的问题。

这条证据解释的是一种已确认的暗侧错误来源，但不代表当前任意一次黑脸截图都只由它引起。
Shadow LUT 中间值误接最终输出、缺失 CharacterVolume 参数、屏幕空间阴影默认值错误等，
仍需分别排查。

## 可复现检查

使用数据流脚本检查最终颜色依赖：

```powershell
python trace_hlsl_dataflow.py `
  "$env:TEMP\EndField-AllShader-1.3.3\Assets\packages\com.hg.render-pipelines\runtime\shaders\materials\characternpr\characternpr_skin\Sub0_Pass0_Fragment_b225.hlsl" `
  --target _4148 --target _4150 --target _4152
```

该脚本是保守的单行赋值图分析器。它不会解析控制流、循环、别名内存或宏，
因此只能用来审计“某个输出依赖了哪些寄存器和外部输入”，不能证明 Shader
语义已经完整恢复。
