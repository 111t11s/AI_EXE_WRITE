CREATE USER 'aitxt_remote'@'%'
  IDENTIFIED BY '1c7db05fef55a2ac3636939fa9ba933d65316bb6f798bb9a'
  REQUIRE SSL;

GRANT SELECT, SHOW VIEW ON AI_TEXT.* TO 'aitxt_remote'@'%';

EXIT;

# 拉取指令
sudo git -C /opt/aitxt pull --ff-only origin master
# 重启后端
sudo systemctl restart aitxt


cd /opt/aitxt
sudo systemctl stop aitxt

sudo mysql -e "GRANT ALTER, INDEX ON \`AI_TEXT\`.* TO 'aitxt'@'localhost'; FLUSH PRIVILEGES;"

sudo .venv/bin/python -m db.migrate_project_trash

sudo mysql -e "REVOKE ALTER, INDEX ON \`AI_TEXT\`.* FROM 'aitxt'@'localhost'; FLUSH PRIVILEGES;"

sudo systemctl start aitxt
sudo systemctl status aitxt --no-pager