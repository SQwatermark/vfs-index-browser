# Windows 发布、安装与升级

VFS Browser 的 Windows 产品包由一个冻结的 Python 服务和一个自包含的 .NET Unity worker
组成。运行产品包不需要安装 Python、.NET 或 AnimeStudio，也不应从相邻源码仓库、固定盘符
或用户目录寻找 Unity 能力。Blender、vgmstream、usm-convert 和 ffmpeg 是可选工具；缺失时
健康检查会明确报告，但不影响不依赖它们的功能。

## 构建发布包

构建机需要 x64 Windows、Python 3.13、仓库 `unity-worker/global.json` 锁定的 .NET SDK
9.0.200，以及带 C++ x64 工具链的 Visual Studio。首次准备隔离环境：

```powershell
python -m venv .tmp/release-venv
.tmp/release-venv/Scripts/python.exe -m pip install -r requirements.txt -r requirements-build.txt
```

发布命令：

```powershell
./tools/Publish-Windows.ps1 `
  -OutputDirectory .tmp/endfield-vfs-browser-win-x64 `
  -ArchivePath .tmp/endfield-vfs-browser-win-x64.zip `
  -PythonExe .tmp/release-venv/Scripts/python.exe `
  -DotnetExe dotnet
```

目标目录必须不存在，脚本不会覆盖旧发布包。正常发布会依次执行完整 Python 与 .NET 门禁、
恢复经过提交与 SHA-256 锁定的 ACL/RTM 源码、构建 Endfield ACL 原生桥、执行 PyInstaller
onedir 构建、win-x64 自包含 worker 发布、EXE 帮助检查和 worker 握手。首次恢复依赖需要
联网，后续会复用 `unity-worker/.deps/` 中校验通过的缓存；产品运行时不会联网。只有 Git
跟踪的 `public/`、`schemas/` 与许可证会进入包，工作树中的研究样本和临时文件不会被复制。
指定 `-ArchivePath` 时还会生成包含顶层产品目录的 ZIP 和相邻 `.sha256` 文件；归档目标同样
必须不存在，发布过程不会覆盖历史归档。

正式发布要求整个 Git 工作树干净，`release.json` 会记录 `sourceTree: clean`。存在已修改或
未跟踪文件时命令会停止，避免用某个 HEAD 冒充实际打包源码。`-AllowDirty` 只允许本地试构建，
产物明确标记为 dirty；验收器默认拒绝它，且即使使用 `-AllowDirtyRelease` 做开发诊断，也不能
生成正式验收报告。
`-SkipTests` 只供已经执行过同一提交门禁的本地迭代，不用于正式发布。

构建后可先做不依赖游戏数据的结构与 worker 验收：

```powershell
./tools/Test-WindowsRelease.ps1 -ReleaseDirectory .tmp/endfield-vfs-browser-win-x64
```

仓库的 `Windows release` GitHub Actions 工作流在手动触发或推送 `vfs-browser-v*` 标签时，
使用 Windows Server 2025、Python 3.13 和精确 .NET SDK 9.0.200 从干净 checkout 执行同一
发布与静态验收命令。发布器和包内验收器均由 Windows PowerShell 5.1 执行，并上传 ZIP 与
`.sha256`，保留 14 天。CI 没有游戏数据，因此不能替代下一节的真实 data root 验收。

## 安装与首次启动

将整个发布目录复制到目标机器的普通可写目录，不要只复制 EXE。游戏索引和派生数据库默认
放在包内 `data/`；已有数据也可放在包外并显式指定：

```powershell
$env:VFS_BROWSER_DATA_ROOT = "E:\EndfieldVfsData"
./endfield-vfs-browser.exe --host 127.0.0.1 --port 8765
```

浏览器打开 `http://127.0.0.1:8765/`。首次缺少 SQLite 时服务会从 data root 下的
`endfield-vfs-index.jsonl.tgz` 构建；启动时也会验证主索引与当前游戏数据，发现过期后原子
重建。若只想诊断而不修复，可加 `--no-auto-rebuild`。

验收脚本随发布包放在 `tools/`，新机器不需要 VFS 源码 checkout。进入解压后的产品目录，再
绑定实际 data root；脚本会在隐藏窗口中启动服务，验证主页、索引、Manifest、包内 worker、
能力集合和旧工具状态，然后停止进程并恢复原环境变量：

