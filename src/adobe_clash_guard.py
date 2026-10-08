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

VERSION = "2.2.1"
# Built-in blocker process names. More can be appended without repackaging through
# the plain-text "blockers.txt" kept in the fixed install directory; see load_blockers().
# The alpha core (verge-mihomo-alpha.exe) is listed here because a user can switch
# cores inside Clash Verge, and an unrecognized core would fail silently.
BLOCKERS = {"clash-verge.exe", "clash verge.exe", "verge-mihomo.exe", "verge-mihomo-alpha.exe", "mihomo.exe", "mihomo-alpha.exe", "clash.exe", "clash-meta.exe"}
ADOBE_PROCESS_NAMES = {"photoshop.exe", "afterfx.exe", "illustrator.exe", "adobe media encoder.exe", "adobe premiere pro.exe", "indesign.exe", "acrobat.exe", "acrord32.exe", "audition.exe", "adobe audition.exe", "bridge.exe", "lightroom.exe", "adobe animate.exe", "animate.exe", "character animator.exe", "adobe character animator.exe", "adobe fresco.exe", "adobe dimension.exe", "adobe substance 3d painter.exe", "adobe substance 3d designer.exe", "adobe substance 3d sampler.exe", "adobe substance 3d stager.exe", "creative cloud.exe", "adobe express.exe"}
CLASH_TARGET_NAMES = {"clash verge.exe", "clash-verge.exe", "clash.exe", "clash-meta.exe", "mihomo.exe", "mihomo-alpha.exe", "verge-mihomo.exe", "verge-mihomo-alpha.exe"}
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


BLOCKERS_FILE = "blockers.txt"
BLOCKERS_TEMPLATE = """\
# Adobe × Clash 互斥检查 —— 额外拦截进程名单
#
# 作用：告诉检查器还有哪些进程算「Clash / 代理内核」。
# 格式：一行一个进程名，要带 .exe；不区分大小写；
#       以 # 开头的行是注释；空行会被忽略。
# 生效：保存后立即生效，不需要重新安装或重启本工具。
#
# 程序已内置以下名称（这里不写也照样拦，写了也不冲突）：
#   clash-verge.exe          verge-mihomo.exe        verge-mihomo-alpha.exe
#   clash verge.exe          mihomo.exe              mihomo-alpha.exe
#   clash.exe                clash-meta.exe
#
# 示例：去掉行首的 # 即可启用
# my-proxy.exe
# sing-box.exe
#
"""


def blockers_path(root=None):
    return (Path(root) if root is not None else state_root()) / BLOCKERS_FILE


def load_blockers(root=None):
    """Built-in blocker names plus anything the user appended to blockers.txt.

    The file sits next to the program rather than inside the bundle, so it
    survives updates and can be edited without repackaging. A missing or
    unreadable file simply falls back to the built-in names.
    """
    names = set(BLOCKERS)
    try:
        text = blockers_path(root).read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError):
        return names
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            names.add(line.casefold())
    return names


def ensure_blockers_file(root):
    """Write the editable list once. An existing file is never overwritten."""
    path = blockers_path(root)
    if path.exists():
        return False
    try:
        path.write_text(BLOCKERS_TEMPLATE, encoding="utf-8")
    except OSError:
        return False
    return True


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


def start_menu_roots():
    """Current-user and all-user Start Menu program folders.

    Clash Verge's installer rebuilds its Start Menu entry on every update, which
    opened a live bypass: launching Clash from the Start Menu or Win-key search
    skipped the guard entirely. These folders are scanned recursively.
    """
    shell = ctypes.WinDLL("shell32", use_last_error=True)
    shell.SHGetFolderPathW.argtypes = [wt.HWND, ctypes.c_int, wt.HANDLE, wt.DWORD, wt.LPWSTR]
    result = []
    for key in (0x02, 0x17):  # CSIDL_PROGRAMS (user), CSIDL_COMMON_PROGRAMS (all users)
        buffer = ctypes.create_unicode_buffer(32768)
        if shell.SHGetFolderPathW(None, key, None, 0, buffer) != 0:
            continue
        path = Path(buffer.value)
        if path.is_dir() and path not in result:
            result.append(path)
    return result


def in_startup(path):
    """Sign-in startup entries stay untouched: guarding them would only add pop-ups."""
    return any(part.casefold() == "startup" for part in Path(path).parts)


# The Start Menu holds dozens of unrelated links; only report skips that match the subject.
NOTEWORTHY = ("adobe", "clash", "mihomo", "verge", "photoshop", "illustrator", "after effects",
              "premiere", "media encoder", "lightroom", "indesign", "audition", "acrobat",
              "animate", "substance", "fresco", "dimension", "bridge", "creative cloud")


