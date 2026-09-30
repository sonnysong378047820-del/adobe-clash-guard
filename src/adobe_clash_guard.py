"""Portable Windows desktop Adobe guard, with explicit install and restoration."""
import ctypes
from ctypes import wintypes as wt
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid

import pylnk3

VERSION = "2.1.1"
BLOCKERS = {"clash-verge.exe", "clash verge.exe", "verge-mihomo.exe", "mihomo.exe", "clash.exe", "clash-meta.exe"}
ADOBE_PROCESS_NAMES = {"photoshop.exe", "afterfx.exe", "illustrator.exe", "adobe media encoder.exe", "adobe premiere pro.exe", "indesign.exe", "acrobat.exe", "acrord32.exe", "audition.exe", "adobe audition.exe", "bridge.exe", "lightroom.exe", "adobe animate.exe", "animate.exe", "character animator.exe", "adobe character animator.exe", "adobe fresco.exe", "adobe dimension.exe", "adobe substance 3d painter.exe", "adobe substance 3d designer.exe", "adobe substance 3d sampler.exe", "adobe substance 3d stager.exe", "creative cloud.exe", "adobe express.exe"}
CLASH_TARGET_NAMES = {"clash verge.exe", "clash-verge.exe", "clash.exe", "clash-meta.exe", "mihomo.exe", "verge-mihomo.exe"}
KNOWN_ADOBE_EXES = {"photoshop.exe", "afterfx.exe", "illustrator.exe", "adobe media encoder.exe", "adobe premiere pro.exe", "indesign.exe", "acrobat.exe", "acrord32.exe", "audition.exe", "adobe audition.exe", "bridge.exe", "lightroom.exe", "adobe animate.exe", "animate.exe", "character animator.exe", "adobe character animator.exe", "adobe fresco.exe", "adobe dimension.exe", "adobe substance 3d painter.exe", "adobe substance 3d designer.exe", "adobe substance 3d sampler.exe", "adobe substance 3d stager.exe", "creative cloud.exe", "adobe express.exe"}
SELF = Path(sys.executable) if getattr(sys, "frozen", False) else Path(__file__).resolve()


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load(path, default=None):
    return json.loads(Path(path).read_text(encoding="utf-8")) if Path(path).is_file() else default


def save(path, value):
    path = Path(path)
    temp = path.with_suffix(path.suffix + ".new")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)


def state_root():
    base = os.environ.get("LOCALAPPDATA")
    if not base:
        raise RuntimeError("无法定位当前用户的本地应用数据目录。")
    # Fixed path (deliberately not renamed per release): already-guarded shortcuts keep working
    # across updates because an updated build replaces the program at this exact path.
    return Path(base) / "AdobeClashGuard" / "v2.1"


def desktops():
    # Windows resolves relocated and OneDrive desktops for the active user.
    shell = ctypes.WinDLL("shell32", use_last_error=True)
    shell.SHGetFolderPathW.argtypes = [wt.HWND, ctypes.c_int, wt.HANDLE, wt.DWORD, wt.LPWSTR]
    result = []
    for key in (0x10, 0x19):
        buffer = ctypes.create_unicode_buffer(32768)
        hr = shell.SHGetFolderPathW(None, key, None, 0, buffer)
        if hr != 0:
            raise OSError("无法读取 Windows 桌面位置：" + str(hr))
        path = Path(buffer.value)
        if path.is_dir() and path not in result:
            result.append(path)
    return result


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [("dwSize", wt.DWORD), ("cntUsage", wt.DWORD), ("th32ProcessID", wt.DWORD), ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wt.DWORD), ("cntThreads", wt.DWORD), ("th32ParentProcessID", wt.DWORD), ("pcPriClassBase", wt.LONG), ("dwFlags", wt.DWORD), ("szExeFile", wt.WCHAR * 260)]


