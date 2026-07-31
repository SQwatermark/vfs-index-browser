# 研究文档索引

- [manifest-hgmmap-format.md](manifest-hgmmap-format.md)：BundleManifest 的头部、Bundle、依赖数组和 AssetInfo 路径格式。
- [jsondata-binary-json-format.md](jsondata-binary-json-format.md)：JsonData 中非文本 JSON 的格式分类和探测结果。
- [memorypack-decoder-progress-2026-07-29.md](memorypack-decoder-progress-2026-07-29.md)：MemoryPack schema 提取与解码进展。
- [shiyume-repositories.md](shiyume-repositories.md)：模型恢复、材质、Shader、动画和物理骨骼仓库的结构性参考。
- [endfield-rendering.md](endfield-rendering.md)：公开访谈、Shader 与真实材质共同支持的 PBR/NPR 混合渲染路线。
- [npc-avatar-model-recovery.md](npc-avatar-model-recovery.md)：普通 NPC 的 AvatarMesh 组合结构、StringPathHash 路径解析与 Deathgirl 样本验证。
- [wwise-audio-and-npc-web-preview.md](wwise-audio-and-npc-web-preview.md)：非对话音频的 Wwise 关系索引、音乐聚合结构，以及 AvatarMesh NPC 接入网页预览的方案。
- [effect-resource-recovery.md](effect-resource-recovery.md)：特效 Prefab、VFX 配置、粒子、Playable、VAT 与运行时渲染输入的组合边界和恢复路线。

Shader 的公式级实验暂位于 `experiments/endfield_blender_shader/`；其中面部与丝袜结论已经
汇入[资源恢复整合路线](../design/integrated-roadmap.md)，实验目录和工具职责见
[文档与工具导航](../README.md)。

文档中的“已确认”应有样本、计数或脚本验证支撑；推测内容需要显式标注。

研究结论如何进入实现、各工作线的依赖和优先级见
[资源恢复整合路线](../design/integrated-roadmap.md)。研究文档保存证据和未知项，
设计文档负责稳定接口与执行计划，两者不互相替代。
