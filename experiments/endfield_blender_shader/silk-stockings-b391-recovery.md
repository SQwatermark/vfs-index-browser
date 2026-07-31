# CharacterNPR Silk Stockings b391 复刻边界

## 目标

本文记录终末地 1.4.4 `CharacterNPR` 丝袜分支中可由归档 Shader 直接确认的公式。
目标是为 Blender 材质复刻提供依据，不以视觉调参替代游戏公式。

当前佩丽卡缓存中启用丝袜的材质是：

```text
M_actor_pelica_cloth_04
```

GLB 保留的材质特征为：

```text
_NORMALMAP
_METALLICSPECGLOSSMAP
_SILK_STOCKINGS
_SPEC_RAMP_ON
```

同时未保留 DiffRamp、AlphaBlend、AlphaTest、Emission 等开关。按 1.4.4
`characternpr.shader` 的精确关键字分派，对应变体为：

```text
characternpr/Sub0_Pass0_Fragment_b391.hlsl
```

`b391` 文件头也列出了相同的关键字组合。

## 材质本质

丝袜不是额外的半透明网格，也不是 Alpha Blend 材质。Shader 保留皮肤 BaseMap，
在不透明表面上完成三项处理：

1. 根据视线与法线夹角计算由浅到深的丝袜覆盖色；
2. 根据角色浸润状态修改颜色、粗糙度和法线；
3. 在普通 GGX 高光之外叠加独立的各向异性高光瓣。

因此，Blender 中简单降低 Alpha 或把黑色 Principled BSDF 混到皮肤上都不等价。

## Shader 属性

1.4.4 `characternpr.shader` 在 127 至 141 行声明了完整属性及默认值：

| 属性 | Shader 默认值 | 作用 |
| --- | ---: | --- |
| `_SilkStockingsDryColor` | `(1, 1, 1, 1)` | 干燥状态对皮肤底色的乘色 |
| `_SilkStockingsWetColor` | `(1, 1, 1, 1)` | 浸润状态对皮肤底色的乘色 |
| `_SilkStockingsColor` | `(0, 0, 0, 1)` | 视角覆盖的边缘颜色；Alpha 参与覆盖指数 |
| `_SilkStockingsMinAffect` | `0.05` | 正视时的最低覆盖 |
| `_SilkStockingsMaxAffect` | `0.9` | 掠射角时的最高覆盖 |
| `_SilkStockingsAdvance` | `0` | 是否使用四通道丝袜 Mask |
| `_SilkStockingsAnisoDirection` | `0` | 非高级模式下的各向异性方向参数 |
| `_SilkStockingsMask` | white | 高级模式的四通道控制图 |
| `_SilkStockingsSpecularInt` | `5` | 丝袜专用高光强度 |
| `_SilkStockingsSpecularMinAtMinWetness` | `0` | 最干状态的高光下限 |
| `_SilkStockingsSpecularFalloff` | `0.8` | 覆盖度对各向异性强度的衰减 |
| `_SilkStockingsSpecularValue` | `2` | 丝袜高光半角向量偏移 |
| `_SilkStockingsRainWetMaskScale` | `0.7` | 浸润遮罩影响 |
| `_SilkStockingsAlbedoAffectType` | `0.5` | 浸润时透肉或压暗的分支参数 |

这些是 Shader 的缺省值，不等于所有材质实例的实际值。只有在确认材质没有覆盖某属性时，
才可以使用对应缺省值。

高级模式 Mask 的通道语义由 Shader 属性名直接给出：

| 通道 | 语义 |
| --- | --- |
| R | 各向异性高光强度 |
| G | 各向异性方向/锐利度 |
| B | 浸润状态下的光滑度 |
| A | 透肉覆盖度 |

## 独立研究的交叉验证

本地 `endfield_research_kit` 的详细研究记录对 Last Rite 的
`M_actor_lastrite_cloth_03` 做过独立的原始 Material、D3D11 变体和运行时消费者审计。
它确认：

- 该材质只因 `_SILK_STOCKINGS` 与控制变体不同；
- `_SilkStockingsAdvance = 0`，Mask PPtr 为空，高级分支不可达；
- 覆盖度来自 BaseMap Alpha；
- 丝袜第二高光瓣使用正交化切线、`roughness * (1 +/- direction)` 两轴，以及
  `normalize(shippedHalf + view * SpecularValue)`；