def noteworthy(path):
    name = Path(path).name.casefold()
    return any(word in name for word in NOTEWORTHY)


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
    """Read-only scan of shortcut locations.

    ``roots=None`` covers both desktop folders plus both Start Menu program
    folders, the latter recursively but skipping sign-in "Startup" folders.
    An explicit ``roots`` list is scanned at top level only; each item may be a
    path or a ``(path, recursive)`` pair.
    """
    result = {"version": VERSION, "scope": [], "candidates": [], "already_guarded": [], "skipped": []}
    if roots is None:
        targets = [(path, False) for path in desktops()] + [(path, True) for path in start_menu_roots()]
    else:
        targets = [(Path(item[0]), bool(item[1])) if isinstance(item, (tuple, list)) else (Path(item), False) for item in roots]
    seen = set()
    for root, recursive in targets:
        result["scope"].append(str(root))
        for path in sorted(root.glob("**/*.lnk" if recursive else "*.lnk")):
            if recursive and in_startup(path):
                continue
            marker = str(path.resolve()).casefold()
            if marker in seen:
                continue
            seen.add(marker)
            try:
                item = inspect_shortcut(path, vendor_reader)
                if item:
                    result["candidates" if item["status"] == "candidate" else "already_guarded"].append(item)
                elif noteworthy(path):
                    result["skipped"].append({"source": str(path), "reason": "目标不是可确认的 Adobe 应用 EXE，或是卸载/安装入口"})
            except Exception as exc:
                if noteworthy(path):
                    result["skipped"].append({"source": str(path), "reason": str(exc)})
    return result


def adobe_running(names, extra=()):
    lower = {name.lower() for name in names}
    return sorted(lower & (ADOBE_PROCESS_NAMES | {name.lower() for name in extra}))


def clash_running(names, blockers=None):
    """Blocker names found among running processes; ``blockers`` overrides the file."""
    known = load_blockers() if blockers is None else {name.casefold() for name in blockers}
    return sorted({name.lower() for name in names} & known)


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
    """Only called for at most ten paths taken from a fresh scan.

    Interactive runs arrive here after the 是/否 dialog. ``--install --yes`` runs
    arrive directly: the user already approved by launching that entry as
    administrator, and a second dialog is the step people most often mis-click.
    """
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


def prune_state(root, version=VERSION):
    """Tidy the bookkeeping file. Only unusable recovery data is ever dropped.

    Drops duplicate ``prepared`` rows left behind by an aborted install, promotes
    rows whose shortcut turns out to be guarded already, and marks rows that an
    external program rewrote -- which is precisely what a Clash Verge update does.
    """
    root = Path(root)
    state = load(root / "state.json")
    if not state or not isinstance(state.get("entries"), dict):
        return {"removed": 0, "fixed": 0}
    entries = state["entries"]
    installed = {str(Path(r.get("source", "")).resolve()).casefold() for r in entries.values() if r.get("state") == "installed"}
    removed, fixed = [], []
    for key, record in list(entries.items()):
        source = Path(record.get("source", ""))
        try:
            current = sha(source) if source.is_file() else None
        except OSError:
            current = None
        if record.get("state") == "prepared":
            if str(source.resolve()).casefold() in installed:
                removed.append(key)            # same shortcut is guarded under another key
            elif current == record.get("source_sha256"):
                removed.append(key)            # the write never landed; nothing to restore
            elif current == record.get("staged_sha256"):
                record["state"] = "installed"  # guarded in fact, only the flag was stale
                fixed.append(key)
        elif record.get("state") == "installed" and current is not None and current != record.get("staged_sha256"):
            record["state"] = "superseded"     # rewritten by someone else; no longer managed
            fixed.append(key)
    for key in removed:
        del entries[key]
    state["version"] = version
    save(root / "state.json", state)
    return {"removed": len(removed), "fixed": len(fixed)}


def tampered_entries(root):
    """Managed shortcuts that no longer match what the guard installed.

    An installer that rewrites a shortcut silently disarms the guard, so surface
    those instead of failing quietly.
    """
    state = load(Path(root) / "state.json")
    if not state:
        return []
    result = []
    for key, record in state.get("entries", {}).items():
        if record.get("state") != "installed":
            continue
        source = Path(record.get("source", ""))
        if not source.is_file():
            result.append({"key": key, "source": str(source), "reason": "快捷方式已不存在"})
            continue
        try:
            if sha(source) != record.get("staged_sha256"):
                result.append({"key": key, "source": str(source), "reason": "内容已被其他程序改写，拦截可能已失效"})
        except OSError as exc:
            result.append({"key": key, "source": str(source), "reason": str(exc)})
    return result


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
    text += "\n\n将先逐个备份并校验。错误配置可能导致图标无法启动。\n仅本批受管图标有双向检查；开机启动项、直接双击 EXE、其他用户桌面等入口仍不受控。\n同时点击已序列化，但进程权限/延迟启动等边界仍不能保证系统级互斥。\n是否确认继续？"
    return message(text, "Adobe 启动检查 — 确认" + action, 0x134) == 6


