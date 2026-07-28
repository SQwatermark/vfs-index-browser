# Endfield VFS Index Browser

一个独立的小工具，用浏览逻辑文件目录的方式查看 Endfield VFS 完整索引。

它不会修改 Endaxis，也不依赖 npm。后端使用 Python 标准库提供本地 HTTP 服务，并把 JSONL/TGZ 索引构建成 SQLite，前端按目录懒加载。

工具定位是本地游戏文件浏览器：完整预览和下载依赖本机存在终末地游戏资源，不把 AKEDB 或其他远程 CDN 当作运行时数据源。

## 使用方式

默认读取当前仓库中已经生成的索引：

```powershell
python server.py --rebuild
```

然后打开：

```text
http://127.0.0.1:8765
```

后续已经生成过数据库时：

```powershell
python server.py
```

指定索引或端口：

```powershell
python server.py `
  --index C:\Users\sqwat\Projects\zmd\Endaxis\zmd-research\analysis\database\facts\endfield-vfs-index-20260727-234026.jsonl.tgz `
  --db data\endfield-vfs-index.sqlite `
  --host 127.0.0.1 `
  --port 8765 `
  --rebuild
```

远程主机直接对局域网开放时：

```powershell
python server.py `
  --index D:\Temp\endfield-re\vfs-index\20260727-234026\endfield-vfs-index-20260727-234026.jsonl.tgz `
  --db data\endfield-vfs-index.sqlite `
  --host 0.0.0.0 `
  --port 8765 `
  --rebuild
```

如果只想重建数据库、不启动 HTTP 服务：

```powershell
python server.py --index D:\Temp\endfield-re\vfs-index\20260727-234026\endfield-vfs-index-20260727-234026.jsonl.tgz --db data\endfield-vfs-index.sqlite --rebuild --build-only
```

远程常驻服务可以用 `run-server.cmd` 作为计划任务入口；该脚本会从自身所在目录启动服务。

`.ab` 内部导出依赖 AnimeStudio。默认查找：

```text
D:\Projects\AnimeStudio\AnimeStudio.CLI\bin\Release\net10.0-windows\AnimeStudio.CLI.exe
```

如路径不同，可通过环境变量 `ANIMESTUDIO_CLI` 覆盖。

`.pck` 音频预览依赖 `vgmstream-cli.exe` 将 Wwise WEM 转成浏览器可播放的 WAV。默认查找：

```text
vfs-index-browser/tools/vgmstream/vgmstream-cli.exe
```

如路径不同，可通过环境变量 `VGMSTREAM_CLI` 覆盖。内部导出和转码缓存默认在：

```text
vfs-index-browser/data/internal-cache/
```

`.usm` 视频预览会优先尝试调用外部 `usm-convert.exe` 转 MP4，找不到工具时回退到内置的基础抽流逻辑并调用 `ffmpeg` 封装。默认查找：

```text
vfs-index-browser/tools/usm-convert.exe
```

如路径不同，可通过环境变量 `USM_CONVERT` 覆盖；`ffmpeg` 可通过环境变量 `FFMPEG` 覆盖。

## 浏览视图

页面提供四个 scope：

| scope | 含义 |
| --- | --- |
| `Effective` | 按 `Persistent -> StreamingAssets` 的静态优先级折叠，每个 `BlockName/fileName` 只显示一个有效候选 |
| `Persistent` | 只看热更目录中的元数据 |
| `StreamingAssets` | 只看底包目录中的元数据 |
| `All Sources` | 顶层按 `Persistent` / `StreamingAssets` 分开，完整展示索引中所有记录 |

目录结构来自索引中的逻辑路径，而不是物理磁盘路径。典型路径形如：

```text
Table/Data/TableCfg/SkillConditionTable.bytes
Audio/Data/Audio/PCK/Windows/Main/default_stream_0.pck
Bundle/Data/Bundles/Windows/main/a53af5bd74e329b80f12a7f4.ab
```

## 预览和下载

点击文件行后，服务会根据索引记录中的物理 chunk 路径、offset 和 length 读取真实文件片段。

