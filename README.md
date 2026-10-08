# AdobeClashGuard

> A Windows tool that makes Adobe apps and Clash Verge **mutually block** each other at launch — if one is running, launching the other pops a native prompt instead of silently misbehaving.

你在用 Clash Verge（或其它基于 Mihomo 内核的代理）跑自定义模型 / 翻墙，同时又开着 Photoshop、After Effects 这类 Adobe 软件时会遇到麻烦：**两边同时开着，Adobe 启动异常或直接起不来。**

这个工具不修复底层冲突，它只做一件事——**在启动前拦住你，并告诉你是哪边开着**。双向都拦：

| 当前正在运行 | 你点击的图标 | 结果 |
|---|---|---|
| Clash 界面或代理内核 | 受管的 Adobe 图标 | 弹窗提示，阻止启动；退出 Clash 后重试即可 |
| 任意 Adobe 应用 | 受管的 Clash 图标 | 弹窗提示，阻止启动；保存工作、退出 Adobe 后重试 |

两种弹窗都可以取消，取消后不会启动任何东西。**工具不会替你强关 Adobe 或 Clash，也不会丢弃未保存的工作。**

## 特性

| 特性 | 说明 |
|---|---|
| 双向互斥 | 不只拦"Clash 开着时启动 Adobe"，反向同样拦截 |
| 覆盖面更全 | 只读扫描**两个桌面 + 用户级与公共开始菜单**（开始菜单递归扫描，跳过「启动」文件夹），自动挑出所有 Adobe 与 Clash 入口 |
| 名单可扩展 | 拦截进程名外挂为纯文本 `blockers.txt`，改完立即生效——换内核、加别的代理软件都不用重新打包 |
| 被改写可发现 | 受管的图标若被安装器重写（Clash Verge 每次更新都会重建快捷方式），扫描时会明确报出来，不会静默失效 |
| 不改原入口 | 保留原名称、图标、参数、工作目录、窗口状态与管理员标记；拖入文件时安全追加参数 |
| 先备份再改 | 每一步先备份并校验，首个失败立即停止；提供一键恢复入口 |
| 不常驻后台 | 检查完就退出，不是常驻监控进程，不占内存 |
| 文件夹版分发 | 2.1.1 起改用 `--onedir`，运行时不向 `%TEMP%` 自解压，不再出现"无法删除临时目录"警告，启动也更快 |
| 可验证 | 38 项自动测试 + 端到端验收脚本，见 [`tests/`](tests/) 与 [`tools/`](tools/) |

## 下载使用

到 [Releases](../../releases/latest) 下载 `AdobeClashGuard-v2.2.1-win64.zip`，完整解压后：

1. 右键 `AdobeClashGuard\AdobeClashGuard.exe` → **以管理员身份运行**（公共桌面上的 Clash 图标通常需要管理员权限才能修改）
2. 程序自动扫描并列出识别到的图标，确认后接入
3. 看到"**接入成功，并已校验**"即完成
4. 用原桌面图标验证两个方向的弹窗是否正常

> **`AdobeClashGuard.exe` 不能单独拷出来用**，它需要同目录的 `_internal` 文件夹。要发给别人，直接发整个 ZIP。

**首次运行可能被系统拦截**——程序没有代码签名证书，Windows 或安全软件提示是正常现象，不是文件损坏：

| 现象 | 处理 |
|---|---|
| 蓝色窗口"Windows 已保护你的电脑" | 点「更多信息」→「仍要运行」 |
| 安全软件提示"未知程序 / 风险程序" | 选择允许运行或加入信任区；**不要关闭实时防护** |
| 提示需要更高权限 | 用**当前同一个账户**右键「以管理员身份运行」 |
| 提示"另一个安装窗口已打开" | 先关掉已打开的检查器弹窗再重试 |

## ⚠️ 能力边界（请先读这段）

**这不是系统级互斥，不要按"绝对互斥"理解或宣传。** 它能约束的只有**接入本工具的桌面快捷方式**：

