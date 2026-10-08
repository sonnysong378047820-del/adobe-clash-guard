import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock

import pylnk3
import adobe_clash_guard as g

ROOT = Path(__file__).resolve().parent

class UniversalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT)
        self.base = Path(self.temp.name)
        self.desktop = self.base / "中文桌面"
        self.public = self.base / "公共桌面"
        self.desktop.mkdir()
        self.public.mkdir()
        self.vendor = lambda path: ""

    def tearDown(self):
        self.temp.cleanup()

    def executable(self, name, folder="Adobe/Adobe Photoshop 2030"):
        path = self.base / folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(sys.executable, path)
        return path

    def shortcut(self, name, target, arguments="", root=None, elevated=False):
        path = (root or self.desktop) / (name + ".lnk")
        lnk = pylnk3.for_file(str(target), arguments=arguments, icon_file=str(target), work_dir=str(target.parent), window_mode="Maximized")
        lnk.link_flags.RunAsUser = elevated
        lnk.save(str(path))
        return path

    def test_all_versions_and_renamed_shortcuts_are_discovered(self):
        for name in ["PS 工作", "PS 2028", "Photoshop 新版"]:
            self.shortcut(name, self.executable("Photoshop.exe"))
        self.shortcut("视频工具", self.executable("AfterFX.exe", "Adobe/Adobe After Effects 2029/Support Files"), root=self.public)
        result = g.scan([self.desktop, self.public], self.vendor)
        self.assertEqual(len(result["candidates"]), 4)

    def test_vendor_identifies_new_unknown_adobe_product(self):
        path = self.executable("NewAdobeTool.exe", "D_custom_apps/Creative")
        self.shortcut("新应用", path)
        result = g.scan([self.desktop], lambda p: "Adobe Inc.")
        self.assertEqual(len(result["candidates"]), 1)

    def test_name_only_does_not_misidentify_non_adobe(self):
        self.shortcut("Adobe 假名字", self.executable("other.exe", "Other"))
        self.assertEqual(g.scan([self.desktop], self.vendor)["candidates"], [])

    def test_non_adobe_photoshop_name_without_vendor_or_folder_is_skipped(self):
        self.shortcut("Adobe Photoshop", self.executable("Photoshop.exe", "Other"))
        self.assertEqual(g.scan([self.desktop], self.vendor)["candidates"], [])

    def test_installer_uninstaller_and_missing_links_are_skipped(self):
        for name in ["setup.exe", "uninstall.exe", "updater.exe"]:
            self.shortcut("Adobe " + name, self.executable(name))
        target = self.executable("Photoshop.exe")
        self.shortcut("Adobe 丢失", target)
        target.unlink()
        result = g.scan([self.desktop], lambda p: "Adobe Inc.")
        self.assertEqual(result["candidates"], [])
        self.assertEqual(len(result["skipped"]), 4)

    def test_existing_local_and_universal_guards_are_skipped(self):
        for name in ["AdobeLaunchGuard.exe", "AdobeClashGuard.exe"]:
            self.shortcut("已接入 " + name, self.executable(name))
        result = g.scan([self.desktop], self.vendor)
        self.assertEqual(len(result["already_guarded"]), 2)
        self.assertEqual(result["candidates"], [])

    def test_scan_is_read_only_and_top_level_only(self):
        target = self.executable("Photoshop.exe")
        path = self.shortcut("PS", target)
        nested = self.desktop / "子目录"
        nested.mkdir()
        self.shortcut("不在扫描范围", target, root=nested)
        before = g.sha(path)
        result = g.scan([self.desktop], self.vendor)
        self.assertEqual(len(result["candidates"]), 1)
        self.assertEqual(g.sha(path), before)

    def test_install_and_restore_exact_bytes_and_original_properties(self):
        exe = self.executable("AdobeClashGuard.exe", "fixed_install")
        source = self.shortcut("设计 PS", self.executable("Photoshop.exe"), '/custom "中文 参数"', elevated=True)
        before = source.read_bytes()
        items = g.scan([self.desktop], self.vendor)["candidates"]
        state_root = self.base / "state"
        self.assertEqual(len(g.install_batch(items, exe, state_root)), 1)
        state = g.load(state_root / "state.json")
        record = next(iter(state["entries"].values()))
        current = pylnk3.parse(str(source))
        self.assertEqual(Path(current.path), exe)
        self.assertEqual(current.icon, items[0]["icon"])
        self.assertTrue(current.link_flags.RunAsUser)
        self.assertEqual(current.window_mode, "Maximized")
        self.assertEqual(Path(record["backup"]).read_bytes(), before)
        self.assertEqual(record["raw_arguments"], '/custom "中文 参数"')
        self.assertEqual(g.scan([self.desktop], self.vendor)["candidates"], [])
        g.restore_batch([record], state_root)
        self.assertEqual(source.read_bytes(), before)

    def test_changed_source_rejected_before_install(self):
        path = self.shortcut("PS", self.executable("Photoshop.exe"))
        items = g.scan([self.desktop], self.vendor)["candidates"]
        path.write_bytes(b"changed")
        with self.assertRaises(RuntimeError):
            g.install_batch(items, self.executable("AdobeClashGuard.exe"), self.base / "state")
        self.assertEqual(path.read_bytes(), b"changed")

    def test_restoration_does_not_overwrite_other_changes(self):
        path = self.shortcut("PS", self.executable("Photoshop.exe"))
        root = self.base / "state"
        g.install_batch(g.scan([self.desktop], self.vendor)["candidates"], self.executable("AdobeClashGuard.exe"), root)
        record = next(iter(g.load(root / "state.json")["entries"].values()))
        path.write_bytes(b"new user shortcut")
        with self.assertRaises(RuntimeError):
            g.restore_batch([record], root)
        self.assertEqual(path.read_bytes(), b"new user shortcut")

    def test_no_changes_to_second_link_after_first_write_failure(self):
        from unittest.mock import patch
        p1 = self.shortcut("PS1", self.executable("Photoshop.exe"))
        p2 = self.shortcut("PS2", self.executable("Photoshop.exe"))
        before = {p: p.read_bytes() for p in [p1, p2]}
        original_copy = shutil.copyfile
        def copy(src, dst, *args, **kwargs):
            if Path(dst) == p1:
                raise PermissionError("test deny")
            return original_copy(src, dst, *args, **kwargs)
        with patch.object(g.shutil, "copyfile", side_effect=copy):
            with self.assertRaises(RuntimeError):
                g.install_batch(g.scan([self.desktop], self.vendor)["candidates"], self.executable("AdobeClashGuard.exe"), self.base / "state")
        self.assertEqual(p1.read_bytes(), before[p1])
        self.assertEqual(p2.read_bytes(), before[p2])
        state = g.load(self.base / "state" / "state.json")
        self.assertTrue(all(Path(record["backup"]).is_file() for record in state["entries"].values()))

    def test_broken_shortcut_is_reported_not_fatal(self):
        (self.desktop / "Adobe broken.lnk").write_bytes(b"broken")
        self.shortcut("PS", self.executable("Photoshop.exe"))
        report = g.scan([self.desktop], self.vendor)
        self.assertEqual(len(report["candidates"]), 1)
        self.assertEqual(len(report["skipped"]), 1)

    def test_batch_limit(self):
        with self.assertRaises(ValueError):
            g.install_batch([{}] * 11, sys.executable, self.base)
        with self.assertRaises(ValueError):
            g.restore_batch([], self.base)

    def test_cancel_does_not_launch(self):
        start = Mock()
        app = {"name": "PS"}
        self.assertEqual(g.launch(app, [], scan_processes=lambda: ["clash-verge.exe"], dialog=lambda *a: 2, start=start), 2)
        start.assert_not_called()

    def test_retry_then_allow_service_only(self):
        scans = iter([["verge-mihomo.exe"], ["clash-verge-service.exe"]])
        app = {"name": "test", "target": sys.executable, "raw_arguments": "", "workdir": str(self.base), "window_mode": "Normal"}
        start = Mock()
        self.assertEqual(g.launch(app, [], scan_processes=lambda: next(scans), dialog=lambda *a: 4, start=start), 0)
        start.assert_called_once()
        self.assertFalse(start.call_args.kwargs["shell"])

    def test_real_child_receives_original_arguments_and_dropped_files(self):
        result = self.base / "参数.json"
        code = "import json,sys; from pathlib import Path; Path(sys.argv[1]).write_text(json.dumps(sys.argv[2:],ensure_ascii=False),encoding='utf-8')"
        original = ["-c", code, str(result), "原始 参数"]
        files = ["C:\\图片 空格\\中文.png", "C:\\a&b\\two.psd", "C:\\末尾\\"]
        app = {"name": "test", "target": sys.executable, "raw_arguments": subprocess.list2cmdline(original), "workdir": str(self.base), "window_mode": "Normal"}
        children = []
        def start(*args, **kwargs):
            child = subprocess.Popen(*args, **kwargs)
            children.append(child)
            return child
        self.assertEqual(g.launch(app, files, scan_processes=lambda: [], start=start), 0)
        self.assertEqual(children[0].wait(timeout=15), 0)
        self.assertEqual(json.loads(result.read_text(encoding="utf-8")), ["原始 参数"] + files)

    def test_scan_error_and_missing_target_fail_closed(self):
        start = Mock()
        with self.assertRaises(OSError):
            g.launch({}, [], scan_processes=Mock(side_effect=OSError("scan")), start=start)
        with self.assertRaises(FileNotFoundError):
            g.launch({"target": str(self.base / "missing.exe"), "name": "PS"}, [], scan_processes=lambda: [], start=start)
        start.assert_not_called()

    def test_reverse_cancel_adobe_blocks_clash(self):
        start = Mock()
        dialog = Mock(return_value=2)
        app = {"name": "Clash Verge", "kind": "clash"}
        self.assertEqual(g.launch(app, [], scan_processes=lambda: ["Photoshop.exe", "Illustrator.exe"], dialog=dialog, start=start), 2)
        start.assert_not_called()
        self.assertIn("Clash", dialog.call_args.args[1])
        self.assertIn("photoshop.exe", dialog.call_args.args[0])

    def test_reverse_retry_rechecks_adobe(self):
        scans = iter([["afterfx.exe"], ["illustrator.exe"], ["clash-verge-service.exe"]])
        app = {"name": "Clash", "kind": "clash", "target": sys.executable, "raw_arguments": "", "workdir": str(self.base)}
        dialog, start = Mock(return_value=4), Mock()
        self.assertEqual(g.launch(app, [], scan_processes=lambda: next(scans), dialog=dialog, start=start), 0)
        self.assertEqual(dialog.call_count, 2)
        start.assert_called_once()

    def test_service_only_never_blocks_either_side(self):
        self.assertEqual(g.adobe_running(["AdobeIPCBroker.exe", "AdobeUpdateService.exe", "clash-verge-service.exe"]), [])
        self.assertEqual(g.clash_running(["clash-verge-service.exe"], g.BLOCKERS), [])

    def test_unknown_registered_adobe_app_is_blocker(self):
        start = Mock()
        app = {"name": "Clash", "kind": "clash", "adobe_process_names": ["customadobe.exe"]}
        self.assertEqual(g.launch(app, [], scan_processes=lambda: ["customadobe.exe"], dialog=lambda *a: 2, start=start), 2)
        start.assert_not_called()

    def test_clash_shortcut_renamed_detected_with_kind_and_args(self):
        target = self.executable("clash-verge.exe", "ProxyApps")
        path = self.shortcut("代理工具 任意名字", target, '--config "中文 参数.yaml"')
        result = g.scan([self.desktop], self.vendor)
        self.assertEqual(result["candidates"][0]["kind"], "clash")
        self.assertEqual(result["candidates"][0]["raw_arguments"], '--config "中文 参数.yaml"')
        before = path.read_bytes()
        root = self.base / "installed"
        g.install_batch(result["candidates"], self.executable("AdobeClashGuard.exe", "guard"), root)
        record = next(iter(g.load(root / "state.json")["entries"].values()))
        self.assertEqual(record["kind"], "clash")
        self.assertIn("退出 Adobe", pylnk3.parse(str(path)).description)
        g.restore_batch([record], root)
        self.assertEqual(path.read_bytes(), before)

    def test_missing_clash_and_clash_service_links_not_candidates(self):
        target = self.executable("clash-verge.exe", "ProxyApps")
        self.shortcut("Clash missing", target)
        target.unlink()
        self.shortcut("Clash 服务", self.executable("clash-verge-service.exe", "ProxyApps"))
        self.assertEqual(g.scan([self.desktop], self.vendor)["candidates"], [])

    def test_alpha_core_counts_as_blocker_and_is_detected(self):
        self.assertEqual(g.clash_running(["verge-mihomo-alpha.exe"], g.BLOCKERS), ["verge-mihomo-alpha.exe"])
        self.assertEqual(g.clash_running(["MIHOMO-ALPHA.EXE"], g.BLOCKERS), ["mihomo-alpha.exe"])
        target = self.executable("verge-mihomo-alpha.exe", "ProxyApps")
        self.shortcut("代理内核 任意名字", target)
        result = g.scan([self.desktop], self.vendor)
        self.assertEqual(result["candidates"][0]["kind"], "clash")

    def test_blockers_file_extends_builtin_names(self):
        root = self.base / "state"
        root.mkdir()
        self.assertEqual(g.load_blockers(root), g.BLOCKERS)
        (root / g.BLOCKERS_FILE).write_text(
            "\ufeff# 注释行\n\n  my-proxy.exe  \nSing-Box.exe # 行尾注释\nlegacy\n", encoding="utf-8")
        names = g.load_blockers(root)
        self.assertIn("my-proxy.exe", names)
        self.assertIn("sing-box.exe", names)
        self.assertIn("legacy", names)
        self.assertNotIn("# 注释行", names)
        self.assertTrue(g.BLOCKERS.issubset(names))
        self.assertEqual(g.clash_running(["MY-PROXY.EXE", "clash-verge.exe"], names), ["clash-verge.exe", "my-proxy.exe"])
        self.assertEqual(g.clash_running(["my-proxy.exe"], g.BLOCKERS), [])

    def test_ensure_blockers_file_never_overwrites_existing(self):
        root = self.base / "state"
        root.mkdir()
        self.assertTrue(g.ensure_blockers_file(root))
        path = root / g.BLOCKERS_FILE
        self.assertIn("verge-mihomo-alpha.exe", path.read_text(encoding="utf-8"))
        path.write_text("mine.exe\n", encoding="utf-8")
        self.assertFalse(g.ensure_blockers_file(root))
        self.assertEqual(path.read_text(encoding="utf-8"), "mine.exe\n")

    def test_recursive_root_covers_subfolders_but_not_startup(self):
        target = self.executable("Photoshop.exe")
        nested = self.desktop / "Adobe 程序组"
        nested.mkdir()
        self.shortcut("PS 嵌套", target, root=nested)
        startup = self.desktop / "Startup"
        startup.mkdir()
        self.shortcut("Adobe 开机启动", target, root=startup)
        result = g.scan([(self.desktop, True)], self.vendor)
        self.assertEqual([item["name"] for item in result["candidates"]], ["PS 嵌套"])

    def test_unrelated_menu_entries_are_not_reported_as_skipped(self):
        target = self.executable("notepad.exe", "Windows")
        self.shortcut("记事本", target)
        self.shortcut("Maxon Cinema 4D 2026", target)
        self.assertEqual(g.scan([self.desktop], self.vendor)["skipped"], [])

    def test_prune_state_drops_aborted_rows_and_flags_rewritten_ones(self):
        root = self.base / "state"
        root.mkdir()
        guarded = self.shortcut("PS", self.executable("Photoshop.exe"))
        untouched = self.shortcut("AI", self.executable("Illustrator.exe", "Adobe/Adobe Illustrator 2031"))
        promoted = self.shortcut("ME", self.executable("Adobe Media Encoder.exe", "Adobe/Adobe Media Encoder 2031"))
        rewritten = self.shortcut("AE", self.executable("AfterFX.exe", "Adobe/Adobe After Effects 2031"))
        g.save(root / "state.json", {"version": "0", "entries": {
            "keep": {"source": str(guarded), "state": "installed", "source_sha256": g.sha(guarded), "staged_sha256": g.sha(guarded)},
            "duplicate": {"source": str(guarded), "state": "prepared", "source_sha256": g.sha(guarded), "staged_sha256": "0" * 64},
            "never-applied": {"source": str(untouched), "state": "prepared", "source_sha256": g.sha(untouched), "staged_sha256": "1" * 64},
            "applied-but-stale": {"source": str(promoted), "state": "prepared", "source_sha256": "2" * 64, "staged_sha256": g.sha(promoted)},
            "overwritten": {"source": str(rewritten), "state": "installed", "source_sha256": g.sha(rewritten), "staged_sha256": "3" * 64},
        }})
        self.assertEqual(g.prune_state(root), {"removed": 2, "fixed": 2})
        state = g.load(root / "state.json")
        self.assertEqual(set(state["entries"]), {"keep", "applied-but-stale", "overwritten"})
        self.assertEqual(state["entries"]["applied-but-stale"]["state"], "installed")
        self.assertEqual(state["entries"]["overwritten"]["state"], "superseded")
        self.assertEqual(state["version"], g.VERSION)
        for record in state["entries"].values():
            self.assertTrue(Path(record["source"]).is_file())

    def test_tampered_entries_flags_rewritten_managed_shortcut(self):
        root = self.base / "state"
        path = self.shortcut("PS", self.executable("Photoshop.exe"))
        g.install_batch(g.scan([self.desktop], self.vendor)["candidates"], self.executable("AdobeClashGuard.exe", "guard"), root)
        self.assertEqual(g.tampered_entries(root), [])
        path.write_bytes(b"rewritten by an installer")
        found = g.tampered_entries(root)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["source"], str(path))
        self.assertIn("改写", found[0]["reason"])
        path.unlink()
        self.assertIn("不存在", g.tampered_entries(root)[0]["reason"])

    def test_parse_args_separates_quiet_from_interactive(self):
        self.assertEqual(g.parse_args([]), ("--install", False))
        self.assertEqual(g.parse_args(["--install"]), ("--install", False))
        self.assertEqual(g.parse_args(["--install", "--yes"]), ("--install", True))
        self.assertEqual(g.parse_args(["--restore", "--yes"]), ("--restore", True))
        self.assertIsNone(g.parse_args(["--scan", "out.json"])[0])
        self.assertIsNone(g.parse_args(["--launch", "key"])[0])
        with self.assertRaises(RuntimeError):
            g.parse_args(["--install", "--force"])

    def test_write_log_appends_one_timestamped_block_per_run(self):
        root = self.base / "log"
        root.mkdir()
        first = g.write_log(root, ["接入 -> A", "完成 1 个"])
        self.assertEqual(first.name, g.INSTALL_LOG)
        g.write_log(root, ["接入 -> B"])
        text = first.read_text(encoding="utf-8")
        self.assertEqual(text.count("====="), 4)  # 两条分隔线，每条两端各五个等号
        self.assertLess(text.index("接入 -> A"), text.index("接入 -> B"))

    def test_helper_links_ship_the_quiet_install_entry(self):
        root = self.base / "links"
        root.mkdir()
        target = root / "AdobeClashGuard.exe"
        target.write_bytes(b"stub")
        g.helper_links(root, target)
        install = pylnk3.parse(str(root / ("补充接入 Adobe 与 Clash 图标.lnk")))
        restore = pylnk3.parse(str(root / ("恢复 Adobe 与 Clash 原图标.lnk")))
        self.assertEqual(install.arguments, "--install --yes")
        self.assertEqual(restore.arguments, "--restore")

    def test_helper_links_request_elevation(self):
        root = self.base / "links-admin"
        root.mkdir()
        target = root / "AdobeClashGuard.exe"
        target.write_bytes(b"stub")
        g.helper_links(root, target)
        for name in ("补充接入 Adobe 与 Clash 图标.lnk", "恢复 Adobe 与 Clash 原图标.lnk"):
            raw = (root / name).read_bytes()
            self.assertEqual(int.from_bytes(raw[0:4], "little"), 76)
            flags = int.from_bytes(raw[20:24], "little")
            self.assertTrue(flags & 0x2000, name + " 缺少 RunAsUser 标志")
            # 保留原有位，避免覆盖 pylnk3 写好的 HasArguments / IsUnicode 等标志
            self.assertTrue(flags & 0x80, name + " 丢失 Unicode 标志")

    def test_mark_run_as_admin_ignores_non_lnk_data(self):
        broken = self.base / "broken.lnk"
        broken.write_bytes(b"not a shortcut at all")
        g.mark_run_as_admin(broken)
        self.assertEqual(broken.read_bytes(), b"not a shortcut at all")

    def test_admin_scope_only_covers_programdata(self):
        programdata = Path(os.environ.get("ProgramData", r"C:\ProgramData"))
        self.assertTrue(g.is_admin_scope(programdata / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Clash Verge.lnk"))
        # 公共桌面普通用户可写（既有接入记录证实），不算管理员范围
        self.assertFalse(g.is_admin_scope(Path(r"C:\Users\Public\Desktop\Clash Verge.lnk")))
        self.assertFalse(g.is_admin_scope(Path(os.environ["USERPROFILE"]) / "Desktop" / "Adobe Photoshop 2024.lnk"))
        self.assertFalse(g.is_admin_scope(Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Clash Verge.lnk"))

    def test_ensure_elevated_stays_put_when_no_admin_path_involved(self):
        # 只涉及用户桌面/用户开始菜单时，不应触发自我提权（否则会莫名弹 UAC）
        self.assertFalse(g.ensure_elevated_for([], True, self.base))
        desktop = Path(os.environ["USERPROFILE"]) / "Desktop" / "Adobe Photoshop 2024.lnk"
        self.assertFalse(g.ensure_elevated_for([{"source": str(desktop)}], True, self.base))

    def test_real_desktop_and_start_menu_paths_available(self):
        self.assertTrue(g.start_menu_roots())
        self.assertTrue(all(p.is_dir() for p in g.start_menu_roots()))
        self.assertTrue(all(p.is_dir() for p in g.desktops()))
        self.assertTrue(all(p.is_dir() for p in g.desktops()))
        self.assertIn(Path(sys.executable).name.lower(), g.process_names())
        self.assertTrue(isinstance(g.company_name(Path(sys.executable)), str))

if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(UniversalTests)
    with (ROOT / "test-results.txt").open("w", encoding="utf-8") as out:
        result = unittest.TextTestRunner(stream=out, verbosity=2).run(suite)
    print((ROOT / "test-results.txt").read_text(encoding="utf-8"))
    sys.exit(not result.wasSuccessful())