- 加密文件会按当前已知的 VFS ChaCha20 规则解密后再预览或下载。
- `.json` / `.lua` / `.md` / `.txt` 等文本文件会显示文本预览。
- 常见图片、视频、音频扩展名会交给浏览器原生预览。
- 其他二进制文件显示前段十六进制内容。
- `.ab` / `.pck` / `.usm` / `.hgmmap` 会识别为二级容器，并提供原始文件下载。
- `.ab` 可以按需切片为临时 AssetBundle，再调用 AnimeStudio 导出 `Texture2D`、`Sprite`、`TextAsset`、`AudioClip`、`VideoClip` 等浏览器可预览文件。
- `.ab` 导出时会额外生成 `AssetMap`，并把导出文件和 Unity 资源身份关联起来。`Container` 是目前最接近“原始 asset 路径”的字段，通常形如 `assets/beyond/.../texture.tga`；`Source` 更接近 AB/CAB 内部源文件，不应当当作资源路径使用。
- `.pck` 会按 AKPK/PCK/BNK 结构解析 WEM 条目，并暴露为虚拟目录：`wem/<前两位 hex>/<wem id>.wem` 和 `wav/<前两位 hex>/<wem id>.wav`。点击 `wem` 会下载解密后的 WEM，点击 `wav` 会按需调用 `vgmstream-cli` 转成 WAV 供浏览器预览。
- `Data/TableCfg/*.bytes` 会按 VFS 规则解密并通过内置 SparkBuffer 解析器转换为 JSON，预览区显示前段内容，同时提供完整 JSON 打开/下载入口。
- `.usm` 暴露为 `mp4/<原文件名>.mp4` 虚拟目录项，点击预览或下载时按需转换为浏览器可播放的 MP4。

## API

```text
GET /api/manifest
GET /api/list?scope=effective&path=Table/Data/TableCfg&page=1&pageSize=100
GET /api/search?scope=effective&q=SkillConditionTable&limit=100
GET /api/preview?id=123
GET /api/raw?id=123
GET /api/raw?id=123&download=1
GET /api/internal/list?id=123
GET /api/internal/preview?id=123&path=Texture2D/example.png
GET /api/internal/raw?id=123&path=Texture2D/example.png
GET /api/internal/list?id=123&path=wem
GET /api/internal/preview?id=123&path=wav/10/269385047.wav
GET /api/internal/raw?id=123&path=wem/10/269385047.wem
GET /api/tablecfg/json?id=123
```

## 诊断脚本

`JsonData/Data/Json/**/*.json` 的扩展名不一定代表解密后就是 JSON 文本。可以用诊断脚本批量扫描真实 payload：

```powershell
python tools\scan_jsondata_formats.py `
  --db data\endfield-vfs-index.sqlite `
  --output data\reports\jsondata-format-report.md
```

脚本会读取 VFS 索引中的 `Effective/JsonData/Data/Json/**/*.json`，按索引中的 `chunk_path`、`offset`、`length` 切片，必要时使用项目内置 VFS ChaCha20 规则解密，然后分类为：

- `JSON 文本`
- `可读文本但不是 JSON`
- `已解密二进制`
- `缺少 chunk`
- `读取错误`

报告会包含总览、按一级目录统计、二进制签名样例和异常样例。默认输出到 `data/reports/`，属于本机诊断产物，不纳入 git。

## 数据库说明

SQLite 数据库默认生成在：

```text
vfs-index-browser/data/endfield-vfs-index.sqlite
```

它是索引的本地派生产物，可以随时通过 `--rebuild` 重新生成。

## 当前限制

VFS 层已经能够定位和读取逻辑文件，`.ab` 已接入按需导出和 AssetMap 元数据，`.pck` 已能按需列出、解密和转码 WEM，`Data/TableCfg/*.bytes` 已能转 JSON，`.usm` 已提供按需 MP4 预览入口。仍需继续完善的方向：

1. 对齐 AnimeStudio 的更多 AKPK/WEM 边界案例，补全音效、角色语音和剧情语音的用途索引。
2. 解析 `.hgmmap` / bundle manifest，建立资源逻辑路径到 `.ab` 的映射，而不只按包名浏览。
3. 把基础文件浏览升级为聚合视图，例如干员、武器、敌人、任务台本、模型和音频用途目录。
4. 增加对 SparkBuffer 表结构变化的批量校验，避免新增版本中静默漏字段。
