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

只启动一个后端进程。世界观问答会话暂存在该进程内存中；重启后需要重新开始未完成的问答，已经保存到 MySQL 的内容保留。不配置多 worker 或负载均衡。登录会话保存在数据库，重启后仍然有效。

~~~bash
sudo systemctl status aitxt
sudo systemctl restart aitxt
sudo journalctl -u aitxt -f
~~~

更新前备份 .env 和数据库，覆盖代码时保留 .env，再重新安装依赖并重启服务。服务日志在 /var/log/aitxt/，运行数据在 /var/lib/aitxt/。生产默认关闭 Swagger 文档，需要排查接口时可临时设置 ENABLE_API_DOCS=true 并重启。

内部 MCP 通过 127.0.0.1:8000 回调 API，不需要公开额外的 MCP 端口。API 失败时前端显示具体错误，可结合 journalctl 排查数据库、模型网关连接。

## 账号与旧小说迁移

升级已有数据库时，`schema.sql` 的 `CREATE TABLE IF NOT EXISTS` 不会补齐旧表字段，必须运行迁移。先备份数据库、更新代码，再执行：

```bash
cd /opt/aitxt
sudo systemctl stop aitxt
sudo .venv/bin/python -m db.migrate_accounts --create-account admin --legacy-owner admin
sudo systemctl start aitxt
```

命令会新增 `app_account`、`app_auth_session` 表，并给小说项目和问答会话增加 `account_id` 及索引、外键。首次创建 `admin` 时在终端输入两次密码（至少 8 位），密码不会显示或出现在命令历史中。若账号已存在，会保留原密码。只有归属为空的旧小说和旧会话会绑定给 `admin`；新账号的已有数据不会改变。命令可重复执行，不会删除小说，也不会重新分配已绑定数据。`admin` 是指定的小说归属账号。

执行迁移的数据库账号需要 CREATE、ALTER、INDEX 等建表改表权限；应用日常需要相关表的 SELECT、INSERT、UPDATE、DELETE 权限。如果迁移仅执行了一部分，补齐权限后重新执行即可。完成后强制刷新网页，Android 需重新构建并安装。

也可以先执行 `python -m db.migrate_accounts` 只补齐结构，在页面注册 `admin`，再执行 `python -m db.migrate_accounts --legacy-owner admin` 绑定旧数据。未分配的数据不会出现在任何账号的书架中。

可通过 `.env` 的 `AUTH_REGISTRATION_ENABLED=false` 关闭开放注册；已有账号仍可登录。`AUTH_SESSION_HOURS` 控制登录有效期，默认 168 小时。退出会立即撤销当前登录令牌。浏览器在当前标签页的 sessionStorage 中保存令牌，普通接口和流式接口均通过 Authorization 请求头携带身份。

## 修改与重置密码

登录后在账号栏选择“修改密码”，输入当前密码和两次新密码（8—128位）。修改成功后，需要重新登录所有设备。

忘记密码时，由有权访问服务器的管理员执行：

```bash
cd /opt/aitxt
sudo .venv/bin/python -m db.reset_password admin
```

将 `admin` 替换为要重置的用户名，按提示输入两次新密码，输入不会回显。重置会立即撤销该账号的全部登录会话，旧密码和旧令牌无法继续使用，其他账号与小说数据保持原样。命令只操作已存在的账号，不会创建账号或启用停用账号；无需修改表结构或重启服务。不要将密码写入命令行或聊天记录。
