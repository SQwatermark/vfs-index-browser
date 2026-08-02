# Humanoid 动画恢复研究

本文记录终末地 Humanoid 动画从 AnimeStudio 导出、离线求解到网页播放的验证过程。目标是恢复游戏使用的动画语义，而不是让两套基于相同假设的实现彼此吻合。

## 当前链路

1. AnimeStudio 从 `AnimationClip` 导出普通 Transform 曲线、Humanoid 属性和 ACL 采样结果。
2. AnimeStudio 按终末地的 101-muscle ABI 为 Humanoid 属性命名。
3. Python 根据模型携带的 Avatar 轴、限制、参考四元数和 twist factor，将可用的身体 muscle 曲线烘焙为骨骼局部旋转。
4. 普通 Transform 曲线与烘焙轨道统一进入 `EndfieldModelAnimation`，供 Three.js 和 Blender 使用。

批量导出现在逐项隔离片段导出和模型绑定错误，成功动画仍会进入同一 GLB/Blender 文件，
失败动画返回结构化诊断。真实 Liino 样本中 79 个候选有 59 个成功、20 个失败。完全静态
的 Transform 轨道会无损收缩为一个关键帧，Blender 文件使用原生压缩保存。

这仍未解决密集采样的根因：同一批成功动画仍有约 457 万个采样点。下一阶段应优先恢复
Unity 原始稀疏关键帧与切线；Humanoid 非线性烘焙曲线再使用可配置、可验证误差上限的
约简。单纯使用 Blender 压缩只能减少磁盘和下载体积，不能减少内部 FCurve 数量。

Blender 4.4 会把一个 glTF 动画导入为多槽 Action：模型根、主骨架、附件和其他动画对象
各有独立 Slot。若活动对象是 glTF 根节点，Action Editor 只显示该根节点的整体变换，容易
误判为骨骼动画丢失。导出器现在取消导入后的全选状态，并将骨骼数最多的 Armature 设为
唯一活动对象。Liino 批量产物中，普通动作的 `OBBip001` 槽约含 2443 条 FCurve；默认
动作从起始帧到中间帧可观测到 237 根 PoseBone 发生变化。纯镜头动作
`A_actor_liino_battle_skill_ult_cam` 没有骨骼 Slot，不能作为骨骼播放验证样本。

## 根因结论

终末地没有沿用标准 Unity 的 95-muscle 序列。其 `m_IndexArray` 固定为 206 项：

- 0..41：Motion、Root 和四肢 IK 属性；
- 42..142：101 个 muscle 属性；
- 143..205：未使用尾部。

终末地在左右腿的标准 muscle 序列中插入了六个自由度：

| Muscle slot | 序列化 attribute | 含义 | 骨骼 selector |
| --- | --- | --- | --- |
| 28 | 70 | Left Foot Twist Roll | LeftFoot 0 |
| 30 | 72 | Left Toes Left-Right | LeftToes 2 |
| 31 | 73 | Left Toes Twist Roll | LeftToes 0 |
| 39 | 81 | Right Foot Twist Roll | RightFoot 0 |
| 41 | 83 | Right Toes Left-Right | RightToes 2 |
| 42 | 84 | Right Toes Twist Roll | RightToes 0 |

因此，插入点之后的右腿、手臂和手指属性会相对标准 Unity 顺序偏移 3 或 6 项。此前 AnimeStudio 按标准顺序命名属性，导致腿部数值被解释为手臂数值、手臂数值被解释为手指数值，这是四肢大幅扭曲的直接原因。

当前实现已经：

- 在 AnimeStudio 中按游戏类型选择标准 Unity 或终末地属性表；
- 保留全部 101 个终末地 muscle 的稳定名称；
- 在 Python 求解器中接入六个额外腿部 selector；
- 将网页动画缓存修订号提升，避免浏览器继续复用旧语义的动画响应。

## 已排除的问题

### RootMotionBuffer 不是遗漏的第二套动画

`RootMotionBufferData` 解压后包含 28 条轨道，逐帧、逐分量与 Humanoid 流开头的 Motion、Root、左右脚 T/Q 完全一致。两段样本的最大误差和 RMS 均为 0，因此它是重复存储，不是缺失姿态来源。

### `m_IndexArray` 的尾部不是额外 muscle