def process_names():
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateToolhelp32Snapshot.argtypes = [wt.DWORD, wt.DWORD]
    k.CreateToolhelp32Snapshot.restype = wt.HANDLE
    k.Process32FirstW.argtypes = [wt.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    k.Process32NextW.argtypes = [wt.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    k.CloseHandle.argtypes = [wt.HANDLE]
    handle = k.CreateToolhelp32Snapshot(2, 0)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)
        if not k.Process32FirstW(handle, ctypes.byref(entry)):
            raise ctypes.WinError(ctypes.get_last_error())
        names = [entry.szExeFile.lower()]
        while k.Process32NextW(handle, ctypes.byref(entry)):
            names.append(entry.szExeFile.lower())
        if ctypes.get_last_error() != 18:
            raise ctypes.WinError(ctypes.get_last_error())
        return names
    finally:
        k.CloseHandle(handle)


def company_name(path):
    """Read executable vendor metadata. This is identification, not signature verification."""
    v = ctypes.WinDLL("version", use_last_error=True)
    v.GetFileVersionInfoSizeW.argtypes = [wt.LPCWSTR, ctypes.POINTER(wt.DWORD)]
    v.GetFileVersionInfoSizeW.restype = wt.DWORD
    v.GetFileVersionInfoW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD, wt.LPVOID]
    v.VerQueryValueW.argtypes = [wt.LPCVOID, wt.LPCWSTR, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wt.UINT)]
    size = v.GetFileVersionInfoSizeW(str(path), None)
    if not size:
        return ""
    buffer = ctypes.create_string_buffer(size)
    if not v.GetFileVersionInfoW(str(path), 0, size, buffer):
        return ""
    ptr, length = ctypes.c_void_p(), wt.UINT()
    pairs = [(0x409, 0x4B0), (0x409, 0x4E4)]
    if v.VerQueryValueW(buffer, "\\VarFileInfo\\Translation", ctypes.byref(ptr), ctypes.byref(length)) and length.value >= 4:
        values = ctypes.cast(ptr, ctypes.POINTER(wt.WORD))
        pairs = [(values[i], values[i + 1]) for i in range(0, length.value // 2 - 1, 2)] + pairs
    for language, codepage in pairs:
        query = "\\StringFileInfo\\%04x%04x\\CompanyName" % (language, codepage)
        if v.VerQueryValueW(buffer, query, ctypes.byref(ptr), ctypes.byref(length)) and length.value:
            return ctypes.wstring_at(ptr.value).strip()
    return ""


def adobe_target(path, vendor):
    path = Path(path)
    lower = path.name.lower()
    if path.suffix.lower() != ".exe" or not path.is_file():
        return False
    if any(word in lower for word in ("uninstall", "setup", "installer", "crash", "updater")):
        return False
    if vendor.lower().startswith("adobe"):
        return True
    return lower in KNOWN_ADOBE_EXES and any(part.lower() == "adobe" or part.lower().startswith("adobe ") for part in path.parts[:-1])


def inspect_shortcut(path, vendor_reader=company_name):
    link = pylnk3.parse(str(path))
    target_text = os.path.expandvars(link.path or "")
    target = Path(target_text)
    target_lower = target.name.lower()
    if target_lower in {"adobelaunchguard.exe", "adobeclashguard.exe", "clashguard.exe"}:
        return {"source": str(path), "name": path.stem, "status": "already_guarded", "reason": "已接入旧版或新版检查器，保留不动；旧版可能只有单向检查"}
    if target.is_file() and target.suffix.lower() == ".exe" and target_lower in CLASH_TARGET_NAMES:
        return {"source": str(path), "name": path.stem, "status": "candidate", "kind": "clash", "target": str(target), "raw_arguments": link.arguments or "", "workdir": os.path.expandvars(link.work_dir or str(target.parent)), "icon": link.icon or str(target), "icon_index": link.icon_index, "window_mode": link.window_mode, "run_as": bool(link.link_flags.RunAsUser), "source_sha256": sha(path), "vendor": "Clash/Mihomo/Verge known executable"}
    vendor = vendor_reader(target) if target.is_file() and target.suffix.lower() == ".exe" else ""
    if not adobe_target(target, vendor):
        return None
    return {"source": str(path), "name": path.stem, "status": "candidate", "kind": "adobe", "target": str(target), "raw_arguments": link.arguments or "", "workdir": os.path.expandvars(link.work_dir or str(target.parent)), "icon": link.icon or str(target), "icon_index": link.icon_index, "window_mode": link.window_mode, "run_as": bool(link.link_flags.RunAsUser), "source_sha256": sha(path), "vendor": vendor or "Adobe 路径与已知应用名称匹配"}


def scan(roots=None, vendor_reader=company_name):
    result = {"version": VERSION, "scope": [], "candidates": [], "already_guarded": [], "skipped": []}
    seen = set()
    for root in (desktops() if roots is None else roots):
        root = Path(root)
        result["scope"].append(str(root))
        for path in sorted(root.glob("*.lnk")):
            if str(path.resolve()).casefold() in seen:
                continue
            seen.add(str(path.resolve()).casefold())
            try:
                item = inspect_shortcut(path, vendor_reader)
                if item:
                    result["candidates" if item["status"] == "candidate" else "already_guarded"].append(item)
                elif "adobe" in path.name.lower():
                    result["skipped"].append({"source": str(path), "reason": "目标不是可确认的 Adobe 应用 EXE，或是卸载/安装入口"})
            except Exception as exc:
                result["skipped"].append({"source": str(path), "reason": str(exc)})
    return result


def adobe_running(names, extra=()):
    lower = {name.lower() for name in names}
    return sorted(lower & (ADOBE_PROCESS_NAMES | {name.lower() for name in extra}))


def clash_running(names):
    return sorted({name.lower() for name in names} & BLOCKERS)


def make_link(path, executable, key, item):
    kind = item.get("kind", "adobe")
    link = pylnk3.for_file(str(executable), arguments="--launch " + key,
                         description=("Adobe 启动检查：请先退出 Clash Verge" if kind == "adobe" else "Clash 启动检查：请先退出 Adobe"), icon_file=item["icon"],
                         icon_index=item["icon_index"], work_dir=str(Path(executable).parent), window_mode=item["window_mode"])
    link.link_flags.RunAsUser = item["run_as"]
    link.save(str(path))
    check = pylnk3.parse(str(path))
    assert Path(check.path) == Path(executable)
    assert check.arguments == "--launch " + key
    assert check.icon == item["icon"] and check.icon_index == item["icon_index"]
    assert bool(check.link_flags.RunAsUser) == item["run_as"]


def launch(app, files, scan_processes=process_names, dialog=None, start=subprocess.Popen):
    dialog = message if dialog is None else dialog
    kind = app.get("kind", "adobe")
    while True:
        names = scan_processes()
        blockers = clash_running(names) if kind == "adobe" else adobe_running(names, app.get("adobe_process_names", ()))
        if not blockers:
            break
        if kind == "adobe":
            text = "Clash Verge 或代理内核还在运行。\n\n本次 Adobe 启动已被拦住。\n请从右下角托盘右键退出 Clash Verge，再点击「重试」。\n只关闭主窗口不算退出；本工具不会替你关闭 Clash。\n\n检测到：" + "、".join(blockers) + "\n\n待启动：" + app["name"]
            title = "Adobe 启动检查：请先退出 Clash Verge"
        else:
            text = "Adobe 软件还在运行。\n\n本次 Clash 启动已被拦住。\n请先退出所有 Adobe 软件，再点击「重试」。\n本工具不会替你关闭 Adobe。\n\n检测到：" + "、".join(blockers) + "\n\n待启动：" + app["name"]
            title = "Clash 启动检查：请先退出 Adobe"
        answer = dialog(text, title, 0x135)
        if answer != 4:
            return 2
    target = Path(app["target"])
    if not target.is_file():
        raise FileNotFoundError("启动路径不存在，请恢复原入口后重新安装检查器：" + str(target))
    command = subprocess.list2cmdline([str(target)])
    if app.get("raw_arguments"):
        command += " " + app["raw_arguments"]
    if files:
        command += " " + subprocess.list2cmdline(list(files))
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = {"Normal": 1, "Maximized": 3, "Minimized": 7}.get(app.get("window_mode"), 1)
    start(command, executable=str(target), cwd=app.get("workdir") or str(target.parent), startupinfo=startup, close_fds=True, shell=False)
    return 0


def serialized_launch(app, files):
    """Serialize guarded start attempts; do not pretend to control other launch routes."""
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = [wt.LPVOID, wt.BOOL, wt.LPCWSTR]
    kernel.CreateMutexW.restype = wt.HANDLE
    kernel.WaitForSingleObject.argtypes = [wt.HANDLE, wt.DWORD]
    kernel.ReleaseMutex.argtypes = [wt.HANDLE]
    kernel.CloseHandle.argtypes = [wt.HANDLE]
    handle = kernel.CreateMutexW(None, False, "Local\\AdobeClashGuard-Launch")
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    acquired = False
    try:
        while True:
            wait = kernel.WaitForSingleObject(handle, 0)
            if wait in (0, 0x80):
                acquired = True
                break
            if wait != 0x102:
                raise ctypes.WinError(ctypes.get_last_error())
            answer = message("另一启动检查窗口或启动过程正在进行。\n请先完成或取消那个窗口，再点击重试。\n\n本次暂未启动：" + app["name"], "双向启动检查：等待其他检查完成", 0x135)
            if answer != 4:
                return 2
        # Mutex covers rechecking, process creation and a bounded visibility check.
        children = []
        def start(*args, **kwargs):
            child = subprocess.Popen(*args, **kwargs)
            children.append(child)
            return child
        result = launch(app, files, start=start)
        if result == 0:
            target_name = Path(app["target"]).name.lower()
            deadline = time.monotonic() + 8
            while target_name not in process_names():
                if children and children[0].poll() is not None:
                    break
                if time.monotonic() >= deadline:
                    break
                time.sleep(0.05)
        return result
    finally:
        if acquired:
            kernel.ReleaseMutex(handle)
        kernel.CloseHandle(handle)


def install_batch(items, executable, root):
    """Only called after explicit UI approval for at most ten exact paths."""
    if not 0 < len(items) <= 10:
        raise ValueError("每批仅允许 1 到 10 个快捷方式")
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    state = load(root / "state.json", {"version": VERSION, "entries": {}})
    batch = root / "backups" / (datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8])
    batch.mkdir(parents=True)
    prepared = []
    for item in items:
        source = Path(item["source"])
        if sha(source) != item["source_sha256"]:
            raise RuntimeError("图标在扫描后发生变化，停止：" + str(source))
        key = uuid.uuid4().hex
        original = batch / (key + "-original.lnk")
        staged = batch / (key + "-guarded.lnk")
        shutil.copy2(source, original)
        if sha(original) != item["source_sha256"]:
            raise RuntimeError("备份校验失败，停止：" + str(source))
        make_link(staged, executable, key, item)
        record = dict(item, key=key, backup=str(original), staged=str(staged), staged_sha256=sha(staged), state="prepared")
        state["entries"][key] = record
        prepared.append(record)
    save(root / "state.json", state)  # Persist recovery data before touching personal files.
    changed = []
    for record in prepared:
        source = Path(record["source"])
        if sha(source) != record["source_sha256"]:
            raise RuntimeError("原图标变化，停止；备份：" + str(batch))
        try:
            shutil.copyfile(record["staged"], source)
            if sha(source) != record["staged_sha256"]:
                raise RuntimeError("写入后校验失败")
        except Exception as exc:
            raise RuntimeError("停止后续写入。\n出错图标：" + str(source) + "\n已完成：" + str(len(changed)) + "\n备份：" + str(batch) + "\n原因：" + str(exc)) from exc
        record["state"] = "installed"
        changed.append(str(source))
        save(root / "state.json", state)
    return changed


def restore_batch(records, root):
    if not 0 < len(records) <= 10:
        raise ValueError("每批仅允许恢复 1 到 10 个图标")
    root = Path(root)
    state = load(root / "state.json")
    batch = root / "backups" / ("before-restore-" + datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8])
    batch.mkdir(parents=True)
    for record in records:
        source = Path(record["source"])
        if sha(record["backup"]) != record["source_sha256"]:
            raise RuntimeError("原备份损坏，停止")
        if sha(source) != record["staged_sha256"]:
            raise RuntimeError("图标被其他程序修改，停止以免覆盖：" + str(source))
        saved = batch / (record["key"] + ".lnk")
        shutil.copy2(source, saved)
        if sha(saved) != record["staged_sha256"]:
            raise RuntimeError("恢复前备份失败")
    changed = []
    for record in records:
        source = Path(record["source"])
        if sha(source) != record["staged_sha256"]:
            raise RuntimeError("图标变化，停止恢复")
        shutil.copyfile(record["backup"], source)
        if sha(source) != record["source_sha256"]:
            raise RuntimeError("恢复校验失败，停止")
        state["entries"][record["key"]]["state"] = "restored"
        save(root / "state.json", state)
        changed.append(str(source))
    return changed