- Clash 开机自启动、任务栏固定图标、直接运行 EXE、其他程序内部调用、重启后自动恢复应用、双击 PSD/JPG 的文件关联 —— **这些入口都可能绕过检查**。本工具不修改这些入口、服务、注册表或开机设置。（2.2.0 起开始菜单入口已纳入扫描，接入后同样受管。）
- 要让它真正管用，需要**自己关闭 Clash 的开机自启动**，并且日常只用接入过的图标启动 Adobe 与 Clash。
- 启动互斥锁只在同一登录会话内串行化启动序列，不长期持有应用生命周期锁。进程权限差异、软件的延迟子进程、多用户会话、改名进程都是边界情况，**不保证任何情况下两者都不能共存**。
- 程序通过**进程名**识别对方。Clash 或 Adobe 若改名、换内核，可能漏检；未知且未登记的软件不覆盖。
- 它**不解决网络问题**，只是帮你避免"两边同时开着"这个状态。

## 从源码运行 / 构建

需要 Windows 10/11 x64 与 Python 3.13。

```bash
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt

# 直接运行（未打包状态会拒绝执行真实安装，便于安全调试）
.venv\Scripts\python.exe src\adobe_clash_guard.py

# 打包为文件夹版
.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean AdobeClashGuard.spec
# 产物：dist/AdobeClashGuard/（exe + _internal/）
```

**必须保持 `--onedir`。** 若改回 `--onefile`，程序运行时会把自身解压到 `%TEMP%\_MEIxxxxxx`，退出时若安全软件正在扫描其中某个 DLL，删除会失败并弹 "Failed to remove temporary directory"；同时安装器会把 `bundle_root()` 判为空，只复制单个 exe，固定安装目录将不完整。

运行测试（使用模拟目录，不会动真实桌面图标）：

```bash
.venv\Scripts\python.exe tests\test_universal.py
```

`tools/verify_package.py` 是端到端验收脚本，需要在打包产物就绪后、于可写目录中运行。

## 工作原理

启动时按顺序做这几件事：

1. **识别**：读取两个桌面（顶层）与用户级 / 公共开始菜单（递归，跳过「启动」文件夹）里的 `.lnk`，Adobe 按目标 EXE 的厂商版本信息或"Adobe 路径 + 已知应用文件名"识别；Clash 按已知直接 EXE 名称识别。厂商信息只用于识别，**不是签名认证**。
2. **接入**：把识别到的图标改指向固定安装目录里的 `AdobeClashGuard.exe`，原始参数以附加参数形式保留，原文件先备份。
3. **启动前检查**：点击受管图标时，程序枚举进程名，若发现"对方"正在运行，弹出原生提示并退出；否则启动真正的目标。
4. **串行化**：同一时刻的多个启动请求通过命名互斥锁排队，避免两个受管图标几乎同时点击时互相放行。
5. **落位**：启动后最多短暂等待 8 秒确认进程可见，然后退出。

固定安装目录：`%LOCALAPPDATA%\AdobeClashGuard\v2.1`（目录名自 2.1 起保持不变，已接入的图标会自动沿用更新后的程序）。备份在其 `backups/`，状态在 `state.json`。

### 拦截名单：`blockers.txt`

固定安装目录里的 `blockers.txt` 是**纯文本**名单，用来告诉检查器"还有哪些进程算 Clash / 代理内核"：

```text
# 一行一个进程名，要带 .exe，不区分大小写
# 以 # 开头的行是注释，空行忽略
my-proxy.exe
sing-box.exe
```

**保存即生效，不用重新打包或重启。** 内置已包含 `clash-verge.exe`、`clash verge.exe`、`verge-mihomo.exe`、`verge-mihomo-alpha.exe`、`mihomo.exe`、`mihomo-alpha.exe`、`clash.exe`、`clash-meta.exe`，在文件里写不写都不影响。

**想确认拦截是否还在生效**：运行 `AdobeClashGuard.exe --scan <报告文件路径>`，报告里的 `tampered` 字段会列出**被其他程序改写过的受管图标**——安装器重建快捷方式时静默解除拦截，就靠这个字段发现。