属性 143..205 是未使用尾部。`m_ValueArrayDelta` 与已导出通道的首尾值严格对应，属于端点/循环元数据，不携带另一套逐帧骨骼变换；当前样本的 `m_ValueArrayReferencePose` 为空。

### Three.js 没有再次扭曲已经生成的姿态

将生产动画和同源 Unity oracle 应用到模型层级后，以 `Bip001` 为参考比较 Humanoid 骨骼世界矩阵，主要骨骼的位置与旋转误差接近数值噪声。浏览器忠实播放了离线求解结果，问题发生在更早的属性语义层。

### 模型蒙皮没有发现基础索引损坏

已检查 Pelica 模型所有 LOD：`WEIGHTS_0` 权重和接近 1，`JOINTS_0` 均位于对应 skin 的 joint 范围内，未发现越界、空映射或明显权重损坏。

## 当前验证结果

修正 ABI 后，301345 的 73 个 Humanoid 属性名按预期变化。与旧结果相比，关键骨骼的局部旋转差异达到：

- 手部最高约 170 度；
- 上臂最高约 96 度；
- 前臂最高约 71 度；
- 右脚最高约 41 度。

这些差异与旧预览中四肢扭曲的量级一致。补齐六个扩展 selector 后，260637 可将 55 条身体曲线烘焙为 21 条骨骼旋转；未消费的身体项来自模型没有对应 Humanoid 节点的眼睛和下颌，而不是腿部属性遗漏。

### 修正 ABI 后的 Unity oracle 对照

旧 oracle 输入沿用了错位的标准 95-muscle 名称，因此不能用于评价修正后的求解器。使用修正后的 101-muscle 名称重新生成输入，并由 Unity 6000.2.5f1 的 `AnimationClipPlayable` 求值后，在 0、0.25、0.5、1、1.5 和 2 秒采样点得到：

- 脊柱、胸、颈、头、双肩、双臂、双手和双腿的世界旋转误差约为 `0.00001` 到 `0.00006` 度；
- 对应世界位置误差约为 `0.0000000` 到 `0.0000004`；
- 左右脚趾仍有约 `0.006` 到 `2.20` 度差异，来源是终末地新增的脚趾左右摆动和扭转自由度，原版 Unity oracle 不识别这两个属性。

因此，标准 Humanoid 身体通道的静态求解已经与 Unity 本体对齐；脚和脚趾扩展通道则由终末地数据定义和网页视觉结果共同验证。

### 剩余浮点曲线的组成

260637 当前报告的 96 条未烘焙浮点曲线可以完整分类：

- 42 条 Motion、Root、双手和双脚 IK 的 T/Q 通道；
- 40 条手指 muscle；
- 6 条眼睛和下颌 muscle，当前模型的 Avatar 没有对应映射；
- 8 条 Animator 自定义浮点属性。

其中手指骨已经存在普通 Transform 旋转轨道，不应再把手指 muscle 重复烘焙到同一骨骼。这里的“未烘焙”不等于预览缺少 96 条骨骼动画。

### Motion/Root 到 HumanPose Body

扩展 Unity oracle 输出 `HumanPose.bodyPosition/bodyRotation` 后，可以确认资源通道到 Body 的关系为：

```text
bodyRotation = inverse(MotionQ) * RootQ
bodyPosition = rotate(inverse(MotionQ), RootT - MotionT)
```

在 301345 的全部 561 帧上，位置重建最大误差约为 `2.1e-7`，旋转重建最大误差约为 `3.5e-5` 度。这一层已经解明。

Body 不是模型中的普通 Hips 变换。Unity 会结合当前全身姿态、Avatar 人体比例及 `m_HumanBoneMass` 质心权重，把 Body 反解为 Hips (`Bip001`)。直接用 Body 首帧相对增量驱动 Hips，在该样本上会产生：

- 平移误差中位数约 `0.0166`，最大约 `0.0552`；
- 旋转误差中位数约 `14.56` 度，最大约 `22.65` 度。

因此不能把已解出的 Body 通道直接写成 `Bip001` 或模型根节点轨道。

### 受控 HumanPose 探针

为排除动画曲线解析和坐标转换的干扰，Unity oracle 增加了直接调用
`HumanPoseHandler.SetHumanPose` 的探针模式。探针固定零 muscle 姿态，分别改变
Body 三轴平移、三轴旋转；随后固定 Body，对 95 个标准 Unity muscle 分别施加
`-0.25/+0.25` 扰动，共得到 203 个样本。