def message(text, title, flags=0x40):
    user = ctypes.WinDLL("user32", use_last_error=True)
    user.MessageBoxW.argtypes = [wt.HWND, wt.LPCWSTR, wt.LPCWSTR, wt.UINT]
    answer = user.MessageBoxW(None, text, title, flags | 0x10000 | 0x40000)
    if not answer:
        raise ctypes.WinError(ctypes.get_last_error())
    return answer


def confirm_paths(items, action):
    text = "此操作非常危险，可能导致不可逆的数据丢失！\n\n本批将" + action + "以下快捷方式（仅更改启动目标，不改 Adobe 安装文件）：\n\n"
    text += "\n".join(item["source"] for item in items)
    text += "\n\n将先逐个备份并校验。错误配置可能导致图标无法启动。\n仅本批受管图标有双向检查；自启动、开始菜单、直接 EXE 等入口不受控。\n同时点击已序列化，但进程权限/延迟启动等边界仍不能保证系统级互斥。\n是否确认继续？"
    return message(text, "Adobe 启动检查 — 确认" + action, 0x134) == 6


def write_scan_report(report, destination):
    save(destination, report)


def bundle_root():
    """Folder-build root of the running program, or None for a single-file build.

    PyInstaller 6 folder builds keep everything in an adjacent ``_internal`` directory and
    point ``sys._MEIPASS`` either at the program folder or at that ``_internal`` folder,
    while single-file builds extract to a temporary directory instead.
    """
    if not getattr(sys, "frozen", False):
        return None
    app_dir = SELF.parent
    internal = app_dir / "_internal"
    if not internal.is_dir():
        return None
    meipass = Path(getattr(sys, "_MEIPASS", app_dir)).resolve()
    return app_dir if meipass in (app_dir.resolve(), internal.resolve()) else None


