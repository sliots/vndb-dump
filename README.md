# VNDB dump 每日镜像

[![Sync VNDB dumps](https://github.com/sliots/vndb-dump/actions/workflows/sync.yml/badge.svg)](https://github.com/sliots/vndb-dump/actions/workflows/sync.yml)

通过 GitHub Actions 每天同步 [VNDB 官方公开 dump](https://vndb.org/d14)，发布到 [GitHub Releases](https://github.com/sliots/vndb-dump/releases)。数据库文件不提交到 Git 历史。

## 下载

[最新 Release](https://github.com/sliots/vndb-dump/releases/latest)

| 文件 | 内容 |
| --- | --- |
| `vndb-db-latest.tar.zst` | 近完整数据库，包含原始 README 和许可证说明 |
| `vndb-tags-latest.json.gz` | 标签 |
| `vndb-traits-latest.json.gz` | 角色特征 |
| `vndb-votes-latest.gz` | 官方公开投票数据 |
| `manifest.json` | 原始下载地址、各文件上游修改时间、下载时间、大小及 SHA-256 |
| `SHA256SUMS` | 下载完整性校验文件 |

固定下载地址示例（正常未分片时）：

```text
https://github.com/sliots/vndb-dump/releases/latest/download/vndb-db-latest.tar.zst
https://github.com/sliots/vndb-dump/releases/latest/download/vndb-tags-latest.json.gz
https://github.com/sliots/vndb-dump/releases/latest/download/vndb-traits-latest.json.gz
https://github.com/sliots/vndb-dump/releases/latest/download/vndb-votes-latest.gz
```

下载 Release 内全部文件后，可在 Linux / macOS / Windows WSL 运行：

```sh
sha256sum -c SHA256SUMS
```

单文件超过 1900 MiB 时自动生成 `.part001`、`.part002` 等分片，以满足 GitHub 单文件必须小于 2 GiB 的限制。先校验各分片，再按编号拼接，例如：

```sh
cat vndb-db-latest.tar.zst.part[0-9][0-9][0-9] > vndb-db-latest.tar.zst
```

完整原文件的 SHA-256 也记录在 `manifest.json`。GitHub 自动附加的 Source code 压缩包只有本仓库代码，不包含 dump。

## 自动同步

- 官方通常每天约 **UTC 08:00** 更新；本仓库每天 **UTC 09:23 / 北京时间 17:23 / 日本时间 18:23** 运行。
- 可在 [Actions](https://github.com/sliots/vndb-dump/actions/workflows/sync.yml) → **Run workflow** 手动执行。同步脚本或工作流推送到 `main` 时也会执行。
- 四个文件依次下载，使用 HTTPS，失败自动重试；检查非空、HTTP Content-Length（如提供）、压缩完整性、JSON 格式及 tar 结构。
- 所有原文件内容一致则跳过发布。Release tag 为 `dump-` 加内容指纹；上游同一天修订也能创建新版本。
- 先创建草稿并上传，验证资产数量、大小及 GitHub 返回的 SHA-256（如提供）后才发布。失败保留草稿，下次同步可重传修复，已发布历史版本不覆盖、不自动删除。
- `releases/latest` 指向最近一次成功发布；运行失败不会替换已有版本。运行结果见 Actions 日志及 Summary。
- 只使用内置 `GITHUB_TOKEN`，无需配置个人令牌或额外 Secrets。
- GitHub 定时任务可能延迟；**公开仓库连续 60 天没有仓库活动时，GitHub 可能停用定时任务**。若被停用，在 Actions 页面重新启用即可，或通过代码维护保持仓库活跃。

本镜像不包含图片、论坛内容、编辑历史或 VNDB 未公开的用户资料。四个文件分别取自官方 `latest`，不宣称它们是数据库事务级的一致快照；各自时间见清单。数据库 schema 可能随上游调整。

## 来源与许可

数据来源：[VNDB.org](https://vndb.org/)，下载说明：[Database Dumps](https://vndb.org/d14)，数据许可：[Privacy Policy & Licensing §4](https://vndb.org/d17#4)。本仓库是非官方镜像，与 VNDB 无隶属关系。

数据保留原有许可，通常涉及 [Open Database License 1.0](https://opendatacommons.org/licenses/odbl/1.0/) 与 [Database Contents License 1.0](https://opendatacommons.org/licenses/dbcl/1.0/)，并存在按表、字段或内容类型的例外。以官方许可页面及 `vndb-db-latest.tar.zst` 内原始 README 的说明为准。再分发和使用时请保留来源及适用许可；本镜像不重新授权原数据。

## 本地验证

```sh
python3 -m unittest discover -s tests -v
```

实际同步需要 Python 3.11+、curl、zstd、GNU tar、GitHub CLI，以及环境变量 `GH_TOKEN`、`GH_REPO`、`GITHUB_SHA`。GitHub Actions 已完成这些配置。
