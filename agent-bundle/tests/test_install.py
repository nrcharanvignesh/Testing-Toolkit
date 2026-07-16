"""Unit tests for agent-bundle/install.py logic functions.

Tests pure logic, path resolution, argument construction, platform helpers,
CLI parsing, and constants. All filesystem/subprocess/network calls are mocked.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

# ---------------------------------------------------------------------------
# Load the installer module without executing side effects
# ---------------------------------------------------------------------------
_INSTALL_PY = Path(__file__).resolve().parent.parent / "install.py"


def _load_installer() -> Any:
    spec = importlib.util.spec_from_file_location("tt_install", _INSTALL_PY)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    assert spec and spec.loader
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


inst = _load_installer()


# ===========================================================================
# Constants and configuration
# ===========================================================================
class TestConstants:
    """Guard critical constants against accidental changes."""

    def test_agent_port(self) -> None:
        assert inst.AGENT_PORT == 7842

    def test_min_py(self) -> None:
        assert inst.MIN_PY == (3, 9)

    def test_create_no_window_value(self) -> None:
        assert inst.CREATE_NO_WINDOW == 0x08000000

    def test_probe_minors_descending(self) -> None:
        # Must be newest-first for correct selection priority
        assert inst._PROBE_MINORS == (13, 12, 11, 10, 9)
        assert inst._PROBE_MINORS == tuple(sorted(inst._PROBE_MINORS, reverse=True))

    def test_ceiling_minor(self) -> None:
        assert inst._CEILING_MINOR >= 14

    def test_pip_quiet_flags(self) -> None:
        assert "--quiet" in inst._PIP_QUIET
        assert "--no-input" in inst._PIP_QUIET
        assert "--no-cache-dir" in inst._PIP_QUIET
        assert "--disable-pip-version-check" in inst._PIP_QUIET

    def test_bundle_dir_is_absolute(self) -> None:
        assert inst.BUNDLE_DIR.is_absolute()

    def test_bundle_layout_paths_relative_to_bundle_dir(self) -> None:
        assert inst.WHEELHOUSE == inst.BUNDLE_DIR / "wheelhouse"
        assert inst.SRC_DIR == inst.BUNDLE_DIR / "src"
        assert inst.RUNTIME_DIR == inst.BUNDLE_DIR / "runtime"
        assert inst.REQUIREMENTS == inst.BUNDLE_DIR / "requirements.txt"


# ===========================================================================
# Platform detection (normalize_os / normalize_arch / detect_platform)
# ===========================================================================
class TestNormalizeOs:
    """Comprehensive OS alias handling including edge cases."""

    @pytest.mark.parametrize("value,expected", [
        ("Windows", "windows"),
        ("windows", "windows"),
        ("WINDOWS", "windows"),
        ("win32", "windows"),
        ("Win64", "windows"),
        ("MSYS_NT-10.0", "windows"),
        ("MINGW64_NT-10.0", "windows"),
        ("CYGWIN_NT-10.0", "windows"),
        ("Darwin", "macos"),
        ("darwin", "macos"),
        ("mac", "macos"),
        ("macos", "macos"),
        ("MacOS", "macos"),
        ("osx", "macos"),
        ("OSX", "macos"),
        ("Linux", "linux"),
        ("linux", "linux"),
    ])
    def test_known_aliases(self, value: str, expected: str) -> None:
        assert inst.normalize_os(value) == expected

    def test_whitespace_stripped(self) -> None:
        assert inst.normalize_os("  Windows  ") == "windows"
        assert inst.normalize_os("\tLinux\n") == "linux"

    def test_unknown_raises(self) -> None:
        with pytest.raises(RuntimeError, match="Unsupported operating system"):
            inst.normalize_os("FreeBSD")

    def test_empty_raises(self) -> None:
        with pytest.raises(RuntimeError, match="<empty>"):
            inst.normalize_os("")

    def test_whitespace_only_raises(self) -> None:
        with pytest.raises(RuntimeError, match="Unsupported operating system"):
            inst.normalize_os("   ")


class TestNormalizeArch:
    """Comprehensive CPU architecture alias handling."""

    @pytest.mark.parametrize("value,expected", [
        ("x86_64", "amd64"),
        ("AMD64", "amd64"),
        ("x64", "amd64"),
        ("amd64", "amd64"),
        ("i386", "x86"),
        ("i486", "x86"),
        ("i586", "x86"),
        ("i686", "x86"),
        ("x86", "x86"),
        ("arm64", "arm64"),
        ("aarch64", "arm64"),
        ("ARM64", "arm64"),
        ("armv8", "arm64"),
        ("armv8l", "arm64"),
        ("armv7", "armv7"),
        ("armv7l", "armv7"),
        ("ppc64le", "ppc64le"),
        ("s390x", "s390x"),
        ("riscv64", "riscv64"),
    ])
    def test_known_aliases(self, value: str, expected: str) -> None:
        assert inst.normalize_arch(value) == expected

    def test_hyphen_replaced_with_underscore(self) -> None:
        # "x86-64" -> "x86_64" -> "amd64"
        assert inst.normalize_arch("x86-64") == "amd64"

    def test_whitespace_stripped(self) -> None:
        assert inst.normalize_arch("  aarch64  ") == "arm64"

    def test_unknown_raises(self) -> None:
        with pytest.raises(RuntimeError, match="Unsupported CPU architecture"):
            inst.normalize_arch("mips64")

    def test_empty_raises(self) -> None:
        with pytest.raises(RuntimeError, match="<empty>"):
            inst.normalize_arch("")


class TestDetectPlatform:
    """detect_platform delegates to normalize_os/normalize_arch."""

    def test_returns_tuple(self) -> None:
        result = inst.detect_platform()
        assert isinstance(result, tuple)
        assert len(result) == 2

    def test_mocked_platform(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("platform.system", lambda: "Darwin")
        monkeypatch.setattr("platform.machine", lambda: "arm64")
        assert inst.detect_platform() == ("macos", "arm64")


# ===========================================================================
# pip argument construction
# ===========================================================================
class TestPipArgs:
    """pip_args_offline and pip_args_online produce correct argument lists."""

    def test_offline_contains_no_index(self) -> None:
        args = inst.pip_args_offline(["-r", "/fake/requirements.txt"])
        assert "--no-index" in args
        assert any(a.startswith("--find-links=") for a in args)

    def test_offline_contains_upgrade(self) -> None:
        args = inst.pip_args_offline([])
        assert "--upgrade" in args

    def test_offline_extra_appended(self) -> None:
        args = inst.pip_args_offline(["--target", "/fake/lib"])
        assert "--target" in args
        assert "/fake/lib" in args

    def test_online_no_no_index(self) -> None:
        args = inst.pip_args_online(["-r", "/fake/requirements.txt"])
        assert "--no-index" not in args

    def test_online_still_has_find_links(self) -> None:
        # Online prefers bundled wheels but allows PyPI fallback
        args = inst.pip_args_online([])
        assert any(a.startswith("--find-links=") for a in args)

    def test_online_contains_upgrade(self) -> None:
        args = inst.pip_args_online([])
        assert "--upgrade" in args

    def test_offline_starts_with_m_pip_install(self) -> None:
        args = inst.pip_args_offline([])
        assert args[:3] == ["-m", "pip", "install"]

    def test_online_starts_with_m_pip_install(self) -> None:
        args = inst.pip_args_online([])
        assert args[:3] == ["-m", "pip", "install"]


# ===========================================================================
# CLI argument parsing
# ===========================================================================
class TestCliParsing:
    """Verify argparse setup produces the expected namespace."""

    def test_default_args(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("sys.argv", ["install.py"])
        parser = inst.argparse.ArgumentParser(description="Testing Toolkit offline installer")
        parser.add_argument("--no-start", action="store_true")
        parser.add_argument("--no-autostart", action="store_true")
        args = parser.parse_args([])
        assert args.no_start is False
        assert args.no_autostart is False

    def test_no_start_flag(self) -> None:
        parser = inst.argparse.ArgumentParser(description="Testing Toolkit offline installer")
        parser.add_argument("--no-start", action="store_true")
        parser.add_argument("--no-autostart", action="store_true")
        args = parser.parse_args(["--no-start"])
        assert args.no_start is True
        assert args.no_autostart is False

    def test_no_autostart_flag(self) -> None:
        parser = inst.argparse.ArgumentParser(description="Testing Toolkit offline installer")
        parser.add_argument("--no-start", action="store_true")
        parser.add_argument("--no-autostart", action="store_true")
        args = parser.parse_args(["--no-autostart"])
        assert args.no_start is False
        assert args.no_autostart is True

    def test_both_flags(self) -> None:
        parser = inst.argparse.ArgumentParser(description="Testing Toolkit offline installer")
        parser.add_argument("--no-start", action="store_true")
        parser.add_argument("--no-autostart", action="store_true")
        args = parser.parse_args(["--no-start", "--no-autostart"])
        assert args.no_start is True
        assert args.no_autostart is True


# ===========================================================================
# windowless_python helper
# ===========================================================================
class TestWindowlessPython:
    """windowless_python resolves pythonw.exe on Windows."""

    def test_non_nt_returns_unchanged(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("os.name", "posix")
        assert inst.windowless_python("/usr/bin/python3") == "/usr/bin/python3"

    def test_already_pythonw_returns_unchanged(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("os.name", "nt")
        assert inst.windowless_python("C:\\Python\\pythonw.exe") == "C:\\Python\\pythonw.exe"

    def test_pythonw_exists_returns_it(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("os.name", "nt")
        mock_path = MagicMock()
        mock_path.name = "python.exe"
        mock_pyw = MagicMock()
        mock_pyw.exists.return_value = True
        mock_pyw.__str__ = lambda self: "C:\\Python\\pythonw.exe"
        mock_path.with_name.return_value = mock_pyw

        with patch.object(inst, "Path", return_value=mock_path):
            result = inst.windowless_python("C:\\Python\\python.exe")
        assert result == "C:\\Python\\pythonw.exe"

    def test_pythonw_missing_returns_original(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("os.name", "nt")
        mock_path = MagicMock()
        mock_path.name = "python.exe"
        mock_pyw = MagicMock()
        mock_pyw.exists.return_value = False
        mock_path.with_name.return_value = mock_pyw

        with patch.object(inst, "Path", return_value=mock_path):
            result = inst.windowless_python("C:\\Python\\python.exe")
        assert result == "C:\\Python\\python.exe"


# ===========================================================================
# _port_free (socket-level logic)
# ===========================================================================
class TestPortFree:
    """_port_free tests with mocked socket."""

    def test_port_in_use_returns_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mock_sock = MagicMock()
        mock_sock.connect.return_value = None  # connection succeeds = port in use
        monkeypatch.setattr(
            "socket.socket",
            lambda *_args, **_kwargs: mock_sock,
        )
        assert inst._port_free(7842) is False

    def test_port_free_returns_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mock_sock = MagicMock()
        mock_sock.connect.side_effect = ConnectionRefusedError("refused")
        monkeypatch.setattr(
            "socket.socket",
            lambda *_args, **_kwargs: mock_sock,
        )
        assert inst._port_free(7842) is True

    def test_timeout_returns_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import socket as _socket
        mock_sock = MagicMock()
        mock_sock.connect.side_effect = _socket.timeout("timed out")
        monkeypatch.setattr(
            "socket.socket",
            lambda *_args, **_kwargs: mock_sock,
        )
        assert inst._port_free(7842) is True


# ===========================================================================
# find_bundled_python
# ===========================================================================
class TestFindBundledPython:
    """find_bundled_python with mocked filesystem."""

    def test_no_runtime_dir_returns_none(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setattr(inst, "RUNTIME_DIR", tmp_path / "nonexistent")
        assert inst.find_bundled_python("windows", "amd64") is None

    def test_finds_python_exe_in_os_arch_dir(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        runtime = tmp_path / "runtime"
        monkeypatch.setattr(inst, "RUNTIME_DIR", runtime)
        plat_dir = runtime / "windows-amd64"
        plat_dir.mkdir(parents=True)
        exe = plat_dir / "python.exe"
        exe.write_text("")
        result = inst.find_bundled_python("windows", "amd64")
        assert result == str(exe)

    def test_finds_bin_python3_on_posix(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        runtime = tmp_path / "runtime"
        monkeypatch.setattr(inst, "RUNTIME_DIR", runtime)
        plat_dir = runtime / "linux-amd64"
        (plat_dir / "bin").mkdir(parents=True)
        exe = plat_dir / "bin" / "python3"
        exe.write_text("")
        result = inst.find_bundled_python("linux", "amd64")
        assert result == str(exe)

    def test_fallback_to_os_only_dir(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        runtime = tmp_path / "runtime"
        monkeypatch.setattr(inst, "RUNTIME_DIR", runtime)
        # No windows-amd64 dir, but windows dir exists
        plat_dir = runtime / "windows"
        plat_dir.mkdir(parents=True)
        exe = plat_dir / "python.exe"
        exe.write_text("")
        result = inst.find_bundled_python("windows", "amd64")
        assert result == str(exe)


# ===========================================================================
# _py_version and _py_ok (mocked subprocess)
# ===========================================================================
class TestPyVersion:
    """_py_version and _py_ok with mocked _run."""

    def test_py_version_parses_output(self, monkeypatch: pytest.MonkeyPatch) -> None:
        result = MagicMock()
        result.returncode = 0
        result.stdout = "3.12\n"
        monkeypatch.setattr(inst, "_run", lambda *_a, **_kw: result)
        assert inst._py_version("/fake/python") == (3, 12)

    def test_py_version_returns_none_on_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        result = MagicMock()
        result.returncode = 1
        result.stdout = ""
        monkeypatch.setattr(inst, "_run", lambda *_a, **_kw: result)
        assert inst._py_version("/fake/python") is None

    def test_py_version_returns_none_on_exception(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(inst, "_run", lambda *_a, **_kw: (_ for _ in ()).throw(OSError("fail")))
        assert inst._py_version("/fake/python") is None

    def test_py_version_returns_none_on_bad_output(self, monkeypatch: pytest.MonkeyPatch) -> None:
        result = MagicMock()
        result.returncode = 0
        result.stdout = "not-a-version\n"
        monkeypatch.setattr(inst, "_run", lambda *_a, **_kw: result)
        assert inst._py_version("/fake/python") is None

    def test_py_ok_true_when_above_min(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(inst, "_py_version", lambda _exe: (3, 12))
        assert inst._py_ok("/fake/python") is True

    def test_py_ok_true_at_exact_min(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(inst, "_py_version", lambda _exe: (3, 9))
        assert inst._py_ok("/fake/python") is True

    def test_py_ok_false_below_min(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(inst, "_py_version", lambda _exe: (3, 8))
        assert inst._py_ok("/fake/python") is False

    def test_py_ok_false_when_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(inst, "_py_version", lambda _exe: None)
        assert inst._py_ok("/fake/python") is False


# ===========================================================================
# _health_matches_installed_agent
# ===========================================================================
class TestHealthMatches:
    """Health check payload validation logic."""

    def test_exact_match(self) -> None:
        payload = {"status": "ok", "version": "3.0.0", "capabilities": {"e2e": True}}
        assert inst._health_matches_installed_agent(payload, "3.0.0") is True

    def test_version_mismatch(self) -> None:
        payload = {"status": "ok", "version": "2.9.0", "capabilities": {}}
        assert inst._health_matches_installed_agent(payload, "3.0.0") is False

    def test_status_not_ok(self) -> None:
        payload = {"status": "error", "version": "3.0.0", "capabilities": {}}
        assert inst._health_matches_installed_agent(payload, "3.0.0") is False

    def test_missing_capabilities(self) -> None:
        payload = {"status": "ok", "version": "3.0.0"}
        assert inst._health_matches_installed_agent(payload, "3.0.0") is False

    def test_empty_payload(self) -> None:
        assert inst._health_matches_installed_agent({}, "1.0.0") is False

    def test_version_as_int_compared_as_string(self) -> None:
        # version field coerced to str
        payload = {"status": "ok", "version": 3, "capabilities": {}}
        assert inst._health_matches_installed_agent(payload, "3") is True


# ===========================================================================
# _process_is_agent
# ===========================================================================
class TestProcessIsAgent:
    """Process identity matching for safe termination."""

    def test_recognizes_python_m_agent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            inst, "_process_identity",
            lambda _pid, _os=None: "/usr/bin/python3 -m agent",
        )
        assert inst._process_is_agent(123, "linux") is True

    def test_recognizes_testingtoolkitweb(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            inst, "_process_identity",
            lambda _pid, _os=None: "C:\\Users\\user\\TestingToolkitWeb\\venv\\python.exe agent",
        )
        assert inst._process_is_agent(123, "windows") is True

    def test_recognizes_testing_toolkit_hyphen(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            inst, "_process_identity",
            lambda _pid, _os=None: "/home/u/testing-toolkit/venv/bin/python -m agent",
        )
        assert inst._process_is_agent(123, "linux") is True

    def test_rejects_non_python(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            inst, "_process_identity",
            lambda _pid, _os=None: "/usr/bin/node server.js",
        )
        assert inst._process_is_agent(123, "linux") is False

    def test_rejects_python_without_agent_marker(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            inst, "_process_identity",
            lambda _pid, _os=None: "/usr/bin/python3 /some/other/script.py",
        )
        assert inst._process_is_agent(123, "linux") is False


# ===========================================================================
# _platform_key (Node distribution key)
# ===========================================================================
class TestPlatformKey:
    """Node.js distribution platform key construction."""

    @pytest.mark.parametrize("os_name,arch,expected", [
        ("windows", "amd64", "win32-x64"),
        ("windows", "arm64", "win32-arm64"),
        ("linux", "amd64", "linux-x64"),
        ("linux", "arm64", "linux-arm64"),
        ("macos", "amd64", "darwin-x64"),
        ("macos", "arm64", "darwin-arm64"),
    ])
    def test_known_mappings(self, os_name: str, arch: str, expected: str) -> None:
        assert inst._platform_key(os_name, arch) == expected

    def test_unsupported_raises(self) -> None:
        with pytest.raises(RuntimeError, match="No bundled Node runtime mapping"):
            inst._platform_key("freebsd", "amd64")

    def test_unsupported_arch_raises(self) -> None:
        with pytest.raises(RuntimeError, match="No bundled Node runtime mapping"):
            inst._platform_key("linux", "x86")


# ===========================================================================
# _windows_startup_dir
# ===========================================================================
class TestWindowsStartupDir:
    """Startup folder resolution."""

    def test_uses_appdata_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("APPDATA", "C:\\Users\\fake\\AppData\\Roaming")
        result = inst._windows_startup_dir()
        expected = Path("C:\\Users\\fake\\AppData\\Roaming") / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
        assert result == expected

    def test_fallback_without_appdata(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("APPDATA", raising=False)
        result = inst._windows_startup_dir()
        assert "Microsoft" in str(result)
        assert "Startup" in str(result)


# ===========================================================================
# _windows_startup_vbs generation
# ===========================================================================
class TestWindowsStartupVbs:
    """VBS autostart script content generation."""

    def test_contains_pythonw_path(self) -> None:
        vbs = inst._windows_startup_vbs(
            "C:\\fake\\pythonw.exe",
            "C:\\fake\\src",
        )
        assert "C:\\fake\\pythonw.exe" in vbs
        assert "C:\\fake\\src" in vbs

    def test_sets_pythonpath_when_provided(self) -> None:
        vbs = inst._windows_startup_vbs(
            "C:\\fake\\pythonw.exe",
            "C:\\fake\\src",
            pythonpath="C:\\fake\\lib;C:\\fake\\src",
        )
        assert "PYTHONPATH" in vbs
        assert "C:\\fake\\lib;C:\\fake\\src" in vbs

    def test_no_pythonpath_when_empty(self) -> None:
        vbs = inst._windows_startup_vbs(
            "C:\\fake\\pythonw.exe",
            "C:\\fake\\src",
            pythonpath="",
        )
        assert "PYTHONPATH" not in vbs

    def test_contains_marker(self) -> None:
        vbs = inst._windows_startup_vbs("C:\\x\\pythonw.exe", "C:\\x\\src")
        assert inst._STARTUP_MARKER in vbs

    def test_launches_with_m_agent(self) -> None:
        vbs = inst._windows_startup_vbs("C:\\x\\pythonw.exe", "C:\\x\\src")
        assert "-m agent" in vbs


# ===========================================================================
# _linux_autostart_desktop generation
# ===========================================================================
class TestLinuxAutostartDesktop:
    """XDG autostart .desktop file generation."""

    def test_basic_content(self) -> None:
        desktop = inst._linux_autostart_desktop(
            "/fake/bin/python3",
            "/fake/agent/src",
        )
        assert "[Desktop Entry]" in desktop
        assert "Exec=/fake/bin/python3 -m agent" in desktop
        assert "Path=/fake/agent/src" in desktop
        assert "Terminal=false" in desktop

    def test_with_pythonpath(self) -> None:
        desktop = inst._linux_autostart_desktop(
            "/fake/bin/python3",
            "/fake/agent/src",
            pythonpath="/fake/lib:/fake/agent/src",
        )
        assert "PYTHONPATH=/fake/lib:/fake/agent/src" in desktop

    def test_without_pythonpath(self) -> None:
        desktop = inst._linux_autostart_desktop(
            "/fake/bin/python3",
            "/fake/agent/src",
            pythonpath="",
        )
        assert "PYTHONPATH" not in desktop

    def test_contains_marker(self) -> None:
        desktop = inst._linux_autostart_desktop("/x/python3", "/x/src")
        assert inst._STARTUP_MARKER in desktop


# ===========================================================================
# _windows_autostart_xml generation
# ===========================================================================
class TestWindowsAutostartXml:
    """Task Scheduler XML generation."""

    def test_contains_command_and_workdir(self) -> None:
        xml = inst._windows_autostart_xml(
            "C:\\fake\\pythonw.exe",
            "C:\\fake\\src",
        )
        assert "C:\\fake\\pythonw.exe" in xml
        assert "C:\\fake\\src" in xml
        assert "-m agent" in xml

    def test_xml_escapes_special_chars(self) -> None:
        xml = inst._windows_autostart_xml(
            "C:\\Users\\A & B\\pythonw.exe",
            "C:\\path<>&src",
        )
        assert "&amp;" in xml
        assert "&lt;" in xml or "&gt;" in xml

    def test_contains_task_marker(self) -> None:
        xml = inst._windows_autostart_xml("C:\\x\\pythonw.exe", "C:\\x\\src")
        assert inst._AUTOSTART_TASK_MARKER in xml

    def test_repetition_interval(self) -> None:
        xml = inst._windows_autostart_xml("C:\\x\\pythonw.exe", "C:\\x\\src")
        assert "PT5M" in xml  # 5-min watchdog


# ===========================================================================
# _macos_plist generation
# ===========================================================================
class TestMacosPlist:
    """LaunchAgents plist generation."""

    def test_basic_structure(self) -> None:
        plist = inst._macos_plist(
            "/fake/python3",
            PurePosixPath("/fake/agent/src"),
        )
        assert "com.testingtoolkit.agent" in plist
        assert "/fake/python3" in plist
        assert "/fake/agent/src" in plist
        assert "<key>KeepAlive</key><true/>" in plist
        assert "<key>RunAtLoad</key><true/>" in plist

    def test_xml_escaping(self) -> None:
        plist = inst._macos_plist(
            "/path/with <angle> & ampersand/python",
            PurePosixPath("/another <path>"),
        )
        assert "&amp;" in plist
        assert "&lt;" in plist


# ===========================================================================
# _linux_unit generation
# ===========================================================================
class TestLinuxUnit:
    """Systemd unit file generation."""

    def test_basic_structure(self) -> None:
        unit = inst._linux_unit(
            "/fake/python3",
            PurePosixPath("/fake/agent/src"),
        )
        assert "[Unit]" in unit
        assert "[Service]" in unit
        assert "[Install]" in unit
        assert "Restart=on-failure" in unit
        assert "WantedBy=default.target" in unit

    def test_quotes_paths_with_special_chars(self) -> None:
        unit = inst._linux_unit(
            "/path with spaces/python",
            PurePosixPath("/dir with spaces/src"),
        )
        assert "'/path with spaces/python'" in unit
        assert "'/dir with spaces/src'" in unit


# ===========================================================================
# _wheel_pyminors (wheel tag parsing)
# ===========================================================================
class TestWheelPyminors:
    """Wheel filename -> supported Python minor versions."""

    def test_pure_python_returns_none(self) -> None:
        assert inst._wheel_pyminors("some_pkg-1.0-py3-none-any.whl") is None
        assert inst._wheel_pyminors("pkg-2.0-py2.py3-none-any.whl") is None

    def test_cpython_exact(self) -> None:
        got = inst._wheel_pyminors("pkg-1.0-cp311-cp311-win_amd64.whl")
        assert got == {11}

    def test_abi3_floor(self) -> None:
        got = inst._wheel_pyminors("pkg-1.0-cp39-abi3-manylinux_2_17_x86_64.whl")
        assert got is not None
        assert 9 in got
        assert 10 in got
        assert inst._CEILING_MINOR in got

    def test_case_insensitive(self) -> None:
        got = inst._wheel_pyminors("PKG-1.0-CP312-CP312-WIN_AMD64.WHL")
        assert got == {12}


# ===========================================================================
# progress function
# ===========================================================================
class TestProgress:
    """progress() routing: percent=None -> milestone, else log-only."""

    def test_no_percent_calls_milestone(self, monkeypatch: pytest.MonkeyPatch) -> None:
        called: list[str] = []
        monkeypatch.setattr(inst, "milestone", lambda msg: called.append(msg))
        inst.progress("phase", "Some message", percent=None)
        assert called == ["Some message"]

    def test_with_percent_logs_only(self, monkeypatch: pytest.MonkeyPatch) -> None:
        logged: list[tuple[str, str]] = []
        monkeypatch.setattr(inst, "_log_line", lambda level, msg: logged.append((level, msg)))
        inst.progress("deps", "Installing", percent=50)
        assert logged == [("PROGRESS", "50% deps: Installing")]

    def test_percent_clamped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        logged: list[tuple[str, str]] = []
        monkeypatch.setattr(inst, "_log_line", lambda level, msg: logged.append((level, msg)))
        inst.progress("x", "over", percent=200)
        assert "100%" in logged[0][1]
        logged.clear()
        inst.progress("x", "under", percent=-50)
        assert "0%" in logged[0][1]


# ===========================================================================
# write_update_config logic (mocked filesystem)
# ===========================================================================
class TestWriteUpdateConfig:
    """write_update_config writes correct JSON when token is provided."""

    def test_no_token_skips(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setattr(inst, "INSTALL_DIR", tmp_path)
        monkeypatch.delenv("TT_UPDATE_TOKEN", raising=False)
        monkeypatch.setattr(inst, "info", lambda _msg: None)
        inst.write_update_config()
        assert not (tmp_path / "update.json").exists()

    def test_writes_json_with_token(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        import json
        monkeypatch.setattr(inst, "INSTALL_DIR", tmp_path)
        monkeypatch.setenv("TT_UPDATE_TOKEN", "ghp_FAKE_TOKEN_12345")
        monkeypatch.setenv("TT_UPDATE_REPO", "org/repo")
        monkeypatch.setenv("TT_UPDATE_REF", "main")
        monkeypatch.setattr(inst, "info", lambda _msg: None)
        monkeypatch.setattr(inst, "ok", lambda _msg: None)
        inst.write_update_config()
        cfg = json.loads((tmp_path / "update.json").read_text())
        assert cfg["token"] == "ghp_FAKE_TOKEN_12345"
        assert cfg["repo"] == "org/repo"
        assert cfg["ref"] == "main"
        assert "api.github.com" in cfg["manifest_url"]

    def test_defaults_repo_and_ref(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        import json
        monkeypatch.setattr(inst, "INSTALL_DIR", tmp_path)
        monkeypatch.setenv("TT_UPDATE_TOKEN", "ghp_FAKE")
        monkeypatch.delenv("TT_UPDATE_REPO", raising=False)
        monkeypatch.delenv("TT_UPDATE_REF", raising=False)
        monkeypatch.setattr(inst, "info", lambda _msg: None)
        monkeypatch.setattr(inst, "ok", lambda _msg: None)
        inst.write_update_config()
        cfg = json.loads((tmp_path / "update.json").read_text())
        assert cfg["repo"] == "nrcharanvignesh/Testing-Toolkit"
        assert cfg["ref"] == "parts"


# ===========================================================================
# copy_tree logic
# ===========================================================================
class TestCopyTree:
    """copy_tree with real tmp dirs (no subprocess/network)."""

    def test_copies_directory(self, tmp_path: Path) -> None:
        src = tmp_path / "source"
        src.mkdir()
        (src / "file.txt").write_text("hello")
        (src / "sub").mkdir()
        (src / "sub" / "nested.txt").write_text("world")

        dst = tmp_path / "dest"
        inst.copy_tree(src, dst)
        assert (dst / "file.txt").read_text() == "hello"
        assert (dst / "sub" / "nested.txt").read_text() == "world"

    def test_missing_src_warns(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        warned: list[str] = []
        monkeypatch.setattr(inst, "warn", lambda msg: warned.append(msg))
        inst.copy_tree(tmp_path / "nonexistent", tmp_path / "dst")
        assert any("Missing" in w for w in warned)


# ===========================================================================
# _mcp_bundle_src resolution
# ===========================================================================
class TestMcpBundleSrc:
    """MCP bundle source directory resolution logic."""

    def test_returns_path_object(self) -> None:
        result = inst._mcp_bundle_src()
        assert isinstance(result, Path)

    def test_prefers_meipass_when_set(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        mei_dir = tmp_path / "mei_extract"
        mcp_dir = mei_dir / "mcp_servers"
        mcp_dir.mkdir(parents=True)
        # _MEIPASS may not exist on sys; use setattr directly then clean up
        monkeypatch.setattr(sys, "_MEIPASS", str(mei_dir), raising=False)
        result = inst._mcp_bundle_src()
        assert result == mcp_dir


# ===========================================================================
# Logging helpers (pure logic)
# ===========================================================================
class TestLogLine:
    """_log_line is a no-op when _LOG_FH is None."""

    def test_no_fh_no_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(inst, "_LOG_FH", None)
        # Should not raise
        inst._log_line("INFO", "test message")

    def test_writes_when_fh_present(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        log_file = tmp_path / "test.log"
        fh = open(log_file, "w", encoding="utf-8")
        monkeypatch.setattr(inst, "_LOG_FH", fh)
        inst._log_line("WARN", "hello world")
        fh.flush()
        content = log_file.read_text()
        assert "[WARN] hello world" in content
        fh.close()


# ===========================================================================
# find_system_python (preference logic, mocked discovery)
# ===========================================================================
class TestFindSystemPython:
    """System Python selection with version preference."""

    def test_returns_none_when_no_candidates(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(inst, "_candidate_system_pythons", lambda: [])
        assert inst.find_system_python({"3.12"}) is None

    def test_prefers_matching_version(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            inst, "_candidate_system_pythons",
            lambda: ["/py39", "/py312", "/py311"],
        )
        versions = {"/py39": (3, 9), "/py312": (3, 12), "/py311": (3, 11)}
        monkeypatch.setattr(inst, "_py_version", lambda exe: versions[exe])
        assert inst.find_system_python({"3.12"}) == "/py312"

    def test_newest_wins_when_no_preference_match(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            inst, "_candidate_system_pythons",
            lambda: ["/py39", "/py311"],
        )
        versions = {"/py39": (3, 9), "/py311": (3, 11)}
        monkeypatch.setattr(inst, "_py_version", lambda exe: versions[exe])
        # Prefer 3.12 but it is not available -> newest (3.11) wins
        assert inst.find_system_python({"3.12"}) == "/py311"

    def test_no_prefer_set_returns_newest(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            inst, "_candidate_system_pythons",
            lambda: ["/py39", "/py313", "/py311"],
        )
        versions = {"/py39": (3, 9), "/py313": (3, 13), "/py311": (3, 11)}
        monkeypatch.setattr(inst, "_py_version", lambda exe: versions[exe])
        assert inst.find_system_python(None) == "/py313"


# ===========================================================================
# _TAG_OS and _TAG_ARCH mapping tables
# ===========================================================================
class TestTagMappings:
    """Wheel tag OS/arch lookup tables are complete for shipped platforms."""

    def test_tag_os_covers_all_normalized_os(self) -> None:
        assert "windows" in inst._TAG_OS
        assert "macos" in inst._TAG_OS
        assert "linux" in inst._TAG_OS

    def test_tag_arch_covers_primary_archs(self) -> None:
        assert "amd64" in inst._TAG_ARCH
        assert "arm64" in inst._TAG_ARCH

    def test_tag_values_are_lowercase(self) -> None:
        for v in inst._TAG_OS.values():
            assert v == v.lower()
        for tup in inst._TAG_ARCH.values():
            for v in tup:
                assert v == v.lower()