def install_program(root):
    """Copy or update this program in the fixed install root; one rollback file is kept.

    Folder builds no longer unpack themselves into %TEMP%, so no temporary-directory
    cleanup warning can appear. The program keeps a stable path so shortcuts that were
    guarded earlier automatically use the updated build.
    """
    if not getattr(sys, "frozen", False):
        raise RuntimeError("安装请使用打包后的 AdobeClashGuard.exe")
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    target = root / "AdobeClashGuard.exe"
    if SELF.resolve() == target.resolve():
        return target
    source = bundle_root()
    try:
        if target.exists():
            shutil.copy2(target, root / "AdobeClashGuard.previous.exe")
        shutil.copy2(SELF, target)
        if source is not None and source.resolve() != root.resolve():
            staged = root / "_internal.new"
            if staged.exists():
                shutil.rmtree(staged)
            shutil.copytree(source / "_internal", staged)
            final = root / "_internal"
            if final.exists():
                shutil.rmtree(final)
            staged.rename(final)
    except OSError as exc:
        raise RuntimeError("无法写入固定安装目录。请先关闭正在运行的检查器窗口（含其他 Adobe/Clash 启动检查弹窗），或改用当前同一账户右键以管理员身份运行。\n" + str(exc)) from exc
    if sha(target) != sha(SELF):
        raise RuntimeError("检查器复制校验失败")
    return target


