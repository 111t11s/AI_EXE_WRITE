# 墨间 Android

Android App 复用 frontend 页面，小说内容保存在服务器，默认 API 为 http://49.232.132.111/api/v1。手机与网页直接进入同一书架；目前不区分用户，所有能访问服务器的人均可编辑共享项目。APK 不包含数据库密码或模型密钥。

## 编译安装包

需要 Node.js 22+、JDK 21、Android SDK Platform 35 和 Build Tools 35.0.0。可安装 Android Studio 来管理这些工具。

从源码包的 aitxt 目录进入 mobile：

~~~powershell
npm.cmd ci
npm.cmd run sync:android
cd android
.\gradlew.bat assembleDebug
~~~

Windows 也可在 aitxt 根目录运行下列命令，自动下载便携 JDK/SDK 并编译（首次下载数百 MB，要求已安装 Python 3.12 和 Node.js）：

~~~powershell
python tools/build_android.py --bootstrap
~~~

自动构建的 APK 输出到 dist/mojian-android-0.1.0-debug.apk；构建工具在 .build-tools 下，不会打入源码包。后续更新请保留同一台机器的 Android debug 签名。

APK 在 mobile/android/app/build/outputs/apk/debug/app-debug.apk，适合朋友侧载试用。安装时允许该文件来源安装应用。服务器部署并启动后，打开 App 即可进入书架。旧版 App 也可在服务器更新后直接进入，若此前停在口令弹窗，关闭并重新打开 App 即可。

修改公开 API 地址时，在同步前设置 AITXT_PUBLIC_API_URL，例如：

~~~powershell
$env:AITXT_PUBLIC_API_URL='http://49.232.132.111/api/v1'
npm.cmd run sync:android
~~~

本版本只对 49.232.132.111 放行明文 HTTP；其他服务器请使用 HTTPS。CapacitorHttp 让 App 通过原生网络访问该 IP，避免本地 HTTPS 页面访问 HTTP API 时受浏览器混合内容限制。前端页面仍打包在 APK 内。

也可以把源码放在自己的 GitHub 仓库后，手动运行 Actions → Build Android APK，下载构建产物。当前操作不会自动创建仓库、推送代码或上传你的数据。

## 后续更新

本地 debug 签名保存在构建机器的 Android 用户目录，更新安装需要使用同一把签名。不同机器或临时 CI 构建可能使用不同的 debug 签名，发生签名冲突时需卸载旧 App 后安装（服务器小说数据不受影响）。正式长期分发请在 Android Studio 的 Generate Signed Bundle / APK 中创建并保管 release 签名，不要提交签名和密码。

App 需要联网，不支持离线写作。安装包不会自动更新前端，更新功能后需重新同步并打包 APK；后端修复可独立发布。最低支持 Android 6，实际使用建议 Android 10+ 且 Android System WebView 保持更新。
