"""Build a side-loadable APK; optional Windows-only portable JDK/SDK bootstrap."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import os
from pathlib import Path
import shutil
import subprocess
import sys
import urllib.request
from zipfile import BadZipFile, ZipFile


ROOT = Path(__file__).resolve().parents[1]
TOOLING = ROOT / ".build-tools"
MOBILE = ROOT / "mobile"


def download(url, name):
    destination = TOOLING / name
    if destination.is_file():
        try:
            with ZipFile(destination) as cached:
                if cached.testzip() is None:
                    return destination
        except BadZipFile:
            pass
    print("Downloading " + name, flush=True)
    with urllib.request.urlopen(url, timeout=90) as response, destination.open("wb") as output:
        shutil.copyfileobj(response, output)
    with ZipFile(destination) as archive:
        if archive.testzip():
            raise RuntimeError("Corrupt download: " + name)
    print("Downloaded " + name, flush=True)
    return destination


def extract(archive, target):
    target.mkdir(parents=True, exist_ok=True)
    with ZipFile(archive) as bundle:
        for member in bundle.namelist():
            if not (target / member).resolve().is_relative_to(target.resolve()):
                raise ValueError("Unexpected archive path")
        bundle.extractall(target)


def prepare_gradle():
    # Verify the mirror download against Gradle's official checksum before use.
    archive = download("https://mirrors.cloud.tencent.com/gradle/gradle-8.11.1-bin.zip", "gradle-8.11.1-bin.zip")
    checksum_url = "https://services.gradle.org/distributions/gradle-8.11.1-bin.zip.sha256"
    with urllib.request.urlopen(checksum_url, timeout=30) as response:
        expected = response.read().decode("ascii").strip().split()[0]
    if sha256(archive.read_bytes()).hexdigest() != expected:
        raise RuntimeError("Gradle checksum verification failed.")
    gradle_home = TOOLING / "gradle/gradle-8.11.1"
    if not (gradle_home / "bin/gradle.bat").is_file():
        extract(archive, TOOLING / "gradle")
    return gradle_home


def bootstrap():
    if sys.platform != "win32":
        raise RuntimeError("Portable bootstrap is for Windows. Elsewhere install JDK 21 and Android SDK 35.")
    TOOLING.mkdir(exist_ok=True)
    jdk_root = TOOLING / "jdk"
    sdk_root = TOOLING / "android-sdk"
    with ThreadPoolExecutor(max_workers=3) as pool:
        jdk = pool.submit(download, "https://cdn.azul.com/zulu/bin/zulu21.40.17-ca-jdk21.0.6-win_x64.zip", "zulu21.zip")
        sdk = pool.submit(download, "https://dl.google.com/android/repository/commandlinetools-win-13114758_latest.zip", "android-tools.zip")
        gradle = pool.submit(prepare_gradle)
        if not list(jdk_root.glob("*/bin/java.exe")):
            extract(jdk.result(), jdk_root)
        if not (sdk_root / "cmdline-tools/latest/bin/sdkmanager.bat").is_file():
            extract(sdk.result(), TOOLING / "android-unpack")
            (sdk_root / "cmdline-tools").mkdir(parents=True, exist_ok=True)
            shutil.copytree(TOOLING / "android-unpack/cmdline-tools", sdk_root / "cmdline-tools/latest", dirs_exist_ok=True)
    java_home = next(jdk_root.glob("*/bin/java.exe")).parents[1]
    env = {**os.environ, "JAVA_HOME": str(java_home), "ANDROID_HOME": str(sdk_root),
           "ANDROID_SDK_ROOT": str(sdk_root), "AITXT_GRADLE_HOME": str(gradle.result())}
    manager = sdk_root / "cmdline-tools/latest/bin/sdkmanager.bat"
    with (TOOLING / "sdk-install.log").open("w", encoding="utf-8") as log:
        print("Accepting Android SDK licenses; details in .build-tools/sdk-install.log", flush=True)
        subprocess.run([str(manager), "--sdk_root=" + str(sdk_root), "--licenses"],
                       input="y\n" * 100, text=True, stdout=log, stderr=log, env=env, check=True)
        print("Installing Android SDK 35 / build-tools 35.0.0", flush=True)
        subprocess.run([str(manager), "--sdk_root=" + str(sdk_root), "platforms;android-35", "build-tools;35.0.0"],
                       input="y\n" * 100, text=True, stdout=log, stderr=log, env=env, check=True)
    return env


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bootstrap", action="store_true", help="Download a portable Windows JDK 21 and Android SDK.")
    args = parser.parse_args()
    env = bootstrap() if args.bootstrap else os.environ.copy()
    if sys.platform == "win32" and not args.bootstrap:
        jdks = list((TOOLING / "jdk").glob("*/bin/java.exe"))
        if jdks:
            env.setdefault("JAVA_HOME", str(jdks[0].parents[1]))
        if (TOOLING / "android-sdk").is_dir():
            env.setdefault("ANDROID_HOME", str(TOOLING / "android-sdk"))
        if (TOOLING / "gradle/gradle-8.11.1/bin/gradle.bat").is_file():
            env.setdefault("AITXT_GRADLE_HOME", str(TOOLING / "gradle/gradle-8.11.1"))
    env["GRADLE_USER_HOME"] = str(TOOLING / "gradle-cache")
    env["AITXT_PUBLIC_API_URL"] = os.environ.get("AITXT_PUBLIC_API_URL", "http://49.232.132.111/api/v1")
    npm = shutil.which("npm.cmd" if sys.platform == "win32" else "npm")
    if not npm:
        raise RuntimeError("Install Node.js 22+ first.")
    if not (MOBILE / "node_modules/@capacitor/cli").is_dir():
        subprocess.run([npm, "ci"], cwd=MOBILE, env=env, check=True)
    subprocess.run([npm, "run", "sync:android"], cwd=MOBILE, env=env, check=True)
    wrapper = MOBILE / "android" / ("gradlew.bat" if sys.platform == "win32" else "gradlew")
    sdk_home = env.get("ANDROID_HOME") or env.get("ANDROID_SDK_ROOT")
    if not sdk_home:
        raise RuntimeError("Set ANDROID_HOME to your Android SDK, or use --bootstrap on Windows.")
    (MOBILE / "android/local.properties").write_text(
        "sdk.dir=" + Path(sdk_home).as_posix() + "\n", encoding="utf-8",
    )
    gradle_home = env.get("AITXT_GRADLE_HOME")
    gradle = Path(gradle_home) / "bin/gradle.bat" if gradle_home else wrapper
    subprocess.run([str(gradle), "--no-daemon", "--no-watch-fs", "--max-workers=2",
                    "-Dorg.gradle.internal.instrumentation.agent=false", "assembleDebug"],
                   cwd=wrapper.parent, env=env, check=True)
    output = ROOT / "dist/mojian-android-0.1.0-debug.apk"
    output.parent.mkdir(exist_ok=True)
    shutil.copy2(MOBILE / "android/app/build/outputs/apk/debug/app-debug.apk", output)
    signer = Path(sdk_home) / "build-tools/35.0.0" / ("apksigner.bat" if sys.platform == "win32" else "apksigner")
    subprocess.run([str(signer), "verify", "--verbose", str(output)], env=env, check=True)
    with ZipFile(output) as apk:
        config = apk.read("assets/public/runtime-config.js").decode("utf-8")
        if env["AITXT_PUBLIC_API_URL"].rstrip("/") not in config:
            raise RuntimeError("APK public API address does not match the build configuration.")
        if any(Path(name).name == ".env" for name in apk.namelist()):
            raise RuntimeError("Unexpected server environment file in APK.")
    print("APK_READY=" + str(output), flush=True)


if __name__ == "__main__":
    main()
