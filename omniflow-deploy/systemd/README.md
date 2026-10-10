# systemd timers (preferred over cron: they catch up after downtime)

```bash
# 1. edit WorkingDirectory/ExecStart/User in the .service files to match the server
sudo cp systemd/omniflow-*.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now omniflow-backup.timer omniflow-certbot.timer
systemctl list-timers 'omniflow-*'
journalctl -u omniflow-backup.service -n 100 --no-pager      # last run's log
```
`./backup-status.sh` tells you whether the newest backup is fresh and whether an off-host copy exists.
