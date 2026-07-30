# Endfield VFS Index Browser

本项目是一个独立的《明日方舟：终末地》本地资源浏览器。它读取本机游戏资源索引，不依赖远程 CDN，也不属于 Endaxis。

## 能力

- 按 `Effective`、`Persistent`、`StreamingAssets` 和 `All Sources` 浏览 VFS 逻辑路径。
- 根据索引中的 chunk、offset、length 和 IV 读取并解密单个文件。
- 在 `manifest.hgmmap` 同级提供虚拟目录，浏览完整 AssetInfo 逻辑树。
- 按需解析单个 AssetBundle，不预扫描全部 `.ab` 文件。
- 预览文本、图片、音频和视频，并下载原始或转换后的文件。
- 解析 TableCfg/SparkBuffer，并实验性解析 JsonData/MemoryPack 二进制配置。
- 按需恢复 Prefab 的组合模型，并以自包含 GLB 在浏览器中预览或下载。

## 架构原则

资源定位和资源内容解析分为两层：

1. `manifest.hgmmap` 提供逻辑资源路径、所属 Bundle 和大小，是虚拟目录的数据源。
2. 用户点击虚拟目录中的资源时，服务定位 Effective `.ab`，再按 AssetMap 的 `Container` 精确查找导出内容。

因此，包含 `.ab` 的普通文件夹不会生成额外虚拟目录，也不需要运行耗时的全量 AnimeStudio 扫描。详细设计见 [docs/design/architecture.md](docs/design/architecture.md)。

## 启动

安装 Python 依赖：

```powershell
python -m pip install brotli pillow
```

首次从 JSONL/TGZ 索引构建 SQLite 并启动：

```powershell
python server.py --rebuild
```

已有 `data/endfield-vfs-index.sqlite` 时直接启动：

```powershell
python server.py
```

默认地址为 `http://127.0.0.1:8765`。局域网访问可添加 `--host 0.0.0.0`。

常用参数：

```powershell
python server.py `
  --index D:\path\to\endfield-vfs-index.jsonl.tgz `
  --db data\endfield-vfs-index.sqlite `
  --host 0.0.0.0 `
  --port 8765 `
  --rebuild
```

`.ab` 按需导出默认调用：

```text
D:\Projects\AnimeStudio\AnimeStudio.CLI\bin\Release\net10.0-windows\AnimeStudio.CLI.exe
```

路径可通过 `ANIMESTUDIO_CLI` 覆盖。内部索引和导出缓存位于 `data/internal-cache/`。

manifest 中的 `.asset`、`.prefab` 如果无法按常规资源类型导出，服务会按
container 精确尝试 `MonoBehaviour + Dump`，用于查看 VolumeProfile 等自定义
Unity 组件。模型快照使用的定制 CLI 若不支持完整 TypeTree Dump，可单独设置：

```powershell
$env:ANIMESTUDIO_MONOBEHAVIOUR_CLI =
  "D:\Projects\AnimeStudio\AnimeStudio.CLI\bin\Release\net10.0-windows\AnimeStudio.CLI.exe"
```

两个变量默认指向同一个程序；只有部署中确实使用两种 AnimeStudio 构建时才需拆分。

manifest 中的 Cubemap 会按 container 精确导出六个面，并在预览区组成可逐面打开、
下载的画廊。若主 CLI 是模型快照专用的定制构建，可把支持六面导出的标准构建单独配置为：

```powershell
$env:ANIMESTUDIO_CUBEMAP_CLI =
  "D:\Projects\AnimeStudio\AnimeStudio.CLI\bin\Release\net10.0-windows\AnimeStudio.CLI.exe"
