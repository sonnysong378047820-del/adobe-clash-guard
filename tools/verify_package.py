"""Final binary checks in a disposable workspace, never install on real desktop."""
import ctypes
from ctypes import wintypes as wt
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import pylnk3
import adobe_clash_guard as g

ROOT = Path(__file__).resolve().parent
USER = ctypes.WinDLL("user32", use_last_error=True)
SHELL = ctypes.WinDLL("shell32", use_last_error=True)
SHELL.ShellExecuteW.argtypes = [wt.HWND, wt.LPCWSTR, wt.LPCWSTR, wt.LPCWSTR, wt.LPCWSTR, ctypes.c_int]
SHELL.ShellExecuteW.restype = ctypes.c_void_p
USER.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
USER.GetDlgItem.argtypes = [wt.HWND, ctypes.c_int]
USER.GetDlgItem.restype = wt.HWND
USER.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
USER.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
CB = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
USER.EnumWindows.argtypes = [CB, wt.LPARAM]


def find_dialog(title="Adobe 启动检查：请先退出 Clash Verge"):
    found = []
    @CB
    def callback(hwnd, unused):
        text = ctypes.create_unicode_buffer(512)
        USER.GetWindowTextW(hwnd, text, 512)
        if text.value == title:
            found.append(hwnd)
        return True
    USER.EnumWindows(callback, 0)
    return found[0] if found else None


def wait_for(predicate, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(0.05)
    raise TimeoutError("Final binary expected state not reached")


def dismiss(title, button=2, expect_retry_cancel=False):
    """Close one of our dialogs. WM_COMMAND(button) is tried first, WM_CLOSE always works;
    closing is equivalent to Cancel, so a mistaken dialog can never approve a write."""
    hwnd = wait_for(lambda: find_dialog(title))
    if expect_retry_cancel:
        assert USER.GetDlgItem(hwnd, 2) and USER.GetDlgItem(hwnd, 4)
    USER.PostMessageW(hwnd, 0x111, button, 0)
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and find_dialog(title):
        time.sleep(0.05)
    if find_dialog(title):
        USER.PostMessageW(hwnd, 0x10, 0, 0)
    wait_for(lambda: not find_dialog(title))


def drive_install(process):
    """Answer dialogs of a sandboxed --install run. Confirmation dialogs are always cancelled."""
    buttons = [("检查器已更新", 1), ("安装成功", 1), ("操作已停止", 1), ("自动识别结果", 1), ("Adobe 启动检查 — 确认接入", 2), ("Adobe 启动检查 — 错误", 1)]
    lookup = dict(buttons)
    answered = []
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        title = next((name for name, _ in buttons if find_dialog(name)), None)
        if title:
            dismiss(title, lookup[title])
            answered.append(title)
        elif process.poll() is not None:
            break
        else:
            time.sleep(0.05)
    process.wait(40)
    return answered


def verify_program_install(binary, base):
    """Run the real folder build with --install against a sandboxed LOCALAPPDATA.

    Confirms the program bundle (exe + _internal) is copied to the fixed path, an older
    build at that path is replaced with a rollback copy, and no real desktop entry changes.
    """
    app = binary.parent
    fake = base / "localappdata"
    root = fake / "AdobeClashGuard" / "v2.1"
    root.mkdir(parents=True)
    (root / "AdobeClashGuard.exe").write_bytes(b"older single-file build")
    (root / "_internal").mkdir()
    (root / "_internal" / "stale.dll").write_bytes(b"stale")
    process = subprocess.Popen([str(binary), "--install"], cwd=str(base), env=dict(os.environ, LOCALAPPDATA=str(fake)))
    try:
        answered = drive_install(process)
        assert answered, "sandboxed install produced no dialog"
        assert g.sha(root / "AdobeClashGuard.exe") == g.sha(binary)
        assert (root / "AdobeClashGuard.previous.exe").read_bytes() == b"older single-file build"
        assert (root / "_internal").is_dir() and not (root / "_internal" / "stale.dll").exists()
        assert not (root / "_internal.new").exists()
        assert {p.name for p in app.iterdir()} <= {p.name for p in root.iterdir()}
        for name in ["补充接入 Adobe 与 Clash 图标.lnk", "恢复 Adobe 与 Clash 原图标.lnk"]:
            assert Path(pylnk3.parse(str(root / name)).path) == root / "AdobeClashGuard.exe"
    finally:
        if process.poll() is None:
            kill_windows(process.pid)
    return {"dialogs": answered, "folder_build_installed_to_fixed_path": True, "previous_build_kept_as_rollback": True, "internal_folder_updated": True, "helper_links_created": True, "installed_files": sorted(p.name for p in root.iterdir())}


def kill_windows(pid):
    """Never leave a test instance sitting on a dialog (it would hold the installer mutex)."""
    @CB
    def callback(hwnd, unused):
        owner = wt.DWORD()
        USER.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid:
            USER.PostMessageW(hwnd, 0x10, 0, 0)
        return True
    USER.EnumWindows(callback, 0)
    time.sleep(1)
    subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)


