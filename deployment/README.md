# Linux 部署：49.232.132.111

网页地址为 http://49.232.132.111/，Android 使用同一台服务器的 /api/v1。后端只监听本机 8000，经 Nginx 的 80 端口提供服务。推荐 Ubuntu 24.04、Python 3.12、MySQL 8。安装脚本面向 systemd 系统，不会自动连接或修改远程服务器。其他 Ubuntu 版本的软件源可能没有 python3.12；脚本会在需要时通过 uv 下载项目独立的 Python 3.12，不替换系统 Python。当前 CrewAI 依赖要求 Python 3.10–3.13，不支持直接使用 Python 3.14。

## 安装

上传 aitxt-server-linux-0.1.0.zip 到服务器。以下命令在服务器执行：

~~~bash
sudo apt update
sudo apt install -y curl ca-certificates mysql-server nginx unzip
sudo unzip aitxt-server-linux-0.1.0.zip -d /opt
cd /opt/aitxt
sudo bash deployment/install.sh
sudo nano /opt/aitxt/.env
~~~

install.sh 创建独立运行用户和虚拟环境、安装 Linux 依赖，并注册 systemd 服务，但不会立即启动。修改 .env 的 DB_HOST、DB_PORT、DB_USER、DB_PASSWORD、DB_NAME，以及 API_KEY、PROXY_URL、MODEL_NAME。不要把本机 .venv 或原始 Windows requirements.txt 上传用于安装。

通过 Git 部署时，将仓库克隆到 /opt/aitxt 后执行同一个 install.sh，无需解压。脚本优先复用已有的兼容虚拟环境，再使用系统 python3.12，否则将 uv 和 Python 安装到 /opt/aitxt/.build-tools/。自动下载需要访问 Astral/GitHub；下载失败时可以先自行安装兼容 Python，再运行 `sudo env PYTHON_BIN=/实际路径/python3.12 bash deployment/install.sh`。不要把运行时安装在 /root 下，服务的 ProtectHome 设置会阻止访问该目录。已有不兼容虚拟环境会使安装停止，需要先备份并移走 .venv 后重新安装。

如果之前执行 `apt install python3.12 ...` 因找不到包失败，请重新执行上面的系统依赖安装命令；失败的那次事务可能没有安装 MySQL 和 Nginx。此时不必重装操作系统。

网页和 App 先注册或登录，再打开个人书架。可以从 .env 移除旧的 `APP_ACCESS_KEY` 和 `ACCESS_KEY_REQUIRED` 两行。模型网关仍需要单独配置 `API_KEY`。

当前小说、世界观、大纲等数据不会打进代码包。若继续使用已有数据库，把连接配置指向该库即可。新建数据库时，先创建 utf8mb4 数据库与专用账户，再使用 MySQL 客户端导入 db/schema.sql。此 SQL 不会创建数据库或 MySQL 登录用户。迁移已有数据时先单独备份并导入数据库，不要只导入空表结构。

## 启动

~~~bash
sudo systemctl enable --now aitxt
curl --fail http://127.0.0.1:8000/healthz
sudo journalctl -u aitxt -n 60 --no-pager
~~~

Nginx 配置部署前先备份现有站点。如果这台机器已经托管其他网站，请合并配置，不要覆盖它们。以下按全新 Ubuntu Nginx 默认站点替换：

~~~bash
sudo cp /etc/nginx/sites-available/default /etc/nginx/sites-available/default.backup
sudo cp /opt/aitxt/deployment/nginx.conf /etc/nginx/sites-available/default
sudo nginx -t
sudo systemctl reload nginx
~~~

每个账号只能访问自己的书架及其世界观、大纲、章节和问答会话。8000、MySQL 3306 不需要对公网开放。

手机浏览器和 App 均连接 http://49.232.132.111/。本次按 IP 直连使用 HTTP，小说内容没有传输加密；建议在可信网络或 VPN 内使用，未来配域名后可切换 HTTPS。

## 运行与更新

小说标题区域增加「导出小说」，可下载 TXT 或 Markdown 并选择附带大纲。导出使用已有数据库资料，无需安装额外依赖或新增 SQL 迁移；覆盖代码并重启服务后强制刷新网页即可使用。

大纲与正文支持「重写」，可设置修改要求。大纲重写保存为新版本；正文重写成功后替换本章正文并恢复草稿状态，期间被编辑的章节会拒绝覆盖。此次重写功能无需新增 SQL 迁移，更新代码并重启服务后强制刷新网页即可。此前的分卷角色功能仍需按下文完成 `novel_volume_character` 表迁移。

只启动一个后端进程。世界观问答会话暂存在该进程内存中；重启后需要重新开始未完成的问答，已经保存到 MySQL 的内容保留。不配置多 worker 或负载均衡。登录会话保存在数据库，重启后仍然有效。