```

Cubemap 缓存会记录 EXE、CLI DLL 和核心 DLL 的文件身份，工具重新构建后自动失效。

## 主要模块

| 路径 | 职责 |
| --- | --- |
| `server.py` | VFS SQLite、HTTP API、文件读取以及各容器适配入口 |
| `manifest_index.py` | HGM manifest 解析、派生 SQLite 缓存和逻辑目录查询 |
| `sparkbuffer.py` | TableCfg/SparkBuffer 解码 |
| `usm.py` | CRI USM 视频处理 |
| `public/` | 无构建步骤的浏览器前端 |
| `tools/` | 格式探测、索引提取和离线解码工具 |
| `schemas/` | MemoryPack 已知类型与 union 映射 |
| `docs/design/` | 当前设计和演进方向 |
| `docs/research/` | 已验证的格式研究记录 |

## API

```text
GET /api/manifest
GET /api/list?scope=effective&path=&page=1&pageSize=100
GET /api/search?scope=effective&q=SkillConditionTable&limit=100
GET /api/preview?id=123
GET /api/raw?id=123&download=1
GET /api/internal/list?id=123&path=assets&page=1&pageSize=100
GET /api/internal/preview?id=123&path=Texture2D/example.png
GET /api/internal/raw?id=123&path=Texture2D/example.png
GET /api/tablecfg/json?id=123
GET /api/manifest-asset/preview?manifestId=123&assetIndex=456
GET /api/manifest-asset/raw?manifestId=123&assetIndex=456
GET /api/manifest-asset/model?manifestId=123&assetIndex=456
GET /api/manifest-asset/model-glb?manifestId=123&assetIndex=456
```

manifest 逻辑树通过普通 `list` API 浏览；资源预览使用 `manifestId + assetIndex` 稳定定位。`.ab`、`.pck` 和 `.usm` 仍通过 internal API 浏览各自的按需内部视图。

## 研究工具

- `tools/parse_hgmmap.py`：离线验证 BundleManifest 结构。
- `tools/extract_indexed_file.py`：按 VFS 文件 ID 提取并解密文件。
- `tools/scan_jsondata_formats.py`：批量统计 JsonData 的真实编码格式。
- `tools/probe_binary_json.py`：对单个二进制 JSON 做结构探测。
- `tools/extract_memorypack_schema.py`：从 IL2CPP dump 提取 MemoryPack schema。
- `tools/decode_memorypack_json.py`：使用已知 schema 解码二进制配置。
- `tools/blender_import_model.py`：在 Blender 4.3 中导入模型 GLB，并根据 `endfieldPreview` 自动建立 Eevee CharacterNPR 预览材质、相机、灯光和可选轮廓。

Blender 脚本必须由 Blender 自带的 Python 执行：

```powershell
blender --background --factory-startup `
  --python tools/blender_import_model.py -- `
  model.glb model.blend --render preview.png
```

角色信息界面的 Cubemap 六面导出后，可先构建独立的光照输入：

```powershell
python tools/build_character_lighting.py `
  path/to/exported/Cubemap `
  path/to/character-lighting.json

blender --background --factory-startup `
  --python tools/blender_import_model.py -- `
  model.glb model.blend `
  --lighting path/to/character-lighting.json `
  --render preview.png
```

`character-lighting.json` 保存原始 Profile 参数、六面相对路径和生成的等距柱状环境贴图路径。当前 AnimeStudio 通过 PNG 输出 BC6H Cubemap，因此这条链路属于 LDR 预览，不能保留原资源的 HDR 动态范围。

使用 `--outline` 可启用近似的 Freestyle 轮廓。脚本会保留 GLB 导入的骨架、蒙皮、纹理和材质自定义属性；当前节点组是 Eevee 静态预览后端，不等同于原始 HGRP Shader。

格式结论和未完成事项以 `docs/research/` 中的文档为准，不应从临时终端输出推断。

## 当前限制

- manifest 已能建立完整路径到 Bundle 的映射，但尚未对所有 Unity 类型提供预览。
- manifest 资源会自动衔接对应 AB；AnimeStudio 未支持的 Unity 类型会明确提示无法导出。
- MemoryPack 解码仍依赖从当前客户端 IL2CPP 数据提取的 schema，游戏升级后需要重新验证。
- 组合模型已完成首个角色样本的网页与 Blender 验证；当前预览区分衣物 PBR 与面部/头发 CharacterNPR，并携带 Ramp、SDF 等专用纹理，但游戏完整 Shader、动画和 BlendShape 尚未恢复。
- 音频用途、任务台本等聚合视图尚未建立。
