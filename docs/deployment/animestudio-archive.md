# 独立 AnimeStudio 仓库归档清单

归档的对象是 `SQwatermark/AnimeStudio` 的独立 GitHub 仓库，不是 VFS 内的
`unity-worker/vendor/animestudio` 快照，也不等于删除任何本地 checkout。GitHub 的 archive
状态是可逆的只读状态；本流程不使用递归删除、强制重置或历史改写。

## 前置门禁

只有以下条件全部满足后才归档：

- VFS P5 发布相关提交已推送，`Windows release` 工作流成功；
- 发布 ZIP 在另一台未安装 Python、.NET 和 AnimeStudio 的 Windows x64 机器上，以真实
  data root 和 `-IsolatedRuntime` 验收通过；
- `tools/Test-AnimeStudioSnapshot.ps1` 确认独立仓库工作树干净、HEAD 等于
  `8cdec963c4e187ea0a4a339b8969844a9574638b`，260 个来源文件仅有两个已记录补丁；
- VFS 的 `unity-worker/UPSTREAM.md`、第三方许可证和发布包内许可证均已复核；
- 用户明确确认执行 GitHub 仓库归档。

## 执行前记录

在独立仓库执行只读检查，并把结果与最终 VFS 发布提交一起记录到产品化文档：

```powershell
git status --short --branch
git rev-parse HEAD
git remote -v
git log -1 --format=fuller
```

预期权威分支为 `feature/endfield-animation-acl`，HEAD 为上述完整提交。若工作树不干净、远端
不再是 `SQwatermark/AnimeStudio`，或 HEAD 发生变化，应停止归档并重新执行来源审计，不能把
“大概是同一版本”写进交接。

## GitHub 归档

由仓库管理员在 GitHub 仓库 Settings 的归档入口执行。归档后验证：

- 仓库页面明确显示 archived/read-only；
- `feature/endfield-animation-acl` 和提交 `8cdec96…` 仍可浏览；
- VFS 构建与运行不访问该仓库；首次干净构建只下载
  `dependencies.lock.json` 锁定并校验的 ACL/RTM 公共依赖；
- VFS `Windows release` 工作流仍可成功生成产品归档。

本地 checkout 至少保留一个发布周期，用于追溯和紧急复核；不要因远端已归档而删除它。

## 恢复与重新开发

需要修正独立仓库时，先由管理员在 GitHub 解除归档，再从权威分支创建明确的新提交。随后：

1. 更新 `unity-worker/UPSTREAM.md` 的完整提交和导入日期；
2. 重新运行快照审计、许可证核对、Python/.NET 门禁和真实样本等价测试；
3. 构建新的完整发布目录，不覆盖旧发布包；
4. 新版本验收完成后再决定是否重新归档。

禁止直接修改 VFS vendor 快照后仍声称来源提交未变，也禁止为方便重新引入运行时
AnimeStudio CLI 回退。