世界观、大纲、章节规划、正文和审读均支持后台任务。发起后可以继续操作，通过“生成任务”或任务卡片的“查看进度”打开实时内容。浏览器刷新或关闭进度页不会取消后台生成；重新登录同一账号仍可在任务列表查看。任务按账号隔离，同一目标正在生成时重复提交会返回原任务，每个账号最多同时运行3个任务。

任务进度暂存于当前服务进程，完成后保留24小时，每个账号保留最近约50个任务；服务重启会清除任务与实时片段，已成功保存到数据库的小说内容保留。该版本不支持跨服务重启恢复未完成的生成。

~~~bash
sudo systemctl status aitxt
sudo systemctl restart aitxt
sudo journalctl -u aitxt -f
~~~

更新前备份 .env 和数据库，覆盖代码时保留 .env，再重新安装依赖并重启服务。服务日志在 /var/log/aitxt/，运行数据在 /var/lib/aitxt/。生产默认关闭 Swagger 文档，需要排查接口时可临时设置 ENABLE_API_DOCS=true 并重启。

内部 MCP 通过 127.0.0.1:8000 回调 API，不需要公开额外的 MCP 端口。API 失败时前端显示具体错误，可结合 journalctl 排查数据库、模型网关连接。

## 账号与旧小说迁移

本次分卷配角复用功能新增 `novel_volume_character` 表。已有数据库升级后执行 `python -m db.migrate_volume_characters`，再重启服务。此命令仅补建戏份表，可重复执行，保留已有小说、角色和大纲。新数据库导入最新版 `db/schema.sql` 即可。

升级已有数据库时，`schema.sql` 的 `CREATE TABLE IF NOT EXISTS` 不会补齐旧表字段，必须运行迁移。先备份数据库、更新代码，再执行：

```bash
cd /opt/aitxt
sudo systemctl stop aitxt
sudo .venv/bin/python -m db.migrate_accounts --create-account admin --legacy-owner admin
sudo systemctl start aitxt
```

命令会新增 `app_account`、`app_auth_session` 表，并给小说项目和问答会话增加 `account_id` 及索引、外键。首次创建 `admin` 时在终端输入两次密码（至少 8 位），密码不会显示或出现在命令历史中。若账号已存在，会保留原密码。只有归属为空的旧小说和旧会话会绑定给 `admin`；新账号的已有数据不会改变。命令可重复执行，不会删除小说，也不会重新分配已绑定数据。`admin` 是指定的小说归属账号。

执行迁移的数据库账号需要 CREATE、ALTER、INDEX 等建表改表权限；应用日常需要相关表的 SELECT、INSERT、UPDATE、DELETE 权限。如果迁移仅执行了一部分，补齐权限后重新执行即可。完成后强制刷新网页，Android 需重新构建并安装。

也可以先执行 `python -m db.migrate_accounts` 只补齐结构，在页面注册 `admin`，再执行 `python -m db.migrate_accounts --legacy-owner admin` 绑定旧数据。未分配的数据只对已授权的管理员可见，普通账号不可见。

可通过 `.env` 的 `AUTH_REGISTRATION_ENABLED=false` 关闭开放注册；已有账号仍可登录。`AUTH_SESSION_HOURS` 控制登录有效期，默认 720 小时（30 天），从登录时开始计算，重新打开页面不会延长有效期。浏览器在 localStorage 中保存令牌，关闭页面或浏览器后仍可恢复登录；普通接口和流式接口均通过 Authorization 请求头携带身份。退出、修改密码或会话过期会清除本地登录状态。升级前若已在 `.env` 配置较短有效期，将 `AUTH_SESSION_HOURS` 改为 `720` 并重启服务；已有会话保留原到期时间，重新登录后使用 30 天有效期，无需数据库迁移。

## 修改与重置密码

登录后在账号栏选择“修改密码”，输入当前密码和两次新密码（8—128位）。修改成功后，需要重新登录所有设备。

忘记密码时，由有权访问服务器的管理员执行：

```bash
cd /opt/aitxt
sudo .venv/bin/python -m db.reset_password admin
```

将 `admin` 替换为要重置的用户名，按提示输入两次新密码，输入不会回显。重置会立即撤销该账号的全部登录会话，旧密码和旧令牌无法继续使用，其他账号与小说数据保持原样。命令只操作已存在的账号，不会创建账号或启用停用账号；无需修改表结构或重启服务。不要将密码写入命令行或聊天记录。

## 更新一致性核对（2026-11-15）

将 `aitxt-server-linux-20261115-consistency.zip` 上传到服务器的 `/home/ubuntu/`，在 Ubuntu SSH 终端执行：

```bash
sudo unzip -o /home/ubuntu/aitxt-server-linux-20261115-consistency.zip -d /opt
sudo systemctl restart aitxt
```

本次只更新代码与页面，无需新增数据库迁移，也不会覆盖服务器 `.env`。网页刷新后使用新报告；手机安装 `mojian-android-0.2.1-debug.apk`，可覆盖安装旧版。必须更新服务器才能启用先读取档案、验证原文依据的新核对规则。

