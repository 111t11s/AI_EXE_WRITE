# Linux 部署：49.232.132.111

网页地址为 http://49.232.132.111/，Android 使用同一台服务器的 /api/v1。后端只监听本机 8000，经 Nginx 的 80 端口提供服务。推荐 Ubuntu 24.04、Python 3.12、MySQL 8。安装脚本面向 systemd 系统，不会自动连接或修改远程服务器。

## 安装

上传 aitxt-server-linux-0.1.0.zip 到服务器。以下命令在服务器执行：

~~~bash
sudo apt update
sudo apt install -y python3.12 python3.12-venv python3-pip nginx unzip
sudo unzip aitxt-server-linux-0.1.0.zip -d /opt
cd /opt/aitxt
sudo bash deployment/install.sh
sudo nano /opt/aitxt/.env
~~~

install.sh 创建独立运行用户和虚拟环境、安装 Linux 依赖，并注册 systemd 服务，但不会立即启动。第一次安装会生成随机访问口令。修改 .env 的 DB_HOST、DB_PORT、DB_USER、DB_PASSWORD、DB_NAME，以及 API_KEY、PROXY_URL、MODEL_NAME。不要把本机 .venv 或原始 Windows requirements.txt 上传用于安装。

APP_ACCESS_KEY 是分享给朋友的访问口令，可改为自己保管的随机长口令。ACCESS_KEY_REQUIRED=true 时，缺少口令将拒绝启动。网页和 App 首次进入均需输入此口令。模型 API_KEY 与这个口令是两回事，不要分享模型 API_KEY。

当前小说、世界观、大纲等数据不会打进代码包。若继续使用已有数据库，把连接配置指向该库即可。新建数据库时，先创建 utf8mb4 数据库与专用账户，再使用 MySQL 客户端导入 db/schema.sql。此 SQL 不会创建数据库或账户。迁移已有数据时先单独备份并导入数据库，不要只导入空表结构。

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

云服务器安全组和本机防火墙开放 TCP 80 给朋友使用；若朋友公网 IP 固定，限制来源到这些 IP。8000、MySQL 3306 不需要对公网开放。访问口令控制应用访问，不能让一个公网 IP 自动变成私网；如果需要网络层完全不对公网开放，请通过 VPN/私网连接。

手机浏览器和 App 均连接 http://49.232.132.111/。本次按 IP 直连使用 HTTP，口令和小说内容没有传输加密；建议在可信网络或 VPN 内使用，未来配域名后可切换 HTTPS。

## 运行与更新

只启动一个后端进程。世界观问答会话暂存在该进程内存中；重启后需要重新开始未完成的问答，已经保存到 MySQL 的内容保留。不配置多 worker 或负载均衡。朋友共享书架，当前没有用户隔离和每人权限。

~~~bash
sudo systemctl status aitxt
sudo systemctl restart aitxt
sudo journalctl -u aitxt -f
~~~

更新前备份 .env 和数据库，覆盖代码时保留 .env，再重新安装依赖并重启服务。服务日志在 /var/log/aitxt/，运行数据在 /var/lib/aitxt/。生产默认关闭 Swagger 文档，需要排查接口时可临时设置 ENABLE_API_DOCS=true 并重启。

内部 MCP 通过 127.0.0.1:8000 回调 API，并自动带上访问口令，不需要公开额外的 MCP 端口。API 失败时前端显示具体错误，可结合 journalctl 排查数据库、模型网关连接。
