"""Exercise Python selection without root, network access or service changes."""

import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


INSTALLER = Path(__file__).resolve().parents[1] / "deployment" / "install.sh"


def shell_path(path):
    value = Path(path).resolve().as_posix()
    if os.name == "nt":
        return f"/{value[0].lower()}{value[2:]}"
    return value


@pytest.fixture
def shell():
    if os.name == "nt" and shutil.which("git"):
        candidate = Path(shutil.which("git")).resolve().parents[1] / "bin" / "bash.exe"
        if candidate.exists():
            return str(candidate)
    candidate = shutil.which("bash")
    if not candidate:
        pytest.skip("Bash is required for installer tests")
    return candidate


def executable(path, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/bash\nset -eu\n" + body + "\n", encoding="utf-8")
    path.chmod(0o755)
    return shell_path(path)


def resolve(shell, project, **settings):
    environment = os.environ.copy()
    environment.pop("PYTHON_BIN", None)
    environment.update(TEST_PYTHON_EXECUTABLE=shell_path(sys.executable), **settings)
    return subprocess.run(
        [shell, "-c", 'source "$1"; resolve_python "$2"', "installer-test",
         shell_path(INSTALLER), shell_path(project)],
        env=environment, capture_output=True, text=True,
    )


def test_installer_shell_syntax(shell):
    subprocess.run([shell, "-n", shell_path(INSTALLER)], check=True)


def test_explicit_python_is_used_without_installing_tools(shell, tmp_path):
    interpreter = shell_path(sys.executable)
    result = resolve(shell, tmp_path, PYTHON_BIN=interpreter)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == interpreter
    assert not (tmp_path / ".build-tools").exists()


def test_existing_virtualenv_is_reused(shell, tmp_path):
    interpreter = executable(tmp_path / ".venv/bin/python", 'exec "$TEST_PYTHON_EXECUTABLE" "$@"')
    result = resolve(shell, tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == interpreter
    assert not (tmp_path / ".build-tools").exists()


def test_python_314_is_rejected_before_installing(shell, tmp_path):
    interpreter = executable(
        tmp_path / "python314",
        "exec \"$TEST_PYTHON_EXECUTABLE\" -c "
        "'import sys; sys.version_info = (3, 14, 0); exec(sys.argv[1])' \"$2\"",
    )
    result = resolve(shell, tmp_path, PYTHON_BIN=interpreter)
    assert result.returncode != 0
    assert "Python 3.10-3.13 required" in result.stderr
    assert not (tmp_path / ".build-tools").exists()


def test_missing_system_python_uses_project_managed_runtime(shell, tmp_path):
    empty_path = tmp_path / "empty-path"
    empty_path.mkdir()
    executable(
        tmp_path / ".build-tools/uv/uv",
        'printf "%s|%s\\n" "$UV_PYTHON_INSTALL_DIR" "$*" >> "$CALL_LOG"\n'
        'case "$2" in\n'
        '  install) printf "install progress\\n" ;;\n'
        '  find) printf "%s\\n" "$TEST_PYTHON_EXECUTABLE" ;;\n'
        '  *) exit 2 ;;\n'
        'esac',
    )
    log = tmp_path / "calls.log"
    result = resolve(shell, tmp_path, PATH=shell_path(empty_path), CALL_LOG=shell_path(log))
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == shell_path(sys.executable)
    assert "install progress" in result.stderr
    assert log.read_text().splitlines() == [
        f"{shell_path(tmp_path / '.build-tools/python')}|python install 3.12",
        f"{shell_path(tmp_path / '.build-tools/python')}|python find --managed-python 3.12",
    ]


def test_download_failure_stops_before_virtualenv_creation(shell, tmp_path):
    empty_path = tmp_path / "empty-path"
    empty_path.mkdir()
    executable(tmp_path / ".build-tools/uv/uv", 'echo "download failed" >&2; exit 17')
    result = resolve(shell, tmp_path, PATH=shell_path(empty_path))
    assert result.returncode != 0
    assert "download failed" in result.stderr
    assert not (tmp_path / ".venv").exists()