def write_scan_report(report, destination):
    save(destination, report)


INSTALL_LOG = "install-log.txt"


def write_log(root, lines):
    """Append one timestamped block to the run log in the fixed install directory.

    A quiet run leaves no dialog to look at afterwards, so every run records what
    it actually did next to state.json. Opening that file is the fastest way to
    tell "it really ran" from "the window was dismissed".
    """
    path = Path(root) / INSTALL_LOG
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with path.open("a", encoding="utf-8") as handle:
        handle.write("\n===== " + stamp + " =====" + "\n")
        for line in lines:
            handle.write(str(line).rstrip() + "\n")
    return path


def parse_args(args):
    """Return ``(action, silent)``, or ``(None, None)`` for a non-install mode.

    ``--yes`` makes the run quiet: the per-batch 是/否 dialog is skipped. That is
    the entry the shipped shortcut uses, because an extra dialog nobody expects is
    the single most common way this tool silently does nothing.
    """
    if not args:
        return "--install", False
    if args[0] not in ("--install", "--restore"):
        return None, None
    rest = args[1:]
    unknown = [item for item in rest if item != "--yes"]
    if unknown:
        raise RuntimeError("未知参数：" + " ".join(unknown))
    return args[0], "--yes" in rest


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


def mark_run_as_admin(path):
    """Set the RunAsUser bit in the .lnk header so a plain double-click raises UAC.

    MS-SHLLINK puts LinkFlags at offset 20; bit 13 (the 0x20 bit of the second
    byte) is RunAsUser. Public Start Menu entries can only be rewritten elevated,
    and without this flag the user has to hunt for "run as administrator" in the
    context menu -- which is where this tool gets abandoned in practice.
    """
    data = bytearray(Path(path).read_bytes())
    if len(data) >= 24 and int.from_bytes(data[0:4], "little") == 76:
        data[21] |= 0x20
        Path(path).write_bytes(bytes(data))
    return Path(path)


def is_elevated():
    """True when this process already has administrator rights."""
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def is_admin_scope(path):
    """True for shortcuts living where only administrators may rewrite them."""
    text = str(path).lower().replace("/", "\\")
    programdata = os.environ.get("ProgramData", r"C:\ProgramData").lower().rstrip("\\")
    return text.startswith(programdata + "\\")


def relaunch_elevated(silent):
    """Restart this program through UAC. Returns True once the elevated copy is up.

    Right-clicking and hunting for "run as administrator" is the step users skip,
    so the program asks for elevation itself when a batch needs it. Windows shows
    its own prompt; declining returns a small error code and we stop cleanly.
    """
    arguments = "--install --yes" if silent else "--install"
    result = ctypes.windll.shell32.ShellExecuteW(None, "runas", str(SELF), arguments, str(SELF.parent), 1)
    return result > 32


def ensure_elevated_for(items, silent, root):
    """Hand off to an elevated copy when the batch touches an administrator-only path.

    Returns True when this process should stop now: either the elevated copy is
    running, or the run is already elevated and can continue on its own.
    """
    if not any(is_admin_scope(item["source"]) for item in items):
        return False
    if is_elevated():
        return False
    if relaunch_elevated(silent):
        write_log(root, ["本批包含公共开始菜单/公共桌面入口，已请求管理员权限，交由提权后的副本继续。"])
        return True
    raise RuntimeError("改写公共开始菜单或公共桌面上的入口需要管理员权限，但提权被取消或拒绝。\n请右键该入口选择「以管理员身份运行」，或先运行「恢复 Adobe 与 Clash 原图标」把公共入口还原。\n本次未修改任何图标。")


def helper_links(root, target):
    # The install entry is quiet (``--yes``): it is launched by right-clicking and
    # choosing "run as administrator", which is already an explicit act of consent.
    for flag, name in [("--install --yes", "补充接入 Adobe 与 Clash 图标"), ("--restore", "恢复 Adobe 与 Clash 原图标")]:
        path = Path(root) / (name + ".lnk")
        pylnk3.for_file(str(target), str(path), arguments=flag, work_dir=str(root))
        mark_run_as_admin(path)


SCOPE_NOTE = "扫描范围：当前用户桌面、公共桌面，以及用户级与公共开始菜单（递归，不含「启动」文件夹）。"