在 muscle 姿态固定时，Body 到 Hips 是严格的刚体关系：

```text
hipsRotation = bodyRotation * poseOffsetRotation
hipsPosition = bodyPosition * humanScale
             + rotate(bodyRotation, poseOffsetPosition)
```

六组 Body 旋转探针的 Hips 旋转误差为浮点零，位置误差约为 `4e-8` 到
`1.4e-7`。`poseOffsetPosition/Rotation` 只由 muscles 和 Avatar 决定。因此问题已从
“Body 如何变成 Hips”进一步缩小为“当前 muscle 姿态如何产生两个 pose offset”。

位置 offset 的证据已经较充分：按 `m_HumanBoneMass` 对各人体骨段中点加权，
在固定 Body 的单 muscle 探针中，估算质心的最大漂移约 `0.8 mm`；在 301345
真实动画全部 561 帧中，中位误差约 `1.2 mm`，最大约 `2.8 mm`。残差主要来自
头、手、脚趾等末端骨段的虚拟端点，以及 Unity 对骨段中心的精确定义尚未完全
复刻。

旋转 offset 不是全身质量点云的普通最佳拟合旋转。探针显示：

- 脊柱、胸和上胸 muscle 会显著改变 Hips 补偿旋转；
- 肩部会产生较小的旋转补偿；
- 腿、手臂、颈和头主要改变 Hips 平移，不改变其补偿旋转。

