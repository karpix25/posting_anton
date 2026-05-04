# Настройка автоматического запуска

## Конфигурация расписания

В `config.json` добавлена секция `schedule`:

```json
{
  "schedule": {
    "enabled": true,
    "timezone": "Europe/Moscow",
    "dailyRunTime": "00:01"
  }
}
```

### Параметры:

- **`enabled`** - включить/выключить автоматический запуск (`true`/`false`)
- **`timezone`** - часовой пояс (например, `"Europe/Moscow"` для МСК)
- **`dailyRunTime`** - время запуска в формате `"HH:MM"` (например, `"00:01"` для 00:01)

## Настройка Cron

### Вариант 1: Через crontab (рекомендуется)

1. Откройте crontab для редактирования:
```bash
crontab -e
```

2. Добавьте строку (запуск каждую минуту, скрипт сам проверит время):
```bash
* * * * * cd /path/to/project && npm run schedule >> /var/log/automation-schedule.log 2>&1
```

3. Замените `/path/to/project` на реальный путь к проекту

4. Сохраните и закройте редактор

### Вариант 2: Через systemd timer (альтернатива)

Создайте файл `/etc/systemd/system/automation-schedule.service`:

```ini
[Unit]
Description=Automation Schedule Check

[Service]
Type=oneshot
WorkingDirectory=/path/to/project
ExecStart=/usr/bin/npm run schedule
```

Создайте файл `/etc/systemd/system/automation-schedule.timer`:

```ini
[Unit]
Description=Run automation schedule check every minute

[Timer]
OnCalendar=*:*:00
Persistent=true

[Install]
WantedBy=timers.target
```

Активируйте:
```bash
sudo systemctl enable automation-schedule.timer
sudo systemctl start automation-schedule.timer
```

## Изменение времени запуска

Просто отредактируйте `config.json`:

```json
{
  "schedule": {
    "enabled": true,
    "timezone": "Europe/Moscow",
    "dailyRunTime": "03:00"  // Запуск в 3:00 по МСК
  }
}
```

Cron автоматически подхватит новые настройки при следующей проверке.

## Изменение часового пояса

Доступные часовые пояса:
- `"Europe/Moscow"` - Москва (UTC+3)
- `"Europe/Kiev"` - Киев (UTC+2)
- `"Asia/Yekaterinburg"` - Екатеринбург (UTC+5)
- `"Asia/Novosibirsk"` - Новосибирск (UTC+7)
- `"UTC"` - UTC (UTC+0)

Полный список: https://en.wikipedia.org/wiki/List_of_tz_database_time_zones

## Проверка работы

### Просмотр логов:
```bash
tail -f /var/log/automation-schedule.log
```

### Проверка текущего времени в настроенном часовом поясе:
```bash
TZ=Europe/Moscow date
```

### Ручной запуск для проверки:
```bash
npm run schedule
```

## Отключение автоматического запуска

Установите `"enabled": false` в `config.json`:

```json
{
  "schedule": {
    "enabled": false,
    "timezone": "Europe/Moscow",
    "dailyRunTime": "00:01"
  }
}
```

Cron продолжит работать, но скрипт будет пропускать запуск.
