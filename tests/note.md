CREATE USER 'aitxt_remote'@'%'
  IDENTIFIED BY '1c7db05fef55a2ac3636939fa9ba933d65316bb6f798bb9a'
  REQUIRE SSL;

GRANT SELECT, SHOW VIEW ON AI_TEXT.* TO 'aitxt_remote'@'%';

EXIT;

# 拉取指令
sudo git -C /opt/aitxt pull --ff-only origin master
# 重启后端
sudo systemctl restart aitxt