- 该材质实际覆盖了默认参数：
  `SpecularMinAtMinWetness = 1`、`SpecularFalloff = 0`、
  `SpecularInt = 0.1756`、`RainWetMaskScale = 0`。

这组值证明 Shader 默认值不能代替材质实例值。例如默认
`SpecularMinAtMinWetness = 0` 会让专用高光在完全干燥时归零，但 Last Rite 通过实例值
使该高光在干湿状态保持一致。佩丽卡也必须读取自己的原始 Material，不能套用 Last Rite
或 Shader 默认参数。

## 基础输入

`b391` 的基础材质采样为：

```hlsl
baseSample = BaseMap(uv)
baseColor = baseSample.rgb * BaseColor.rgb
metallicGloss = MetallicGlossMap(uv)

metallic = metallicGloss.r
shadowMask = metallicGloss.b
perceptualRoughness = 1 - metallicGloss.a
baseAlpha = baseSample.a * BaseColor.a
```

阴影侧颜色不是另一个贴图，而是由基础色经过 `_ShadowColorBrightness` 和
`_ShadowColorSaturation` 生成。丝袜分支会同时修改明面基础色和阴影侧基础色。

## 浸润状态

`b391` 将每角色数据中的四个 8 位通道解包为 `[0, 1]`，再组合世界高度浸润：

```hlsl
wetness = max(channelR, max(channelB, heightWetness * channelG))
```

其中 `heightWetness` 依赖：

- 角色/全局浸润高度；
- 当前片元世界空间高度；
- `_CharacterParams10`；
- 每对象缓冲区中的打包值。

Blender 导出的静态模型没有这些运行时缓冲。静态预览只能显式暴露一个
`Wetness` 输入，并默认设为 `0`；不能声称该常量复刻了游戏的动态浸润系统。

## 高级模式与普通模式

设：

```text
wet = wetness
alpha = BaseMap.a * BaseColor.a
specBase = SilkSpecularInt
         * lerp(SilkSpecularMinAtMinWetness, 1, wet)
```

高级模式：

```hlsl
mask = SilkStockingsMask(uv)
roughnessRaw = lerp(1 - MetallicGloss.a, 1 - mask.b, wet)
specIntensity = specBase * mask.r
anisoDirection = clamp(mask.g * 2 - 1, -0.95, 0.95)
coverage = saturate(lerp(alpha, mask.a, wet) + 1 - SilkColor.a)
```

普通模式：

```hlsl
roughnessRaw = 1 - MetallicGloss.a
specIntensity = specBase
anisoDirection =
    -lerp(SilkAnisoDirection, 0.5, saturate(alpha * 0.5))
coverage = saturate(alpha + 1 - SilkColor.a)
```

佩丽卡当前 GLB 没有携带丝袜 Mask，不能据此判断原材质一定没有 Mask。需要读取原始
Material 的 `_SilkStockingsAdvance` 和 `_SilkStockingsMask` 才能最终确认。若
`Advance == 0`，不应虚构一张 Mask 或把 BaseMap Alpha 当作 Mask A。

## 视角覆盖

设 `N` 为应用法线贴图后的世界空间单位法线，`V` 为指向相机的单位向量：

```hlsl
NdotV = saturate(dot(N, V))
edgePower = saturate(pow(1.05 - NdotV, 2 * coverage))
affect = lerp(MinAffect, MaxAffect, edgePower)
silkTint = lerp(DryColor.rgb, WetColor.rgb, wet)

litAlbedo = lerp(baseColor * silkTint, SilkColor.rgb, affect)
shadowAlbedo = lerp(shadowColor * silkTint, SilkColor.rgb, affect)
```

这说明“通透”来自正视时保留较多皮肤底色、掠射角逐渐趋向丝袜颜色，而不是表面透明。

## 各向异性高光

丝袜专用高光不是 Blender Principled 的一个经验性 `Anisotropic` 值。`b391`
显式计算第二个 NDF：

```hlsl
falloff = 1 - saturate(coverage * SilkSpecularFalloff)
direction = anisoDirection * falloff

alpha = max(roughness * roughness, 0.0078125)
alphaT = alpha * (1 - direction)
alphaB = alpha * (1 + direction)
alphaTB = alphaT * alphaB

Hsilk = normalize(Hmain + V * SilkSpecularValue)
v = float3(
    alphaB * dot(T, Hsilk),
    alphaT * dot(B, Hsilk),
    alphaTB * dot(N, Hsilk)
)

denominator = pow(dot(v, v), 2)
numerator = pow(alphaTB, 3)
silkNdf = denominator != numerator
    ? clamp(numerator / denominator, 0, 20)
    : 1

silkSpecular = specIntensity * silkNdf
```

