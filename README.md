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
| 自动识别 | 只读扫描当前用户桌面与公共桌面顶层的 `.lnk`，自动挑出所有 Adobe 与 Clash 入口，无需手动选 |
| 不改原入口 | 保留原名称、图标、参数、工作目录、窗口状态与管理员标记；拖入文件时安全追加参数 |
| 先备份再改 | 每一步先备份并校验，首个失败立即停止；提供一键恢复入口 |
| 不常驻后台 | 检查完就退出，不是常驻监控进程，不占内存 |
| 文件夹版分发 | 2.1.1 起改用 `--onedir`，运行时不向 `%TEMP%` 自解压，不再出现"无法删除临时目录"警告，启动也更快 |
| 可验证 | 24 项自动测试 + 端到端验收脚本，见 [`tests/`](tests/) 与 [`tools/`](tools/) |

## 下载使用

到 [Releases](../../releases/latest) 下载 `AdobeClashGuard-v2.1.1-win64.zip`，完整解压后：

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

- Clash 开机自启动、开始菜单 / 任务栏原图标、直接运行 EXE、其他程序内部调用、重启后自动恢复应用、双击 PSD/JPG 的文件关联 —— **这些入口都可能绕过检查**。本工具不修改这些入口、服务、注册表或开机设置。
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

1. **识别**：读取桌面顶层 `.lnk`，Adobe 按目标 EXE 的厂商版本信息或"Adobe 路径 + 已知应用文件名"识别；Clash 按已知直接 EXE 名称识别。厂商信息只用于识别，**不是签名认证**。
2. **接入**：把识别到的图标改指向固定安装目录里的 `AdobeClashGuard.exe`，原始参数以附加参数形式保留，原文件先备份。
3. **启动前检查**：点击受管图标时，程序枚举进程名，若发现"对方"正在运行，弹出原生提示并退出；否则启动真正的目标。
4. **串行化**：同一时刻的多个启动请求通过命名互斥锁排队，避免两个受管图标几乎同时点击时互相放行。
5. **落位**：启动后最多短暂等待 8 秒确认进程可见，然后退出。

固定安装目录：`%LOCALAPPDATA%\AdobeClashGuard\v2.1`（目录名自 2.1 起保持不变，已接入的图标会自动沿用更新后的程序）。备份在其 `backups/`，状态在 `state.json`。

**升级**：直接运行新版 `AdobeClashGuard.exe` 即可，它会替换固定目录里的程序并保留一份 `AdobeClashGuard.previous.exe` 用于回滚，**已接入的图标不需要重新接入**。

**恢复**：`Win+R` 输入 `%LOCALAPPDATA%\AdobeClashGuard\v2.1`，运行「恢复 Adobe 与 Clash 原图标.lnk」。

## 项目结构

```
AdobeClashGuard.spec     PyInstaller 打包配置（onedir）
requirements.txt         依赖版本
src/
  adobe_clash_guard.py   主程序（全部逻辑）
tests/
  test_universal.py      24 项自动测试
tools/
  verify_package.py      端到端验收脚本
docs/
  使用说明.md / .html    面向使用者的完整说明（含故障排查）
  重建说明.md            构建、依赖替换与测试细节
licenses/                第三方组件许可全文
```

## 已知限制

- **尚未在第二台物理电脑上做过安装验收**，也未验证物理鼠标拖放、Clash 自启动流程、正式安全软件兼容等场景。首次在别人电脑上使用前，请先确认两个方向的弹窗都能正常出现。
- 仅识别 Clash Verge / Mihomo 系（`clash-verge.exe`、`verge-mihomo.exe`、`mihomo.exe`、`clash.exe`、`clash-meta.exe` 等）。其他类型的代理软件不会被识别。
- 正向"Clash 拦截 Adobe"的自动化测试使用同名模拟进程触发，未启动真实 Clash。

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
