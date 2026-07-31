# 特效资源恢复边界与路线

## 目标

本文记录终末地特效资源的静态结构，以及网页预览和 Blender 导出应采用的恢复边界。
这里的“特效”包括战斗技能、角色界面、场景和通用反馈效果，不把某个 `.prefab` 或
`.asset` 文件误认为完整特效。

## Manifest 资源分布

当前游戏 Manifest 中路径含 `effect` 的资源约 2.1 万项，主要类型如下：

| 类型 | 作用 | 已观察规模 |
| --- | --- | ---: |
| `.prefab` | 层级、组件、Renderer、粒子系统和引用入口 | 约 1.14 万 |
| `.png` / `.tga` | 粒子、遮罩、噪声、渐变和序列帧贴图 | 约 4,800 |
| `.asset` | VFX 配置、运行时参数或其他 MonoBehaviour 数据 | 约 1,900 |
| `.playable` / `.anim` | Timeline、片段时序和属性动画 | 约 1,500 |
| `.mat` / `.shader` | 材质参数、渲染队列、关键字和 Shader 程序 | 约 560 |
| `.fbx` / `.exr` | 特效网格和 VAT 的位置、旋转数据 | 数百项 |

关键目录包括：

- `assets/beyond/dynamicassets/gameplay/effects/prefabs/`：运行时特效入口；
- `assets/beyond/dynamicassets/gameplay/effects/vfx/`：VFX 配置资产；
- `assets/beyond/arts/effects/`：网格、材质、Shader 和贴图；
- `assets/beyond/arts/effects/commonassets/vat/`：Fluid、Rigid、SoftBody VAT 数据。

Manifest 记录中的数百字节大小只是入口对象本身的序列化大小。实际内容通过 PPtr、
Bundle 依赖和运行时组件分散在多个资源中，不能据此判断特效很小或内容缺失。

## 已确认的组合关系

一个可播放特效通常需要同时恢复以下层次：

1. Prefab 的 GameObject/Transform 层级与组件启用状态；
2. ParticleSystem、Renderer 或项目自定义 MonoBehaviour 的序列化参数；
3. Material 的 Shader、关键字、渲染队列、混合和深度状态；
4. Texture2D、Mesh、FBX 与 VAT EXR 等视觉输入；
5. AnimationClip/Playable 的时间、属性曲线和对象绑定；
6. 游戏运行时写入的全局参数、角色索引、场景深度/颜色等帧资源。

现有 `endfield_research_kit` 的独立研究也证明，部分界面特效由单独的 Effect Prefab、
Timeline 和材质共同驱动；粒子 Renderer 即使带有实时阴影标记，其 Shader 没有
`ShadowCaster` pass 时仍不能写入角色阴影图。这说明组件标志、材质能力和渲染管线
必须联合解释。

## 与模型恢复管线的关系

特效中的 Mesh、Material、Texture 和 Transform 可以复用现有 AnimeStudio 对象导出、
Manifest 依赖闭包、ModelDocument 材质语义和 Blender Shader 后端，但特效本身不应伪装
成角色模型：

- 静态网格和材质继续使用已有解析器；
- Prefab 引用图继续使用稳定的 manifest 资产身份；
- 动画沿用“基础资源与独立时序数据分离”的设计；
- 特效需要独立的 EffectDocument，表达粒子模块、发射器、时序、事件和运行时输入；
- 浏览器和 Blender 都消费 EffectDocument，不分别发明解释规则。

EffectDocument 目前只应先定义已验证字段。未知 MonoBehaviour、Shader 全局量或运行时事件
必须保留来源和诊断并停止对应分支，不能降级成看似成功的白色粒子。

## 推荐实现顺序

1. **对象清单**：对代表性 Prefab 导出完整对象类型、PathID、container 和 PPtr 依赖图。
2. **静态预览**：恢复 MeshRenderer/ParticleSystemRenderer 的网格、材质和贴图，先显示单帧。
3. **粒子模块**：映射 Unity 标准 ParticleSystem 的 emission、shape、lifetime、size、color、velocity。
4. **时序绑定**：解析 AnimationClip/Playable，按稳定对象身份绑定启用状态和材质参数曲线。
5. **VAT**：识别 EXR 通道、帧数、顶点顺序及材质采样参数，再在网页和 Blender 共用结果。
6. **自定义运行时组件**：结合 TypeTree、IL2CPP 和运行时抓取逐类补齐，严格记录置信度。
7. **渲染逼近**：按 Shader 家族实现 Blender/网页后端；依赖场景颜色、深度或 MRT 的分支明确标注。

第一阶段的验收标准不是“看起来有点像”，而是同一个样本的对象数、组件数、依赖引用、
材质槽和纹理输入均可追溯，并且未知字段不会被静默丢弃。

## 当前阻塞

远程游戏盘当前未挂载，无法继续读取代表性 Prefab 和 VFX `.asset` 的对象导出结果。
待数据盘恢复后，应优先固定三个样本：标准 ParticleSystem、带 Playable 的角色技能特效、
带 VAT 的网格特效。它们可以覆盖三条主要恢复路线，避免在单一 Shader 上过早深挖。
