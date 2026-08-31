# 支持

## 从哪里获得帮助

| 渠道 | 最适合 |
|---|---|
| [Discord](https://discord.gg/bzQavDfVV9) — `#help` | 安装问题、快速提问、分享成果 |
| [GitHub Issues](https://github.com/debpalash/VoiceStudio/issues) | 缺陷与功能请求——请使用模板，并附上诊断包（设置 → 关于 → “保存诊断包”） |
| [GitHub Discussions](https://github.com/debpalash/VoiceStudio/discussions) | 设计问题、想法、成果展示 |
| 安全问题 | **绝不开公开 issue**——私密报告方式见 [SECURITY.md](SECURITY.md) |

## 卸载 / 删除全部数据

VoiceStudio 完全本地——没有什么需要停用，只有文件夹需要删除。
`scripts/uninstall.sh`（macOS/Linux）或 `scripts\uninstall.ps1`
（Windows）会列出每个 VoiceStudio 文件夹及其大小（先空跑预览），
加 `--yes` 后才会真正删除。各平台的完整路径列表见
[docs/install/uninstall.md](docs/install/uninstall.md)。

## 我们支持的模型来源

VoiceStudio 的立足理念是：它运行的一切都**开放且人人可得**——
来源与许可可验证的免费公开模型（Hugging Face 仓库、项目官方
发布），让整个社区可以用、测试并调试同一份东西。

**我们不支持私售、设付费墙或门控的模型文件。** 私下交付的模型
无法验证、复现或分享——这不符合项目目标，涉及此类模型的 issue
会被客气地关闭。作为一条通用安全规则，绝不运行任何模型压缩包
内捆绑的可执行文件。

## 提交缺陷之前

1. 更新到最新发布版（如果你跟随预览版，则更新到 `main`）——修复在持续发布。
2. 运行应用内自检：**设置 → 关于 → 运行自检**。
3. 搜索现有 issue；给已有的 issue 加 👍 并附上你的情况，而不是开重复的。

## 响应预期

这是一个在自动化分诊机器人协助下维护的开源项目：issue 通常在数小时
内完成分诊，每份报告都会得到人工确认的回复。可复现并附上诊断包的
报告修复得最快。
