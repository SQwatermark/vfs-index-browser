# 终末地面部骨骼形变动画解析

## 问题现象

剧情目录下的 `morphanim/*.anim` 用普通 AnimationClip 流程解析时，可能得到数百条
`Transform.localPosition` 曲线，但所有路径哈希均无法绑定到角色模型，最终表现为
“0 条轨道，若干绑定诊断”。

这些曲线并非角色骨骼动画。游戏运行时会创建 `FacialMorphCtrlGO` 等临时控制器，
`.anim` 仅将控制值写入这些对象的 `localPosition.x`。控制器不属于角色 prefab，
因此不能通过补充模型路径哈希解决。

## 完整数据链

以 `dlgtl_e11m1_10_sub_1_npc_chr_0036_jsspsi_0` 为例：

1. `morphanim/*.anim`：Unity 动画载体，不直接描述脸部骨骼。
2. 同名 `morphanimso/*.asset`：`SkeletalMorphAnimSO`，保存有语义的控制器名称和
   `AnimationCurve`，本例实际只有四条眼部控制曲线。
3. `data_facemorph_avatar_jsspsi.asset`：角色专属 `SkeletalMorphAvatarDataSO`，保存
   91 根脸部骨骼的基准姿态和 269 个控制器到骨骼目标姿态的映射。
4. 角色 postmodel：已经包含上述脸部骨骼，无需额外拼装隐藏脸模。

角色映射中的 `SerializeReference` 内容不能由当前 AnimeStudio TypeTree 文本正确展开，
但原始 MonoBehaviour 数据可依据 IL2CPP 类型定义严格读取。当前解析器要求托管引用
数量、RID、类型、控制器编号、骨骼编号和文件尾全部对齐，格式变化时会直接报错。

## 当前实现

`skeletal_morph.py` 负责：

- 解析 `SkeletalMorphAnimSO` 和 `SkeletalMorphAvatarDataSO` 原始数据；
- 根据模型路径推导角色专属脸部映射，并按逻辑路径配对同名 `morphanimso`；
- 对 Unity AnimationCurve 进行 30 FPS Hermite 采样；
- 将控制器权重与角色基准姿态、目标姿态组合，生成现有
  `EndfieldModelAnimation` 可直接播放的平移、旋转、缩放轨道。
- MorphAvatar 的基准姿态仅用于计算目标差值；输出以实际模型绑定姿势为基准，避免
  prefab 覆盖或模型归一化导致动画首帧跳变。

服务端仅对 `/morphanim/*.anim` 启用该流程，普通 Transform 动画与 Humanoid 动画不受
影响。首次访问需要导出两个原始 MonoBehaviour，随后使用内容身份缓存。

## 已知边界

### BlendShape 与 LOD

部分眼部控制器同时驱动面部骨骼和眉眼网格的 BlendShape。若只恢复骨骼轨道，画面会出现眼皮已经运动但原始眼部几何仍然可见的重影。当前流程会：

- 从 Unity Mesh 的 `m_Shapes` 导出 ModelDocument `blendShapes`；
- 将每个单帧 Shape 转为 GLB Morph Target，并保留目标名称；
- 读取 `blendShapeMorphHashMap`，把语义控制器曲线转换为命名的 `blendShapeWeight` 轨道；
- 当前端播放外置动画时，按 `morphTargetDictionary` 解析名称并驱动对应权重；
- 当模型缺少 LODGroup 元数据时，从 `_lod0`、`_lod1` 等节点名推导预览 LOD，只渲染并绑定 LOD0。

多帧 Unity BlendShape 会在 GLB 预览中使用该通道的最终帧；若后续样本依赖中间帧，需要再补充 Unity 权重区间到多个 glTF Morph Target 的分段换算。

- 当前只支持非加算型面部动画和非加权切线；遇到其他格式会明确报错。
- 控制器到脸部骨骼的映射已经支持。
- 少量控制器用于 Shader 参数或眉部 BlendShape；当前播放器无法应用时会保留诊断，
  不会将其误报为已恢复的骨骼轨道。
- 当前网页动画接口已接入该流程；带面部动画的 GLB/Blend 导出仍需复用同一份已绑定
  动画结果，不能重新走普通 AnimationClip 绑定。

当前实现仍属于基础设施阶段：空的 MorphAnimSO、纯 Shader 参数表情、跨 LOD 的
BlendShape 绑定和播放器端目标解析需要在全量样本审计后分别处理，不能仅凭单个角色
样本宣称表情动画已经完整恢复。

## 验证样本

模型：`chr_0036_jsspsi_postmodel.prefab`，Manifest 资源索引 `221407`。

动画：`dlgtl_e11m1_10_sub_1_npc_chr_0036_jsspsi_0.anim`，Manifest 资源索引
`215234`。

修复前结果为 0 条轨道和 268 个未解析路径哈希；修复后识别四个语义控制器、32 根
受影响脸部骨骼，并生成 96 条模型动画轨道。