```powershell
Set-Location E:/Apps/endfield-vfs-browser
./tools/Test-WindowsRelease.ps1 `
  -ReleaseDirectory . `
  -DataRoot E:/EndfieldVfsData `
  -IsolatedRuntime `
  -ReportPath E:/VfsAcceptance/endfield-vfs-browser.json
```

发布与验收脚本兼容 Windows 自带的 Windows PowerShell 5.1，不要求目标机器额外安装
PowerShell 7。验收器在 Windows PowerShell 5.1 下也会执行同一份逐文件完整性、worker 和服务检查。

`-IsolatedRuntime` 会在 worker 握手和服务子进程启动期间清除 Python、dotnet、旧 worker 与
可选工具覆盖，并把 `PATH` 限制为 Windows 系统目录；检查结束后恢复调用进程原环境。该模式
用于排除发布包意外借用开发机运行时，但仍不能替代另一台物理或虚拟 Windows 机器验收。
`-ReportPath` 只在指定 data root 的完整服务验收中可用，拒绝覆盖旧报告，也拒绝把报告写入
不可变发布目录。报告不记录用户名、发布绝对路径或 data root；它保存发布提交与元数据哈希、
操作系统/架构、隔离状态、文件数、worker 版本/能力数以及索引和 Manifest 结论。完成另一台
机器验收后，应将这份报告作为 P5 门禁证据保存。

部署到其他机器时必须连同下列目录保留：

- `_internal/`：冻结 Python 运行时；
- `public/` 与 `schemas/`：前端和严格数据契约；
- `unity-worker/artifacts/`：自包含 .NET worker、原生库及其依赖；
- `unity-worker/third-party/licenses/`：随产品分发的第三方许可证；
- `tools/` 及根目录的 Blender 辅助模块：可选 Blender 导出入口。

## 健康检查与故障诊断

访问 `GET /api/health`。正常产品包至少应满足：

- 顶层 `status` 为 `ready`；
- `indexFreshness.status` 为 `current`；
- `manifestIndex.status` 为 `ready`；
- `unityWorker.status` 为 `ready`，协议为 `vfs-unity-worker/1.0.0`，且
  `missingCapabilities` 为空；
- `legacyTools` 为空。

`release.json` 记录 Git 提交、Python/PyInstaller 版本、worker 协议与版本，以及全部不可变
文件的相对路径、大小和 SHA-256。验收脚本会拒绝缺失、被修改或额外出现的不可变文件；运行时
可写的 `data/` 不进入该清单。服务日志默认输出结构化 JSON；可用
`VFS_BROWSER_LOG_FORMAT=text` 改为文本，或用
`--log-level debug` 增加诊断。所有路径覆盖都通过 `runtime_config.py` 中列出的
`VFS_BROWSER_*`、`BLENDER_EXE`、`VGMSTREAM_CLI`、`USM_CONVERT` 和 `FFMPEG` 环境变量提供，
不修改包内文件来绑定机器路径。

## 升级与回退

发布目录是不可变版本单元。升级时把新包解压到新的并列目录，继续指向同一个外部 data root，
先启动新包并检查 `/api/health`，确认后再停止旧包。不要把新文件覆盖进正在运行的旧目录；这会
混合 Python 和 .NET 运行时版本。回退只需停止新包并重新启动原目录。缓存带版本身份，服务不会
把未完成的任务目录发布成有效结果；重要的索引和 data root 仍应按常规方式备份。

独立 AnimeStudio 仓库不是安装或升级依赖。若未来新增 Unity 能力，应先扩展 VFS 自有协议、
测试和包内 worker，再发布完整新目录，不能恢复对旧 CLI 的运行时回退。

归档独立 AnimeStudio 仓库前，可从 VFS checkout 复核最终同步点和内嵌快照：

```powershell
./tools/Test-AnimeStudioSnapshot.ps1 -AnimeStudioPath D:/Projects/AnimeStudio
```

该检查要求外部仓库 HEAD 与 `unity-worker/UPSTREAM.md` 一致、工作树干净，并逐个比较 VFS
跟踪的 vendor 文件。文本只归一化 CRLF/LF；其余字节严格比较。允许的差异只能是
`PATCHES.md` 已记录的两个源码补丁。实际归档步骤、停止条件与恢复流程见
[`animestudio-archive.md`](animestudio-archive.md)。
