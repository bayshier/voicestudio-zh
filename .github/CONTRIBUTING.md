# 参与 VoiceStudio 贡献

感谢你有意改进 VoiceStudio！本指南涵盖了上手所需的一切。

## 快速链接

| | |
|---|---|
| 💬 **聊天** | [Discord](https://discord.gg/bzQavDfVV9) |
| 🐛 **缺陷反馈** | [GitHub Issues](https://github.com/debpalash/VoiceStudio/issues) |
| 🏷️ **新手友好 issue** | [筛选后的列表](https://github.com/debpalash/VoiceStudio/labels/good%20first%20issue) |
| 📋 **路线图** | [README → Roadmap](README.md#roadmap) |

---

## 新增 TTS 或 ASR 引擎

新引擎是**为明确的岗位**而引入的，而不是往清单里加一项——准入门槛、当前的岗位对照表以及目录树外的接入路径都在 [docs/engine-acceptance.md](../docs/engine-acceptance.md) 里。提交提案前请先阅读；尤其是其中的许可证检查，会直接终结大多数提案。

## 开发环境搭建

### 前置条件

- [Git](https://git-scm.com/)
- `curl`（macOS 和 Linux 上的 Bun / uv / rustup 一行安装命令会用到）
- [Bun](https://bun.sh/)（前端包管理器）
- [uv](https://docs.astral.sh/uv/)（Python 环境管理器）
- [ffmpeg](https://ffmpeg.org/)（音频/视频处理）
- [Rust / Cargo](https://rustup.rs/)（仅桌面外壳需要）
- Python 3.10+（由 `uv` 自动管理）

Linux 桌面开发还需要 WebKitGTK/GTK 开发库。在 Debian 或 Ubuntu
上，请安装与 CI 相同的包：

```bash
sudo apt-get update
sudo apt-get install -y \
  libwebkit2gtk-4.1-dev libgtk-3-dev libpango1.0-dev libcairo2-dev \
  libsoup-3.0-dev libgdk-pixbuf-2.0-dev \
  libayatana-appindicator3-dev librsvg2-dev libssl-dev libxdo-dev \
  gstreamer1.0-plugins-good \
  libasound2-dev build-essential curl wget file
```

Fedora 和 Arch 的包请见
[Linux 源码构建指南](../docs/install/linux.md#building-from-source)。

### 克隆并运行

```bash
git clone https://github.com/debpalash/VoiceStudio.git
cd VoiceStudio
bun install
bun run dev
```

这会同时启动两个服务：

| 服务 | 地址 | 作用 |
|---------|-----|---|
| **Backend** | `localhost:3900` | FastAPI 服务器 —— TTS、ASR、说话人分离、配音流水线 |
| **Frontend** | `localhost:3901` | React + Vite UI |

后端通过 `scripts/dev-backend.mjs`（即 `dev:api` 脚本）运行：uvicorn
命令没有变化，但如果后端**挂掉**（被 OOM 杀掉、硬崩溃），包装脚本会在
开发栈关闭前打印一个带边框的退出横幅，其中包含退出码/信号以及
`omnivoice.log` 的最后 20 行——这样原因就不会随终端滚动而消失。后端
下次启动时，UI 也会把这次挂掉作为崩溃通知再次报告（见
[docs/install/troubleshooting.md §14c](docs/install/troubleshooting.md)）。

### 桌面应用（Tauri）

```bash
bun run desktop          # 开发模式：热重载 Tauri 外壳 + 后端
bun run desktop-prod     # 生产模式：先构建并打包后端，再启动
```

两者都会先运行 `uv sync`（完成 Python 后端环境设置）并自动启动
后端——你**无需**单独启动它。请使用准确的脚本名：不存在
`desktop=prod`（注意 `desktop-prod` 里是**连字符**）。
`desktop-prod` 对 Windows 做了适配（自动检测 bash/git；见 `scripts/desktop-prod.mjs`）。

需要 [Rust](https://rustup.rs/) 和各平台特定的 Tauri 依赖——见 [Tauri 前置要求](https://v2.tauri.app/start/prerequisites/)。

在 macOS/Linux 上用 rustup 安装 Rust 后，启动桌面应用前请另开一个新终端，
或在当前终端里加载 Cargo：

```bash
source "$HOME/.cargo/env"
bun desktop
```

在 Linux 上，`Package gdk-3.0 was not found`、缺少 `pango.pc` 或
`javascriptcoregtk-4.1` 之类的错误意味着上面那些原生包没有安装；修改
`PKG_CONFIG_PATH` 并不能让根本不存在的库凭空出现。

如果应用能打开，但一直停在**没有任何按钮的安装引导页**上，说明 Python
后端没有完成启动——引导页会给出卡住的原因、一个日志面板和一个
**重试**按钮（完整堆栈见 设置 → 日志 → 后端）。源码运行时最常见的原因
是 `uv` 或 Python 不在你的 PATH 上。

---

## 项目结构

```
VoiceStudio/
├── backend/                 # Python FastAPI 服务器
│   ├── api/                 # 路由处理器
│   ├── core/                # 配置、偏好设置、常量
│   └── services/            # TTS 引擎、ASR、配音、音频 DSP
│       └── tts_backend.py   # ← 多引擎 TTS 注册表
├── frontend/                # React + Vite
│   ├── src/
│   │   ├── components/      # UI 组件
│   │   ├── hooks/           # 自定义 React hooks
│   │   ├── stores/          # Zustand 状态切片
│   │   └── utils/           # 共享工具函数
│   └── src-tauri/           # Rust/Tauri 桌面外壳
├── deploy/                  # Docker、CI 配置
├── docs/                    # 截图、MCP 配置
└── scripts/                 # 构建与发布脚本
```

---

## 如何贡献

### 缺陷报告

开一个 [issue](https://github.com/debpalash/VoiceStudio/issues/new)，包含：

1. **实际发生了什么** vs **你期望的结果**
2. **复现步骤**
3. **操作系统、GPU 与 Python 版本**（可在 设置 → 日志 中查看）
4. **错误日志**（设置 → 日志 → 复制相关行）

### 拉取请求

1. **Fork** 仓库并从 `main` 创建分支
2. **保持 PR 聚焦**——每个 PR 只做一个功能或一个修复
3. 推送前**运行测试**：
   ```bash
   # 后端测试
   uv run pytest backend/ -x -q

   # 前端构建检查
   cd frontend && npx vite build --mode development
   ```
4. **写清晰的 PR 标题**——它会成为 squash 合并的提交信息
5. **不要**在 PR 描述中包含本机统计信息、文件路径或私有系统信息

### 新增一个 TTS 引擎

VoiceStudio 的 TTS 后端是一个插件注册表。新增一个引擎大约只需 50 行代码：

1. 打开 `backend/services/tts_backend.py`
2. 创建一个继承 `TTSBackend` 的类：

```python
class MyEngineBackend(TTSBackend):
    id = "my-engine"
    display_name = "My Engine (description)"

    @classmethod
    def is_available(cls) -> tuple[bool, str]:
        try:
            import my_engine  # noqa: F401
            return True, "ready"
        except ImportError:
            return False, "my_engine not installed. pip install my-engine"

    @property
    def sample_rate(self) -> int:
        return 24000

    @property
    def supported_languages(self) -> list[str]:
        return ["en", "zh"]

    def generate(self, text: str, **kw) -> torch.Tensor:
        # ... 调用你的引擎，返回 [1, num_samples] 张量
```

3. 在文件底部的 `_REGISTRY` 中注册
4. 完成后它会自动出现在 设置 → TTS 引擎 中

---

## 代码风格

### Python（后端）

- **格式化工具**：没有全局强制——与你所编辑文件的风格保持一致即可
- **日志**：使用 `logger.warning()` / `logger.error()`，绝不要裸用 `print()`
- **异常**：避免裸的 `except: pass`——应捕获具体的异常
- **类型注解**：公共 API 函数和类方法请使用

### JavaScript/React（前端）

- **组件**：使用带 hooks 的函数组件
- **状态**：Zustand store 位于 `src/stores/`，按切片组织
- **品牌资产**：复用 [`docs/branding.md`](../docs/branding.md) 中的标准标志、配色、命名与兼容性规则；不要临时重绘或重命名运行时标识符
- **CSS**：**Utilities 优先 + shadcn/ui，单一样式表。** UI 构建在 `src/components/ui/` 中的 shadcn/ui 原语之上（由 `src/ui/` 桶文件封装，按 VoiceStudio 配色定题），并以 Tailwind v4 工具类组合。**所有样式现在都集中在一个文件——`src/index.css`**：包括 `@theme` / `[data-theme]` 令牌基础，以及工具类无法表达的那一小撮不可简化规则（`@keyframes`、玻璃拟态/`backdrop-filter`、伪元素、`:has()`、未分层的级联覆盖，以及对库生成 DOM（如虚拟化行 / WaveSurfer）的样式钩子）。各组件的 `.css` 文件已在 CSS→Tailwind/shadcn 迁移中清除——**不要再新建。** 优先使用 shadcn 原语 + 工具类；如果某条规则确实无法简化，就加到 `src/index.css` 并附来源注释。（唯一的另一个 `.css` 是仅用于测试的可视化测试架。见 `docs/shadcn-migration.md`。）
- **命名**：组件用 `PascalCase`，hooks 和工具函数用 `camelCase`

### Rust（Tauri）

- **格式化**：提交前运行 `cargo fmt`
- **模块**：每个文件只负责一件事（`bootstrap.rs`、`tools.rs`、`config.rs`、`commands.rs`）

---

## 前端文件结构与大小限制

前端代码保持模块化，一次编辑加载的是一个小文件，而不是一个 1900 行
的大文件。规则如下：

- **大小上限：** 每个 `.jsx` 文件**软上限 300 行**、**硬上限 500 行**。
  超过 500 行必须拆分。（该上限**不**适用于 `src/index.css`——它是
  唯一的、刻意集中的样式基础，也是应用唯一的样式表；见上面的 CSS
  规则。）
- **页面是薄编排层。** `frontend/src/pages/` 中的文件只负责布局 +
  路由 + 状态接线，把各功能组件组合起来——不要内联超过约 50 行的
  子组件。
- **每个文件一个组件。** 把 `Foo.jsx` 和 `Foo.test.jsx` 放在一起，
  置于 `frontend/src/components/` 下按页面划分的功能文件夹中（例如
  `components/settings/`、`components/dub/`）。样式**不**与组件
  同置——样式用 utilities + shadcn，无法简化的规则放进 `src/index.css`。
- **共享的小部件放进功能文件夹内的 `primitives/` 文件夹**
  （`components/settings/primitives/` 是现成的例子）。
- **由 ESLint 的 `max-lines` 规则强制执行**（`max: 500`）——目前
  **仅警告**，因此绝不会弄挂 CI；待超长文件存量清理完后，目标是
  升级为 `error`。

---

## 提交信息

写清晰、简洁的信息。PR 标题会成为 squash 合并的提交信息。

```
good: fix: prevent CUDA OOM during concurrent transcription + TTS
good: feat: add CosyVoice 3 TTS backend adapter
good: docs: add platform compatibility matrix to README

bad:  fixed stuff
bad:  update
bad:  WIP
```

---

## 测试

```bash
# 运行全部后端测试
uv run pytest backend/ -x -q

# 运行单个测试文件
uv run pytest backend/tests/test_api.py -x -q

# 前端构建验证（暂无测试套件）
cd frontend && npx vite build --mode development

# Tauri 外壳检查（需要 Rust）
cd frontend/src-tauri && cargo check
```

---

---

## 代码评审是什么样的

每个 PR 在有人工查看之前，都会先由两个 AI 评审者评审：

- **CodeRabbit** 会发布一份变更走查（附时序图；UI 变更还会有 ASCII
  前后对比草图）、行内发现，以及针对项目硬规则的警告模式预合并
  检查。
- **Greptile** 依据同一套项目准则评审，并从其评论上的 👍/👎 反应中
  学习——请通过反应来训练它。

这两者都只起建议作用，不做门禁：最终由 CI 和维护者的批准说了算。
PR 开出几分钟后就收到详尽的机器人评论不必惊讶——说得对的就处理，
说得不对的就在回复中有理有据地反驳。

**提交与 PR 约定：** 采用带作用域的 conventional-commit 风格
（`fix(dub): …`、`feat(setup): …`），并在标题或正文中关联 issue
（`Closes #N` / `Refs #N`）。

### 使用 AI 智能体贡献

这里的很多贡献都是用 Claude Code、Cursor 和类似智能体完成的——
欢迎之至，但质量标准与手写 PR 相同（真实的 bug、正确的修复、回归
测试；见下文的质量门禁）。

一个实用提示：这个代码库很大，每次会话都向你的智能体重新解释一遍，
会迅速消耗上下文和 token。持久的记忆层可以解决这个问题——智能体
能回忆起架构、约定和你过去的发现，而不必每次重新读代码树。
[**memxt**](https://github.com/debpalash/memxt)（100% 本地、基于 MCP、
由本项目维护者开发）正是为此而生；任何 MCP 记忆服务器都可以。再搭配
仓库的智能体技能——`npx skills add debpalash/omnivoice-studio`——
你的智能体从第一条提示起就了解项目的硬规则。

## 你的 PR 必须通过的质量门禁

- **跨平台一致性（硬规则）：** 凡随默认模式发布的内容，必须在
  macOS、Windows 和 Linux 上行为完全一致。平台特定的*实现*可以；
  默认*行为*因平台而异则是 P0。仅限平台的功能须放在显式开关之后
  （设置开关、环境变量或 CLI 标志）。
- **i18n——全部 21 种语言（硬规则）：** 每个面向用户的字符串都要经过
  `t('...')`，且键必须存在于 `frontend/src/i18n/locales/` 下**全部
  21 个**文件中。要翻译；不要把英文原样复制进非英文语言文件。CI 会
  因允许清单之外写死的 CJK 字符而失败（清单在
  `tests/test_no_hardcoded_cjk.py`；合理的功能性 CJK 可扩展
  `_ALLOWED_FILES` 并附理由）。
- **数据库结构变更**须经由带已测试升级路径的 alembic 迁移——现有的
  `omnivoice_data/` 必须无需任何手动步骤即可继续工作。
- **引擎向后兼容：** 已安装的引擎（磁盘上的模型权重）不得要求重新
  安装或重新下载。
- **本地优先：** 除 GitHub Issues（自愿开启的上报）和 HuggingFace
  模型下载外，不得新增任何出站调用。绝不记录或持久化密钥及绝对
  home 路径。
- **安全姿态：** 后端以回环 HTTP 提供服务——把每个查询/路径/表单
  参数都视为恶意输入。用户选择的文件系统目标位置在 Tauri 进程中
  授权（保存对话框），绝不通过 HTTP 参数。

## 贡献许可

VoiceStudio 采用 **AGPL-3.0-only**，维护者同时提供**商业许可**
（见 [LICENSE](LICENSE)）。提交贡献即表示你同意：

1. 你有权提交它（你自己的作品，或采用兼容许可的作品）；
2. 它以 **AGPL-3.0** 许可给本项目；且
3. 你授予项目维护者一项永久的、全球范围的、非独占的权利，使其也
   可以按项目的商业许可条款分发你的贡献。

正是这份入站授权让双许可模式得以维系。如果某项贡献你无法同意
第 (3) 条，请在 PR 中说明，我们会在合并前讨论。在提交中添加
`Signed-off-by:` 行（DCO）值得赞赏，但并非必需。

---

## 需要帮助？

- **搭建卡住了？** 到 [Discord #help](https://discord.gg/bzQavDfVV9) 提问
- **不知从何下手？** 看看[新手友好 issue](https://github.com/debpalash/VoiceStudio/labels/good%20first%20issue)
- **想讨论一个大的改动？** 先开 [discussion](https://github.com/debpalash/VoiceStudio/discussions) 或 Discord 话题，再动手写代码

感谢你的贡献！🎙️