该项与普通 GGX 高光相加后，再共同乘以 SpecRamp、角色主光和
`_CharacterParams13.w`。因此直接使用 Principled BSDF 的各向异性参数，只能算近似，
无法表达偏移后的 `Hsilk` 和独立 NDF。

## 当前 Blender 近似与原 Shader 的差异

当前 `create_silk_stockings_group()` 明确是近似实现，至少存在以下差异：

- `Dry Tint` 使用手工颜色，而游戏 Shader 默认是白色，实际值应来自 Material；
- 使用一个额外 `Stocking Coverage` 再混合皮肤，原 Shader 没有这一步；
- 无 Mask 时仍使用虚构的 Mask 默认值；
- 将 Mask A 当作外层覆盖权重，原 Shader 只在高级模式和浸润插值中读取它；
- 把 Mask G 直接接到 Principled 的旋转，原 Shader 将其映射到 `[-0.95, 0.95]`
  后改变 `alphaT/alphaB`；
- 使用 Principled 各向异性代替独立、偏移半角向量的丝袜 NDF；
- 添加了 Shader 中不存在的 `Ambient Floor` 和 `Sheen Weight`；
- 湿润粗糙度使用简单插值，没有复刻雨痕、高度浸润和法线变化；
- 没有同时处理阴影侧反照率；
- 没有接入 SpecRamp、角色专用环境光、场景主光和屏幕空间阴影。

这些节点可以继续作为易用预览后端，但不能命名或描述为原 Shader 复刻。

此外，当前导出链在生成 `previewPbr` 时预先把 `MaxAffect` 烘进
`baseColorFactor`：

```text
baseColorFactor = lerp(_BaseColor.rgb, _SilkStockingsColor.rgb, MaxAffect)
```

这不是 `b391` 的计算。原 Shader 的 affect 随 `NdotV` 和 coverage 逐片元变化。
GLB 中佩丽卡材质的 `baseColorFactor = 0.1` 正是黑色与 `MaxAffect = 0.9`
预混后的结果。随后 Blender 丝袜节点虽然改从 BaseMap 取色以避免再次压暗，却同时丢失了
原始 `_BaseColor` 乘色。

精确后端必须保留原始 `_BaseColor` 和全部丝袜参数，不能把已经预烘的 PBR
`baseColorFactor` 当作源数据。预烘值可以继续服务通用 glTF 预览，但必须与原始材质参数
并存。

## Blender 可复刻层级

### 可在普通 Shader Nodes 中精确实现

- BaseMap、BaseColor、MetallicGloss 的通道拆分；
- 普通/高级模式的 coverage、roughnessRaw、specIntensity 和 anisoDirection；
- 干湿颜色插值；
- `NdotV` 视角覆盖公式；
- 明面与阴影侧反照率的丝袜调色；
- 将 Wetness 作为显式静态输入。

### 需要手写 OSL 或显式节点网络

- 偏移半角向量的丝袜 NDF；
- 与普通 GGX、SpecRamp 和主光的精确合成。

OSL 只适用于 Cycles，不能直接作为 Eevee/材质预览的通用后端。普通 Principled
无法精确替代这套 NDF。

### 缺少运行时数据时不能完整复刻

- 每角色浸润打包值与高度浸润；
- 场景主光和角色主光的 HGRP 混合；
- 屏幕空间阴影遮罩；
- Irradiance Volume；
- 角色专用 CharacterVolume 参数；
- 附加灯光列表、雾和曝光。

## 后续实现顺序

1. 让 ModelDocument 保留完整丝袜属性和 `_SilkStockingsAdvance`，不要只保留
   `color/maxAffect`。
2. 对原始 Material 验证佩丽卡是否覆盖 Shader 默认值，确认是否真正启用高级 Mask。
3. 新建一个默认关闭的“公式验证”节点组，仅实现可精确表达的反照率链；不替换现有后端。
4. 使用固定、显式的 `N/V/T/B/wetness` 输入对照 `b391` 数值，而不是先看最终截图调色。
5. 数值测试通过后，再分别设计 Cycles OSL 后端和 Eevee 近似后端，并在命名上明确区分。