def main():
    # Read only actual desktops: snapshot hashes before and after package tests.
    actual = {str(path): g.sha(path) for root in g.desktops() for path in root.glob("*.lnk")}
    binary = ROOT / "dist" / "AdobeClashGuard" / "AdobeClashGuard.exe"
    process = subprocess.run([str(binary), "--scan", str(ROOT / "binary-scan.json")], timeout=30)
    assert process.returncode == 0
    report = g.load(ROOT / "binary-scan.json")
    assert len(report["already_guarded"]) >= 4
    clash_active = bool(set(g.process_names()) & g.BLOCKERS)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
        base = Path(folder)
        program = verify_program_install(binary, base)
        executable_dir = base / "installed"
        shutil.copytree(binary.parent, executable_dir)
        executable = executable_dir / "AdobeClashGuard.exe"
        desktop = base / "测试桌面"
        desktop.mkdir()
        target = base / "Adobe" / "Adobe Photoshop 2033" / "Photoshop.exe"
        target.parent.mkdir(parents=True)
        shutil.copyfile(sys.executable, target)
        source = desktop / "PS 任意版本.lnk"
        pylnk3.for_file(str(target), str(source), icon_file=str(target), work_dir=str(target.parent))
        before = source.read_bytes()
        candidates = g.scan([desktop], lambda path: "")["candidates"]
        g.install_batch(candidates, executable, executable.parent)
        # Forward popup: a real Clash when it is running, otherwise a renamed inert process
        # (same process name the guard matches). Never start or stop the user's own Clash.
        blocker = None
        try:
            if clash_active:
                forward_blocker = "running Clash Verge / proxy core"
            else:
                fake_dir = base / "simulated-proxy"
                fake_dir.mkdir()
                fake = fake_dir / "clash-verge.exe"
                # Self-contained system binary so the renamed copy keeps running.
                shutil.copyfile(r"C:\Windows\System32\PING.EXE", fake)
                blocker = subprocess.Popen([str(fake), "-n", "120", "127.0.0.1"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                wait_for(lambda: "clash-verge.exe" in g.process_names())
                assert blocker.poll() is None, "simulated blocker exited early"
                forward_blocker = "simulated clash-verge.exe process (no Clash running)"
            for files in [[], ["C:\\中文 图片\\产品.png", "C:\\a&b\\文件.psd"]]:
                code = SHELL.ShellExecuteW(None, "open", str(source), subprocess.list2cmdline(files) if files else None, str(base), 1)
                assert code and code > 32
                dismiss("Adobe 启动检查：请先退出 Clash Verge", 2, True)
                wait_for(lambda: "adobeclashguard.exe" not in g.process_names())
        finally:
            if blocker is not None:
                blocker.terminate()
                blocker.wait(15)
        record = next(iter(g.load(executable.parent / "state.json")["entries"].values()))
        # Reverse popup using a registered test Adobe process (this Python runner),
        # never start/stop the user's real Adobe or Clash applications.
        clash_target = base / "ProxyApps" / "clash-verge.exe"
        clash_target.parent.mkdir()
        shutil.copyfile(sys.executable, clash_target)
        clash_link = desktop / "Clash 任意路径.lnk"
        pylnk3.for_file(str(clash_target), str(clash_link), icon_file=str(clash_target))
        clash_before = clash_link.read_bytes()
        clash_item = g.inspect_shortcut(clash_link, lambda path: "")
        g.install_batch([clash_item], executable, executable.parent)
        state = g.load(executable.parent / "state.json")
        state["entries"]["test-adobe-running"] = {"kind": "adobe", "target": sys.executable, "state": "installed"}
        g.save(executable.parent / "state.json", state)
        code = SHELL.ShellExecuteW(None, "open", str(clash_link), None, str(base), 1)
        assert code and code > 32
        title = "Clash 启动检查：请先退出 Adobe"
        dismiss(title, 2, True)
        wait_for(lambda: "adobeclashguard.exe" not in g.process_names())
        # Verify competing starts are rejected while another guard owns the mutex.
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateMutexW.argtypes = [wt.LPVOID, wt.BOOL, wt.LPCWSTR]
        kernel.CreateMutexW.restype = wt.HANDLE
        kernel.ReleaseMutex.argtypes = [wt.HANDLE]
        kernel.CloseHandle.argtypes = [wt.HANDLE]
        lock = kernel.CreateMutexW(None, True, "Local\\AdobeClashGuard-Launch")
        assert lock
        try:
            code = SHELL.ShellExecuteW(None, "open", str(clash_link), None, str(base), 1)
            assert code and code > 32
            title = "双向启动检查：等待其他检查完成"
            dismiss(title, 2, True)
            wait_for(lambda: "adobeclashguard.exe" not in g.process_names())
        finally:
            kernel.ReleaseMutex(lock)
            kernel.CloseHandle(lock)
        state = g.load(executable.parent / "state.json")
        del state["entries"]["test-adobe-running"]
        g.save(executable.parent / "state.json", state)
        records = list(state["entries"].values())
        g.restore_batch(records, executable.parent)
        assert source.read_bytes() == before
        assert clash_link.read_bytes() == clash_before
    assert all(Path(path).is_file() and g.sha(path) == value for path, value in actual.items())
    assert "adobeclashguard.exe" not in g.process_names()
    results = {"compiled_readonly_scan": True, "known_existing_guards_skipped": len(report["already_guarded"]), "packaged_binary_shortcut_popup_and_cancel": True, "packaged_binary_dropped_file_popup_and_cancel": True, "forward_popup_blocker": forward_blocker, "reverse_clash_popup_and_cancel": True, "concurrent_launch_mutex_and_cancel": True, "simulated_install_restore_byte_exact": True, "real_desktop_links_unchanged": True, "remaining_guard_processes": 0}
    results.update({key: value for key, value in program.items() if key != "dialogs"})
    results["sandboxed_install_dialogs"] = program["dialogs"]
    g.save(ROOT / "package-test-results.json", results)
    print(json.dumps(results, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