def helper_links(root, target):
    for flag, name in [("--install", "补充接入 Adobe 与 Clash 图标"), ("--restore", "恢复 Adobe 与 Clash 原图标")]:
        pylnk3.for_file(str(target), str(Path(root) / (name + ".lnk")), arguments=flag, work_dir=str(root))


def install_ui():
    root = state_root()
    # Program first: an updated build replaces the same fixed path, so already-guarded
    # shortcuts pick it up without being touched again.
    target = install_program(root)
    report = scan()  # Read-only scan of the two desktop roots.
    candidates = report["candidates"]
    helper_links(root, target)
    if not candidates:
        message("程序已就位并校验：" + VERSION + "\n固定安装目录：" + str(root) + "\n\n未发现需要新接入的桌面快捷方式。\n已接入并保留：" + str(len(report["already_guarded"])) + " 个\n跳过：" + str(len(report["skipped"])) + " 个\n\n已接入的图标会直接使用该目录里的程序，无需重新接入。\n扫描仅覆盖当前用户桌面和公共桌面的顶层 .lnk。", "检查器已更新")
        return 0
    count = 0
    for offset in range(0, len(candidates), 10):
        batch = candidates[offset:offset + 10]
        if not confirm_paths(batch, "接入"):
            message("已取消后续操作。已完成 " + str(count) + " 个，其余未修改。", "操作已停止")
            return 2
        changed = install_batch(batch, target, root)
        count += len(changed)
    ctypes.WinDLL("shell32").SHChangeNotify(0x08000000, 0, None, None)
    message("接入成功，并已校验：" + str(count) + " 个图标。\n\n版本：" + VERSION + "\n固定安装目录：" + str(root) + "\n备份：该目录内 backups\n\n分享压缩包/下载目录可删除，固定安装目录不能删除，也不能单独移动其中的 AdobeClashGuard.exe。\n以后用原桌面图标启动即可；新增 Adobe 或 Clash 图标可重新运行本工具。\n跳过/已接入：" + str(len(report["skipped"])) + "/" + str(len(report["already_guarded"])), "安装成功")
    return 0