## 更新角色阶段档案（2026-11-15）

将 `aitxt-server-linux-20261115-character-profiles.zip` 上传到 `/home/ubuntu/`，在 Ubuntu SSH 终端执行：

```bash
sudo unzip -o /home/ubuntu/aitxt-server-linux-20261115-character-profiles.zip -d /opt
sudo systemctl restart aitxt
```

安装 Android `mojian-android-0.2.2-debug.apk` 更新手机界面。新配角及勾选复用的配角会同时生成阶段档案；已有主角和配角可补生成。本次使用现有 `novel_volume_character.scenes` 保存可读的阶段记录，不新增表或字段，不需要新的数据库迁移。旧记录保留，可通过「生成阶段档案」补齐；服务器和手机均更新后使用完整功能。

## 管理员查看全部账号小说（2026-11-15）

管理员登录后书架显示全部账号的小说，包含未分配的旧小说，可按所属账号筛选，阅读正文、大纲、角色阶段档案并导出。其他账号的小说为只读，编辑、删除和生成仍要求小说归属当前账号。个人问答会话和后台任务保持账号隔离。用户名为 `admin` 不会自动获得管理员权限，普通注册也无法设置管理员标记。

先在 Windows 命令提示符上传更新包：

```bat
scp "C:\Users\Administrator\PycharmProjects\AITXT\dist\aitxt-server-linux-20261115-admin.zip" ubuntu@49.232.132.111:/home/ubuntu/
ssh ubuntu@49.232.132.111
```

然后在 **Ubuntu SSH 终端**执行以下命令。此服务器数据库为 `AI_TEXT`，应用数据库账号此前缺少建表权限，因此使用数据库 root 执行幂等字段迁移，再用应用命令创建或授权管理员：

```bash
sudo systemctl stop aitxt
sudo unzip -o /home/ubuntu/aitxt-server-linux-20261115-admin.zip -d /opt
cd /opt/aitxt
sudo mysql AI_TEXT < deployment/admin-role.sql
sudo .venv/bin/python -m db.manage_admin admin --create
sudo systemctl start aitxt
```

`admin` 可替换为指定用户名。账号不存在时交互式输入两次密码（8—128位，不回显）；已存在则保留原密码，只授予管理员权限。迁移不改动小说归属、正文或已有密码，无需扩大应用数据库账号的权限。部署后刷新网页，手机安装 `mojian-android-0.2.3-debug.apk`。已登录账号刷新或重新登录后显示管理员界面。

其他部署若执行迁移的数据库账号已有 ALTER 权限，可用 `sudo .venv/bin/python -m db.migrate_admin` 替代上面的 root SQL 命令。字段存在时该命令不再执行 ALTER。新数据库导入最新版 `db/schema.sql` 已包含该字段。

仅授权已有账号：`sudo .venv/bin/python -m db.manage_admin 用户名`。撤销：`sudo .venv/bin/python -m db.manage_admin 用户名 --revoke`。权限在每次请求时从数据库读取，撤销后旧登录令牌也无法继续跨账号读取。CLI 只在有服务器访问权限的终端使用，没有公开提权接口或默认密码。

## 正文自动更新人物档案（2026-11-16）

更新包为 `aitxt-server-linux-20261116-character-progress.zip`，Android 安装包为 `mojian-android-0.2.3-20261116-character-progress-debug.apk`。生成或重写正文时，同时保存人物已发生的行动、截至本章状态、关系变化和正文原句依据；后续续写读取章号早于当前章的最新人物状态。角色页将这些记录标为“正文进展（已发生）”，与基础设定及未来大纲计划分别展示。

本次新增 `novel_character_progress` 表。更新前备份数据库和 `.env`，将更新包上传至 `/home/ubuntu/`，然后在 **Ubuntu SSH 终端**执行：

```bash
sudo systemctl stop aitxt
sudo unzip -o /home/ubuntu/aitxt-server-linux-20261116-character-progress.zip -d /opt
cd /opt/aitxt
sudo mysql AI_TEXT < deployment/character-progress.sql
sudo systemctl start aitxt
```

表已存在时 SQL 不会重复创建；应用数据库账号具备 CREATE 权限的部署也可执行 `sudo .venv/bin/python -m db.migrate_character_progress`。更新包保留服务器 `.env`，不含本机密码。刷新网页，手机安装本次 APK。该包也包含 30 天登录保持和每章单文件的 ZIP 批量导出。

已有正文不会自动补抽取，需重新生成或重写后同步。重写替换本章旧人物进展；手动修改正文、章号或所属分卷会清除本章旧进展，删除章节同时删除其记录。基础人物身份与大纲阶段计划保留。正文和进展在同一事务保存，人物 ID 或原句依据校验失败时，本章不保存。
