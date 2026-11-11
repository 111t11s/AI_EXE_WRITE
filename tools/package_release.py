"""Build allowlisted archives; never copy a workstation's .env, DB or logs."""

from hashlib import sha256
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo
import json


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "dist"
VERSION = "0.1.0"
EXCLUDED_PARTS = {
    ".git", ".venv", "node_modules", "__pycache__", ".gradle", ".idea",
    "build", "www", "logs", ".build-tools",
}
TEXT_SUFFIXES = {".py", ".js", ".mjs", ".css", ".html", ".json", ".txt", ".md", ".sh", ".service", ".conf", ".yml"}


def files(roots):
    for root in roots:
        entry = ROOT / root
        if not entry.exists():
            raise FileNotFoundError(entry)
        for item in sorted(entry.rglob("*")) if entry.is_dir() else [entry]:
            relative = item.relative_to(ROOT)
            if (not item.is_file() or item.is_symlink() or set(relative.parts) & EXCLUDED_PARTS
                    or item.name in {".env", "local.properties", "google-services.json"}
                    or item.suffix in {".pyc", ".log", ".jks", ".keystore", ".apk"}
                    or "/src/main/assets/" in relative.as_posix()):
                continue
            yield item


def archive(name, roots, prefix="aitxt"):
    destination = OUTPUT / name
    with ZipFile(destination, "w", compression=ZIP_DEFLATED) as bundle:
        for item in files(roots):
            relative = item.relative_to(ROOT)
            data = item.read_bytes()
            if item.suffix in TEXT_SUFFIXES or item.name in {".env.example", "gradlew"}:
                data = data.replace(b"\r\n", b"\n")
            info = ZipInfo((Path(prefix) / relative).as_posix())
            info.create_system = 3
            info.compress_type = ZIP_DEFLATED
            mode = 0o755 if item.suffix == ".sh" or item.name == "gradlew" else 0o644
            info.external_attr = (0o100000 | mode) << 16
            bundle.writestr(info, data)
    with ZipFile(destination) as bundle:
        assert not any(Path(name).name == ".env" for name in bundle.namelist())
        assert bundle.testzip() is None
    return destination


def main():
    OUTPUT.mkdir(exist_ok=True)
    packages = [
        archive(f"aitxt-server-linux-{VERSION}.zip", [
            "main.py", "requirements-server.txt", "api", "agents", "core", "db",
            "frontend", "deployment",
        ]),
        archive(f"aitxt-android-source-{VERSION}.zip", [
            "frontend", "mobile", ".github/workflows/android-apk.yml",
            "tools/build_android.py",
        ]),
        archive(f"aitxt-web-{VERSION}.zip", ["frontend"], prefix=""),
    ]
    apk = OUTPUT / f"mojian-android-{VERSION}-debug.apk"
    if apk.is_file():
        packages.append(apk)
    manifest = {
        "version": VERSION,
        "server": "http://49.232.132.111",
        "artifacts": [
            {"file": path.name, "bytes": path.stat().st_size, "sha256": sha256(path.read_bytes()).hexdigest()}
            for path in packages
        ],
    }
    (OUTPUT / "release-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    for entry in manifest["artifacts"]:
        print(f'{entry["file"]}: {entry["bytes"]} bytes')


if __name__ == "__main__":
    main()
