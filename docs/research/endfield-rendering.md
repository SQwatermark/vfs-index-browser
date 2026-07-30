# 《终末地》渲染路线调研

## 结论

《终末地》的角色渲染不适合概括为统一 NPR。更准确的实现模型是按材质区域混合写实 PBR 与风格化渲染：服装、武器及环境强调材质质感和真实受光，面部、头发和眼睛负责维持二次元造型与色彩层次。轮廓线不是主要视觉支柱，不应作为预览器默认效果。

这与当前样本中的 Shader/材质分区一致：同一个角色 Prefab 同时包含 Skin、Hair、Eye、Cloth 和 OverlayShadow 变体，Cloth 具有标准微表面所需的法线、金属度和光滑度数据，面部则额外携带 SDF 光照、Ramp 与专用高光语义。

## 公开资料

- 机核对开发团队的访谈将目标称为“写实主义二次元”，并说明二次元角色与写实场景之间的平衡不是纯写实材质复刻，而是让笔触随光照、视角变化；服装和武器仍保持较高写实度：[《终末地》开发团队访谈](https://www.bilibili.com/opus/1011529093889392680)。
- 后续访谈继续以 PBR 与 NPR 的结合描述画面，并讨论玻璃、水等写实材质表现：[机核后续访谈](https://www.bilibili.com/opus/1136927664898048018)。
- NVIDIA 的官方页面确认 PC 版本使用 DLSS 4 多帧生成与 Reflex；这属于最终帧生成和延迟链路，不能用于推导角色材质公式：[NVIDIA《明日方舟：终末地》技术页面](https://www.nvidia.cn/geforce/news/arknights-endfield-dlss-4-multi-frame-generation/)。
- Unity HDRP 的功能说明可作为标准 PBR、光照、体积和后处理能力的参照，但不能证明游戏直接采用某个未修改的 HDRP Shader：[Unity HDRP 功能文档](https://docs.unity.cn/Packages/com.unity.render-pipelines.high-definition%4017.6/manual/HDRP-Features.html)。

## 本地证据

FractalMiner 归档的 1.3.3 Shader 与佩丽卡真实材质共同确认：

- `_MetallicGlossMap`：`R=Metal`、`G=Spec`、`B=Shadow`、`A=Smoothness`；
- `_BumpMap`：法线；
- `_DiffRampMap`：漫反射分段/风格化色阶；
- `_SpecRampMap`：高光色阶；
- `_SDFLightmap`：面部等区域的方向性风格化光照；
- `_StrokeMap`、`_EyeHighLight`、`_UseGrayAsAlpha` 分别可用于识别头发、眼睛和覆盖阴影材质。

因此材质分类应依赖 Shader 属性签名，而非 `cloth`、`face` 等不稳定名称。原始属性完整保留在 ModelDocument，GLB 只承载可移植预览语义。

佩丽卡样本的 `_BumpMap` 导出图只有 RG 携带数据、B 恒为 0。直接作为 glTF Normal Texture 会令衣物法线严重偏转并呈现黑亮表面；翻转 DirectX 风格 G 通道并由 RG 重建 Z 后，衣物基础色和受光恢复正常。这个结论已有开关法线贴图的 A/B 渲染和像素级转换测试支持。

## 还原路线

1. 衣物和武器先恢复标准 PBR：Base Color、Normal、Metallic、Roughness，之后再加入 Spec、Shadow、Clear Coat 和各向异性。
2. 面部、头发、眼睛分别建立专用节点组，读取真实 Ramp、SDF 和高光贴图，而不是共用一个 Toon Ramp。
3. OverlayShadow 按原混合与深度/模板语义实现，避免把它当普通透明贴花。
4. 默认关闭几何描边；仅在 Shader 或真实画面证明某材质需要轮廓时局部启用。
5. 使用多个角色、昼夜光照和近远镜头做截图对照，逐项校准而不是只凭单个正面样本。

## 证据边界

公开访谈能够证明美术方向，Shader 与材质数据能够证明字段和分支存在；二者都不能单独证明每个版本的完整运算公式。最终还原仍需要反编译 Shader、真实材质值、贴图通道统计和游戏截图四类证据互相校验。

## 面部材质实测

佩丽卡面部材质同时启用了 `_UseDiffRampMap`、`_UseSDFLightmap`、`_UseShadowLutTex` 和 `_FaceHighlightMap`。对应的 `_DiffRampMap`、`_SDFLightmap`、`_SDFMask`、`_ShadowLutTex` 与 `_HighlightMap` 已能随 ModelDocument 和 GLB 完整导出。

`_SDFLightmap` 的 R/G 通道呈左右镜像的面部距离场，但单独选择一个通道并映射到 Diff Ramp 会使整张脸落入错误的阴影色阶。该实验说明 SDF 贴图不是可直接显示的颜色输入；正确实现至少还需要恢复光照在面部局部坐标中的方向、左右通道选择、距离阈值、`_SDFMask` 分区及 `_ShadowLutTex` 调色关系。在公式确认前，Blender 默认预览继续使用稳定的法线受光近似，不启用实验性 SDF 节点。

### 编译变体定位

根据佩丽卡面部材质实际启用的 Toggle，Pass 0 的静态主变体是 `Sub0_Pass0_Fragment_b225.hlsl`；启用屏幕空间阴影遮罩时对应 `b301`。二者的材质局部关键字相同：

- `_DIFF_RAMP_ON`
- `_EMOTION_MAP`
- `_HIGHLIGHT_MAP`
- `_NORMALMAP`
- `_OUTLINE_MASK`
- `_SDFLIGHTMAP`
- `_SHADOW_LUT_TEX`

`b377`、`b453` 还要求运行时溶解，不是普通静态预览的默认选择。此前关注的 `b171` 缺少 Diff Ramp、Shadow LUT 与 Highlight，并启用了角色自定义分支，不符合该材质。

通过相邻变体差分和采样上下文，`b225` 中与面部材质相关的匿名纹理寄存器可确定为：

| 寄存器 | Shader 属性 |
| --- | --- |
| T13 | `_DiffRampMap` |
| T14 | `_HighlightMap` |
| T15 | `_EmotionMap` |
| T16 | `_ShadowLutTex` |
| T21 | `_BaseMap` |
| T22 | `_BumpMap` |
| T23 | `_SDFLightmap` |
| T24 | `_SDFMask` |

`_ShadowLutTex` 不是普通阴影颜色贴图。佩丽卡样本尺寸为 `1024×32`，对应横向铺开的 `32×32×32` 三维颜色 LUT。Shader 将线性基础色转为 sRGB 后，以 R/G 定位切片内坐标，以 B 选择相邻的两个蓝色切片，执行两次采样并插值。`_DiffRampMap` 则用光照计算结果作为横坐标、固定 `0.5` 作为纵坐标采样。

### SDF 核心流程

`b225` 中已经能够还原的面部 SDF 主干可表达为以下伪代码：

```text
lightX = dot(mainLightDirection, objectRight)
lightZ = dot(mainLightDirection, objectForward)
horizontalLight = normalize(float2(lightX, lightZ))

sampleU = horizontalLight.x > 0 ? uv.x : 1 - uv.x
sdf = SDFLightmap(sampleU, uv.y)
mask = SDFMask(uv)

pseudoNormalX = horizontalLight.x > 0 ? 2 * sdf.b - 1 : 1 - 2 * sdf.b
pseudoNormalZ = 1 - abs(pseudoNormalX)
pseudoNormal = normalize(float3(pseudoNormalX, epsilon, pseudoNormalZ))

sdfSignal = directionalThreshold(
  sdf.r + sdf.g,
  horizontalLight.z,
  characterGlobals
)
normalSignal = dot(transformToWorld(pseudoNormal), mainLightDirection)
diffuseSignal = lerp(sdfSignal, normalSignal, mask.g)
diffuseColor = DiffRamp(float2(diffuseSignal * 0.5 + 0.5, 0.5))
```

Shader 属性声明将 `_SDFMask` 的 RGB 通道直接命名为 `RimMask / SDFMask / FlatSHMask`：

- R 控制面部边缘光影响范围；
- G 控制距离场信号与伪法线受光之间的混合；
- B 控制平坦球谐光照区域；
- A 进入视角相关的面部边缘计算，但属性声明没有给出正式名称。

SDF 主分支仍依赖运行时全局参数 `_CharacterParams11.w` 与 `_CharacterParams12.x`；`_CharacterParams12.y` 决定使用场景主光还是角色自定义主光。`_CharacterParams15.z` 出现在后续另一组方向性面部高光/边缘项中，不属于 SDF 主阈值。

Research Kit 的现有逆向记录和当前版 IL2CPP 类型信息共同表明，`_CharacterParams0..16` 由 `HGCharacterVolume` 打包。该 Volume 明确包含角色主光模式与方向、主光范围偏置、阴影染色、自动边缘光、面部边缘光、环境光以及附加光开关。现有记录已确认 12.y/z/w 的部分主光、附加光与环境曝光语义，但还没有把 11.w、12.x、15.z 精确映射回 Volume 字段和值域。它们不在材质和当前资源快照中，因此暂不把猜测值接入正式预览器。

当前版 runtime dump 将 `HGCharacterVolume.GetCharLightVolumeData` 定位在 `GameAssembly.dll + 0x09B693A0`。磁盘上的 `GameAssembly.dll` 代码段仍经过保护，必须在游戏运行并完成解密后读取进程内存。项目提供按 RVA 抓取和反汇编的小工具：

```powershell
python tools/inspect_process_rva.py `
  Arknights.exe GameAssembly.dll 0x09B693A0 `
  --bytes 4096 `
  --output GetCharLightVolumeData.bin
```

该工具只读取指定范围，不需要再次导出整个运行时模块。RVA 会随游戏版本变化，每次更新后必须以同版本 runtime dump 重新确认。

### CharInfo 默认角色光照 Profile

manifest 资源
`assets/beyond/dynamicassets/gameplay/prefabs/charinfo/charoverridevolumeprofile.asset`
位于 Bundle `main/818a3daf64683e6797e4ac34.ab`。按 container 导出
MonoBehaviour TypeTree 后，可确认 Profile 包含一个 `HGCharacterVolume` 组件。

该 Profile 中明确启用 override 的字段为：

| 字段 | 值 |
| --- | --- |
| `charMaxCubemap` | 外部 Cubemap 引用 |
| `charAmbientLightBaseIntensity` | `1.0` |
| `charAmbientLightCustomDir` | `(180, 0)` |
| `charAmbientLightDirIntensity` | `0.6` |
| `charAmbientLightDirParam` | `0.15` |

其余主光、阴影染色、自动边缘光、面部边缘光、天气预览和质量档字段虽然存在序列化默认值，但 `overrideState = 0`，不能把这些默认值直接当作该 Profile 的生效配置。这个结果补齐了角色信息界面的基础环境光输入，却仍不足以推导 `_CharacterParams0..16` 的完整运行时打包结果；场景 Volume 混合、全局默认值和运行时代码仍需分别验证。
