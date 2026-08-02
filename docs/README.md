# 文档与工具导航

## 从哪里开始

1. [系统架构](design/architecture.md)：VFS、manifest、容器解析和聚合资源的总体数据流。
2. [当前工作线与待办](design/current-workstreams.md)：主线、支线、已完成能力和各方向待办。
3. [战斗系统反推计划](research/combat-system-reversing-plan.md)：当前主线的证据、阶段与验收方式。
4. [资源恢复整合路线](design/integrated-roadmap.md)：资源工具形成过程中的架构与接口背景。
5. [组合模型恢复](design/model-recovery.md)：ModelDocument、GLB、动画和 Blender 的详细链路。
6. [角色材质管线](design/material-pipeline.md)：Prefab 与 AvatarMesh 共享的材质恢复流程。
7. [音频语义索引](design/audio-index.md)：AudioDialog 虚拟目录和其他 Wwise 音频的组织方式。
8. [Wwise 关系索引](design/wwise-index.md)：PCK/HIRC 数据结构、网页虚拟目录和构建方式。

## 文档分工

`docs/design/` 保存当前采用或计划采用的稳定接口与设计决策。

`docs/research/` 保存样本、逆向证据、已确认结论和未知边界。研究结论只有在进入设计文档、
schema、测试或生产代码后，才算正式进入实现路线。

`docs/reference/` 保存外部格式或实现参考，不代表项目已支持。

`experiments/` 保存可复现实验。实验脚本和说明有长期价值；`.blend`、PNG、缓存和临时
导出只是验证产物，不应成为生产代码的运行时依赖。

## 生产模块

| 模块 | 作用 |
| --- | --- |
| `server.py` | HTTP、VFS 查询、按需读取和容器适配编排 |
| `manifest_index.py` | `manifest.hgmmap` 解析和逻辑目录索引 |
| `sparkbuffer.py` | TableCfg/SparkBuffer 解码 |
| `model_document.py` | 完整模型领域文档及语义校验 |
| `animestudio_model.py` | AnimeStudio Unity 对象适配和共享几何/材质构建 |
| `npc_avatar_config.py` | AvatarMesh TypeTree、路径哈希引用和配置摘要 |
| `npc_avatar_resources.py` | AvatarMesh 到 manifest 具体资源与 Bundle 的严格解析 |
| `avatar_mesh_snapshot.py` | AvatarMesh 所需 AnimeStudio 对象与纹理导出契约 |
| `npc_avatar_model.py` | AvatarMesh 通用 NPC 适配 |
| `gltf_export.py` | ModelDocument 到自包含 GLB 的派生导出 |
| `blender_materials.py` | Blender 材质节点后端 |
| `shader_binary_packages.py` | AnimeStudio 编译 Shader 包的严格读取与关键词查询 |
| `dxbc.py` | DXBC 容器、Shader Stage 和 Shader Model 检查 |
| `character_lighting.py` | 版本化角色光照输入 |
| `model_animation.py` | 独立动画文档和节点绑定 |
| `public/` | 资源浏览和模型/媒体预览前端 |
| `audio_package.py` | PCK 中 Bank 与 Media 的物理边界解析 |
| `wwise_hirc.py` | SoundBank HIRC 对象与关系解析 |
| `wwise_store.py` | Wwise SQLite 索引和虚拟目录查询 |
| `schemas/` | 稳定领域文档和已知二进制配置 schema |

## 常用工具

### 资源与格式

- `tools/parse_hgmmap.py`：解析并验证 Bundle manifest。
- `tools/extract_indexed_file.py`：按 VFS 索引提取单文件。
- `tools/scan_jsondata_formats.py`：统计二进制 JSON 的编码类型。
- `tools/probe_binary_json.py`：探测单个二进制 JSON。
- `tools/extract_memorypack_schema.py`：从 IL2CPP 信息提取 MemoryPack schema。
- `tools/decode_memorypack_json.py`：按已知 schema 解码 MemoryPack。
- `tools/build_wwise_index.py`：直接从本地 VFS 索引构建全部可用 PCK 的 Wwise 索引。
- `tools/index_wwise_pck.py`：索引一个已提取的 PCK。

### 模型与材质

- `tools/build_model_document.py`：从 AnimeStudio 导出构建 ModelDocument。
- `tools/inspect_npc_avatar_mesh.py`：检查 AvatarMesh 部件、材质和路径引用。
- `tools/build_npc_avatar_preview.py`：构建 NPC ModelDocument/GLB 验证样本。
- `tools/build_character_lighting.py`：生成版本化角色光照输入。
- `tools/select_shader_variants.py`：按材质开关筛选 HLSL Shader 变体。
- `tools/blender_import_model.py`：从 GLB 和语义元数据派生 Blender 文件。
- `tools/inspect_blend_actions.py`：检查 Blender 多槽 Action、骨骼曲线归属和实际 PoseBone 运动。

### 低层研究

- `tools/build_combat_evidence_manifest.py`：固定战斗逆向所用客户端、metadata 与 IL2CPP dump 的版本和哈希。
- `tools/index_il2cpp_types.py`：将普通 C# 或 AI structured IL2CPP dump 转成统一的可查询类型索引。
- `tools/build_il2cpp_type_graph.py`：从类型索引生成保守的字段/属性/方法签名引用图。
- `tools/disassemble_rva.py`：反汇编指定 RVA。
- `tools/inspect_process_rva.py`：检查运行进程中的 RVA。
- `tools/probe_runtime_rvas.py`：按版本化清单批量采集关键战斗 RVA 的运行时内存证据。

## Shader 实验

编译程序与材质语义管线的当前桥接边界见
[编译 Shader 包桥接层](research/compiled-shader-package-bridge.md)。

`experiments/endfield_blender_shader/` 当前包含两类可复用成果：

- `face-b225-recovery-boundary.md`：面部 Shader 可确认公式、运行时输入和边界；
- `silk-stockings-b391-recovery.md`：丝袜 Shader 的逐片元覆盖和专用高光结构；
- `silk_stockings_b391_reference.py`：丝袜局部公式参考实现；
- `trace_hlsl_dataflow.py`：HLSL 数据流追踪辅助工具。

`experiments/endfield_blender_export/` 保存 Blender 导出和截图对照过程。正式实现应引用其中
已经写入文档或测试的结论，不应读取该目录下的某个 `.blend` 或 PNG 才能工作。