def restore_ui():
    root = state_root()
    state = load(root / "state.json", {"entries": {}})
    records = []
    for record in state["entries"].values():
        path = Path(record["source"])
        if record["state"] != "restored" and path.is_file() and sha(path) == record["staged_sha256"]:
            records.append(record)
    if not records:
        message("没有可恢复的受管图标。已删除或被其他程序改动的图标不会覆盖。", "恢复结果")
        return 0
    count = 0
    for offset in range(0, len(records), 10):
        batch = records[offset:offset + 10]
        if not confirm_paths(batch, "恢复"):
            return 2
        count += len(restore_batch(batch, root))
    ctypes.WinDLL("shell32").SHChangeNotify(0x08000000, 0, None, None)
    message("已恢复并校验 " + str(count) + " 个原图标。备份与程序仍保留，未删除任何文件。", "恢复完成")
    return 0


def main(args=None):
    args = sys.argv[1:] if args is None else args
    try:
        if args and args[0] == "--launch":
            if len(args) < 2:
                raise RuntimeError("缺少图标标识")
            state = load(SELF.parent / "state.json")
            if not state or args[1] not in state["entries"]:
                raise RuntimeError("启动配置不存在，请重新安装或恢复原图标。")
            app = dict(state["entries"][args[1]])
            if app.get("kind") == "clash":
                app["adobe_process_names"] = [Path(record["target"]).name.lower() for record in state["entries"].values() if record.get("kind", "adobe") == "adobe" and record.get("state") != "restored"]
            return serialized_launch(app, args[2:])
        if args and args[0] == "--scan":
            if len(args) != 2:
                raise RuntimeError("只读扫描需指定报告文件路径")
            write_scan_report(scan(), Path(args[1]))
            return 0
        # No UAC relaunch: avoid scanning an administrator's desktop under alternate credentials.
        if not args or args in (["--install"], ["--restore"]):
            k = ctypes.WinDLL("kernel32", use_last_error=True)
            k.CreateMutexW.argtypes = [wt.LPVOID, wt.BOOL, wt.LPCWSTR]
            k.CreateMutexW.restype = wt.HANDLE
            handle = k.CreateMutexW(None, True, "Local\\AdobeClashGuard-Installer")
            if not handle:
                raise ctypes.WinError(ctypes.get_last_error())
            k.ReleaseMutex.argtypes = [wt.HANDLE]
            k.CloseHandle.argtypes = [wt.HANDLE]
            try:
                if ctypes.get_last_error() == 183:
                    raise RuntimeError("另一个安装窗口已打开，请先完成或取消。")
                return restore_ui() if args == ["--restore"] else install_ui()
            finally:
                k.ReleaseMutex(handle)
                k.CloseHandle(handle)
        raise RuntimeError("未知参数")
    except Exception as exc:
        message("操作未完成，已停止后续处理。\n\n" + str(exc) + "\n\n不要关闭安全软件。若公共桌面写权限不足，可用当前同一账户右键以管理员身份运行。\n已生成的备份保留在固定安装目录。", "Adobe 启动检查 — 错误", 0x10)
        return 1

if __name__ == "__main__":
    sys.exit(main())
