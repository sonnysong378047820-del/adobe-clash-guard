import importlib.util
import json
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
        self.assertEqual(g.clash_running(["clash-verge-service.exe"]), [])

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

    def test_real_desktop_paths_and_processes_available(self):
        self.assertTrue(all(p.is_dir() for p in g.desktops()))
        self.assertIn(Path(sys.executable).name.lower(), g.process_names())
        self.assertTrue(isinstance(g.company_name(Path(sys.executable)), str))

if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(UniversalTests)
    with (ROOT / "test-results.txt").open("w", encoding="utf-8") as out:
        result = unittest.TextTestRunner(stream=out, verbosity=2).run(suite)
    print((ROOT / "test-results.txt").read_text(encoding="utf-8"))
    sys.exit(not result.wasSuccessful())
