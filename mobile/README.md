# 墨间 Android

Android App 复用 frontend 页面，小说内容保存在服务器，默认 API 为 http://49.232.132.111/api/v1。手机与网页使用同一套账号注册登录，同一账号共享个人书架，不同账号的数据隔离。APK 不包含数据库密码或模型密钥。升级账号功能后需要重新构建并安装 App；旧版没有登录入口，无法访问受保护接口。

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

自动构建的 APK 输出到 dist/mojian-android-0.2.3-debug.apk；构建工具在 .build-tools 下，不会打入源码包。后续更新请保留同一台机器的 Android debug 签名。

2026-11-16 人物进展更新包为 `mojian-android-0.2.3-20261116-character-progress-debug.apk`。正文生成或重写后自动记录人物行动、当前状态、关系变化与正文原句依据，角色页可展开查看“正文进展（已发生）”，按页加载历史记录。续写按章序使用先前人物状态；基础设定及未来计划分别展示。需部署同名日期的 `character-progress` 服务器包并创建 `novel_character_progress` 表，参见 `deployment/README.md`。已有正文仅在重新生成或重写后同步。本包包含登录保持 30 天及按章节批量导出 ZIP。

0.2.3 增加管理员书架：授权的管理员可查看全部账号的小说，按所属账号筛选，并阅读和导出正文、大纲与角色档案。其他账号的小说只读，自己的小说仍可创作。需更新后端并迁移 `app_account.is_admin` 字段，通过服务器 `python -m db.manage_admin admin --create` 显式创建或授权管理员；没有预设密码。普通用户继续使用个人书架。

0.2.2 新增按大纲分卷保存角色阶段档案，包括具体行动、阶段结果、剧情节点与进度关系。新增和复用配角时同时保存；主角与重要配角生成可选择依据大纲，已有角色可单独补生成。角色页按大纲版本展示分卷档案与当前正文数量。阶段结果明确标为计划，不以已写章节数量推断结局已发生。需部署对应后端，本次沿用分卷角色戏份表，无需新增迁移。

0.2.1 强化一致性核对：开始前读取完整大纲、人物、人物关系、分卷戏份与相关设定；报告展示实际读取档案，每个问题须有正文和档案原文依据，资料不足不凭空判断。需要部署对应的最新服务器代码，才能启用新核对规则；本次无需新增数据库迁移。

0.2.0 同步最新工作台：任务中心分类、角色生成与前卷配角复用、大纲和正文重写、一致性核对，以及正文区域独立滚动。小说标题区域导出 ZIP 压缩包，每章一个 TXT 或 Markdown 文件，支持全部正文或跨页勾选章节批量导出，大纲单独成文件。使用 Android 系统文件选择器选择压缩包保存位置，可取消，不申请全盘存储权限。导出通过原生网络携带登录令牌下载，逐块写入缓存和目标文件，避免把长篇小说编码后跨 WebView 桥传输；批量选择通过 POST 请求正文提交。使用此功能需重新构建并安装 APK，同时更新后端；无需新增数据库迁移，分卷角色功能仍需先建好相应的戏份表。

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