def install_ui(silent=False):
    root = state_root()
    # Program first: an updated build replaces the same fixed path, so already-guarded
    # shortcuts pick it up without being touched again.
    target = install_program(root)
    ensure_blockers_file(root)
    tidy = prune_state(root)
    report = scan()  # Read-only scan of desktops and Start Menu folders.
    candidates = report["candidates"]
    if ensure_elevated_for(candidates, silent, root):
        return 0
    helper_links(root, target)
    tidy_note = ""
    if tidy["removed"] or tidy["fixed"]:
        tidy_note = "\n已整理状态记录：清理 " + str(tidy["removed"]) + " 条，修正 " + str(tidy["fixed"]) + " 条。"
    if not candidates:
        write_log(root, ["无事可做：没有待接入的入口。",
                         "已接入 " + str(len(report["already_guarded"])) + " 个，跳过 " + str(len(report["skipped"])) + " 个。",
                         "范围：" + "、".join(report["scope"])])
        message("程序已就位并校验：" + VERSION + "\n固定安装目录：" + str(root) + tidy_note + "\n\n未发现需要新接入的快捷方式。\n已接入并保留：" + str(len(report["already_guarded"])) + " 个\n跳过：" + str(len(report["skipped"])) + " 个\n\n已接入的图标会直接使用该目录里的程序，无需重新接入。\n" + SCOPE_NOTE, "检查器已更新")
        return 0
    count = 0
    for offset in range(0, len(candidates), 10):
        batch = candidates[offset:offset + 10]
        if not silent and not confirm_paths(batch, "接入"):
            write_log(root, ["已取消（确认框未点「是」），已完成 " + str(count) + " 个。"])
            message("已取消后续操作。已完成 " + str(count) + " 个，其余未修改。", "操作已停止")
            return 2
        changed = install_batch(batch, target, root)
        count += len(changed)
        write_log(root, ["接入 -> " + item for item in changed])
    ctypes.WinDLL("shell32").SHChangeNotify(0x08000000, 0, None, None)
    write_log(root, ["完成：本次接入 " + str(count) + " 个，共受管 " + str(len(load(root / "state.json", {"entries": {}})["entries"])) + " 个。",
                     "模式：" + ("静默（--yes）" if silent else "交互确认")])
    message("接入成功，并已校验：" + str(count) + " 个图标。\n\n版本：" + VERSION + "\n固定安装目录：" + str(root) + tidy_note + "\n备份：该目录内 backups\n\n分享压缩包/下载目录可删除，固定安装目录不能删除，也不能单独移动其中的 AdobeClashGuard.exe。\n以后用原图标启动即可；新增 Adobe 或 Clash 图标可重新运行本工具。\n额外拦截进程可编辑 " + BLOCKERS_FILE + "，改完立即生效。\n跳过/已接入：" + str(len(report["skipped"])) + "/" + str(len(report["already_guarded"])), "安装成功")
    return 0


def restore_ui(silent=False):
    root = state_root()
    state = load(root / "state.json", {"entries": {}})
    records = []
    for record in state["entries"].values():
        path = Path(record["source"])
        if record["state"] != "restored" and path.is_file() and sha(path) == record["staged_sha256"]:
            records.append(record)
    if not records:
        write_log(root, ["恢复：没有可恢复的受管图标。"])
        message("没有可恢复的受管图标。已删除或被其他程序改动的图标不会覆盖。", "恢复结果")
        return 0
    if ensure_elevated_for(records, silent, root):
        return 0
    count = 0
    for offset in range(0, len(records), 10):
        batch = records[offset:offset + 10]
        if not silent and not confirm_paths(batch, "恢复"):
            write_log(root, ["恢复已取消，已完成 " + str(count) + " 个。"])
            return 2
        count += len(restore_batch(batch, root))
    ctypes.WinDLL("shell32").SHChangeNotify(0x08000000, 0, None, None)
    write_log(root, ["恢复完成 " + str(count) + " 个。"])
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
            report = scan()
            # Surface managed shortcuts that another program rewrote: that is how an
            # installer disarms the guard, and it must not pass unnoticed.
            report["tampered"] = tampered_entries(state_root())
            write_scan_report(report, Path(args[1]))
            return 0
        # No UAC relaunch: avoid scanning an administrator's desktop under alternate credentials.
        action, silent = parse_args(args)
        if action is not None:
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
                return restore_ui(silent) if action == "--restore" else install_ui(silent)
            finally:
                k.ReleaseMutex(handle)
                k.CloseHandle(handle)
        raise RuntimeError("未知参数")
    except Exception as exc:
        try:
            write_log(state_root(), ["运行失败：" + str(exc)])
        except Exception:
            pass
        message("操作未完成，已停止后续处理。\n\n" + str(exc) + "\n\n不要关闭安全软件。若公共桌面写权限不足，可用当前同一账户右键以管理员身份运行。\n已生成的备份保留在固定安装目录。", "Adobe 启动检查 — 错误", 0x10)
        return 1

if __name__ == "__main__":
    sys.exit(main())
