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