单 muscle 探针一度可把旋转近似到中位 `0.36` 度、P95 `1.12` 度，但 Unity
官方文档给出了精确定义：上方向由左右髋关节中点指向左右肩关节中点，左右方向
是髋部和肩部左右向量的平均，前方向由二者叉乘得到。这里的髋关节对应左右
`UpperLeg` 起点，肩关节对应左右 `UpperArm` 起点。参考：
[Unity HumanPose.bodyRotation](https://docs.unity3d.com/2017.4/Documentation/ScriptReference/HumanPose-bodyRotation.html)。

按照该定义计算当前骨架的 Body frame，并用 Avatar `m_RootX.q` 作为 T-Pose
参考 frame，在受控探针中的最大误差约 `0.00019` 度；在 301345 全部 561 帧中，
误差中位数为浮点零、P95 约 `0.0076` 度、最大约 `0.016` 度。旋转方向已经解明，
不再需要经验拟合。

生产求解可按以下形式组织：

```text
physicalBodyRotation = bodyRotation * avatarRootXRotation
rootCorrection = physicalBodyRotation * inverse(currentBodyFrame)
```

先用 `rootCorrection` 旋转整个人体姿态，再用骨段质量中心把质心平移到 Body
指定的位置。位置求解应使用 Avatar `m_RootX.t` 校准骨段中点模型的参考误差，
而不是从某段动画首帧拟合偏移。

### 生产求解器回归

上述公式现已接入正式模型预览和动画导出路径。内部 `bake_humanoid` 参数仍用于
隔离底层绑定单元测试，但服务端统一启用。模型文档在 skeleton 级保存
`m_RootX`、`m_Scale`、人体骨段质量及参考质心偏移，在 node/bone 级保存 Human
bone 身份和 Muscle Referential。这里必须区分两类集合：skin joint 只表示网格
蒙皮引用的节点，Humanoid bones 还包含可能不参与蒙皮的骨骼。Pelica 的 Hips
节点 `Bip001` 就不在 skin joint 列表中，因此求解器使用完整 `document.nodes`
执行层级正向运动学，不能只遍历 `skeleton.bones`。

使用 `tools/research/validate_humanoid_body_tracks.py` 对 Pelica 301345 动画全部
561 帧与 Unity 6000.2.5f1 oracle 比较，得到：

- Hips 平移误差中位数约 `2.08 mm`，P95 约 `2.34 mm`，最大约 `2.53 mm`；
- Hips 旋转误差中位数为浮点零，P95 约 `0.026` 度，最大约 `0.034` 度。

旋转残差已经可以忽略；平移残差符合当前以真实骨骼起点代替末端虚拟骨段端点
时的毫米级误差预期。显式 Transform 曲线仍拥有更高优先级，Body 求解器不会
覆盖资源中已经直接提供的 Hips 位移或旋转。

还验证了 `HumanPoseHandler.GetInternalHumanPose`：当 handler 由 Avatar 和骨架根
创建时，其 Body 输出与 `GetHumanPose` 逐位一致，真实动画和受控探针均如此。
“内部 HumanPose”不会暴露一个更接近 Hips 的中间 Root，因此不能借它绕过
Body 到骨架的转换。

### 完整节点树与 NPC AvatarMesh

Humanoid 骨骼集合不能等同于 skin joint 集合。JSSPSI 的
`Bip001_L_Thigh`、`Bip001_L_Calf`、`Bip001_R_Thigh` 和
`Bip001_R_Calf` 都存在于完整 prefab 节点树并带有 Humanoid 元数据，但不在模型
文档的 skin joint 子集中。旧求解器只扫描 `skeleton.bones`，因此脚部普通
Transform 轨道可以播放，大腿和小腿的 muscle 曲线却没有被烘焙。改为复用完整
节点树中的 Humanoid 映射后，动画 308852 的 Humanoid 输出由 19 条增加到 23 条
骨骼轨道，左右大腿和小腿均得到 101 帧的变化旋转。

NPC AvatarMesh 是另一类遗漏。模型构建链原本已经从源 FBX 导出 Avatar，并用其
`m_AvatarSkeleton`、`m_DefaultPose` 和 `m_TOS` 重建完整骨架，但没有把同一 Avatar
中的 `m_HumanDescription` 与 Muscle Referential 附着到 ModelDocument。因此
动画绑定器只能识别普通 Transform 曲线，身体 muscle 曲线全部留在 unsupported
诊断中。现在 NPC 模型构建完成骨架后会复用 `annotate_humanoid_bones`；Andrew
模型配合动画 108135 的输出由 98 条增加到 121 条轨道，脊柱、头部、左右大腿和
小腿都获得 574 帧的变化旋转，未处理浮点曲线由 145 条降至 76 条。

这两项修复没有增加 NPC 或干员专用动画格式。模型层负责提供稳定节点身份和
Avatar 人形语义，动画层继续通过同一套 Humanoid 烘焙与 Transform 绑定流程消费。

## 尚未覆盖的层

以下内容与本次 ABI 修复相互独立，仍需分别验证：

1. Motion 与绝对 Root 的角色位移和 `Bip001` Body 语义。
2. 手指普通 Transform 轨道与 Humanoid 表示的来源关系，以及不同资源是否始终同时提供二者。
3. Hand/Foot IK goal、权重和游戏侧 IK/约束回调。
4. AnimatorController 的层、混合、打断、AvatarMask 和事件。
5. 普通 Transform overlay 与 Humanoid 输出的完整覆盖顺序。
6. 游戏运行时 Grounding、secondary motion 和其他程序化姿态。

这些功能不能用“让画面看起来合理”的方式猜测。每一层都需要原始数据、反编译语义或可信运行时样本作为依据。

## 下一步顺序

1. 补齐末端骨段的虚拟端点定义，进一步收敛当前毫米级质心误差。
2. 增加第二个不同体型角色的 Unity oracle，验证 Avatar profile 的通用性。
3. 抽样确认不同角色和 NPC 是否都同时提供手指 Transform 轨道，决定是否需要手指 muscle 回退路径。
4. 再依次处理 IK、AnimatorController 和游戏运行时程序化姿态。

## 本地验证材料

相关实验产物位于 `data/local-validation/`，大文件仅用于本地研究，不应直接提交：

- `animation-301345-source-v12.json`
- `animation-301345-source-endfield-abi.json`
- `animation-301345-production-endfield-abi.json`
- `animation-301345-oracle-endfield-abi-input.json`
- `animation-301345-oracle-endfield-abi-output.json`
- `animation-301345-pose-probe-input.json`
- `animation-301345-pose-probe-output.json`
- `animation-260637-source-v12.json`
- `animation-260637-source-endfield-abi.json`
- `animation-260637-production-endfield-abi-full.json`
- `pelica-model-v4.json`
- `pelica-model-v4-geometry.bin`

辅助分析脚本位于 `tools/research/`：

- `analyze_humanoid_root_motion.py`
- `analyze_humanoid_body.py`
- `compare_humanoid_pose.py`
- `validate_humanoid_body_tracks.py`