**升级**：直接运行新版 `AdobeClashGuard.exe` 即可，它会替换固定目录里的程序并保留一份 `AdobeClashGuard.previous.exe` 用于回滚，**已接入的图标不需要重新接入**。

**恢复**：`Win+R` 输入 `%LOCALAPPDATA%\AdobeClashGuard\v2.1`，运行「恢复 Adobe 与 Clash 原图标.lnk」。

## 项目结构

```
AdobeClashGuard.spec     PyInstaller 打包配置（onedir）
requirements.txt         依赖版本
src/
  adobe_clash_guard.py   主程序（全部逻辑）
tests/
  test_universal.py      38 项自动测试
tools/
  verify_package.py      端到端验收脚本
docs/
  使用说明.md / .html    面向使用者的完整说明（含故障排查）
  重建说明.md            构建、依赖替换与测试细节
licenses/                第三方组件许可全文
```

## 已知限制

- **尚未在第二台物理电脑上做过安装验收**，也未验证物理鼠标拖放、Clash 自启动流程、正式安全软件兼容等场景。首次在别人电脑上使用前，请先确认两个方向的弹窗都能正常出现。
- 仅识别 Clash Verge / Mihomo 系（`clash-verge.exe`、`verge-mihomo.exe`、`verge-mihomo-alpha.exe`、`mihomo.exe`、`clash.exe`、`clash-meta.exe` 等）。其他类型的代理软件默认不识别，可在 `blockers.txt` 里补上进程名。
- 正向"Clash 拦截 Adobe"的自动化测试使用同名模拟进程触发，未启动真实 Clash。

## 更新记录

| 版本 | 主要变化 |
|---|---|
| **2.2.1** | ① 接入入口改为**静默模式**（`--install --yes`）：旧版动手前会弹一个是/否框，而它的默认焦点不在「是」上——按 Esc、点「否」或当成普通提示关掉都等于取消，这是"点了程序却没反应"的头号原因 ② 入口快捷方式写入 `RunAsUser` 标志，**双击即弹 UAC**，不必再去右键菜单里找「以管理员身份运行」 ③ 批次涉及公共开始菜单时若未提权，程序**自己请求提权**（`ShellExecute runas`）；提权被拒则明确报错并且不改任何图标 ④ 每次运行追加写入 `install-log.txt`（含时间、接入清单、失败原因）|
| **2.2.0** | ① 扫描范围扩到用户级与公共开始菜单（递归，跳过「启动」文件夹），堵上"从开始菜单启动 Clash 绕过检查"的旁路 ② 拦截进程名单外挂为 `blockers.txt`，加名字不用重新打包 ③ 内置名单补 `verge-mihomo-alpha.exe`、`mihomo-alpha.exe`，切换 Alpha 内核不再静默失效 ④ 安装时自动整理 `state.json`（清重复与无效记录）⑤ `--scan` 报告新增 `tampered`，报出被安装器改写的受管图标 |
| 2.1.1 | 改为文件夹版（`--onedir`）分发，不再向 `%TEMP%` 自解压 |
| 2.1.0 | 增加反向拦截（Clash 启动前检查 Adobe）；固定安装目录 |

## 许可

本项目以 **GNU GPL-3.0** 发布，全文见 [LICENSE](LICENSE)。

这意味着你可以自由使用、修改、分发，甚至可以收费分发；但**衍生作品必须以同样的许可开源**。

第三方组件分别遵守各自的许可，全文收录在 [`licenses/`](licenses/)：

| 组件 | 许可 |
|---|---|
| [pylnk3](https://github.com/strayge/pylnk3) 0.4.3 | LGPL-3.0 |
| PyInstaller 6.22.2 | GPL-2.0-or-later，附打包例外 |
| Python | PSF License |

作者保留版权。如需在 GPL-3.0 条款之外使用（例如闭源集成或商业授权），请通过 Issue 联系。
