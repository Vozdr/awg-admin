# Несколько AWG-серверов

[Главная](../README.md) · [Все переменные окружения](ENVIRONMENT_RU.md) · [Диагностика](TROUBLESHOOTING_RU.md)

Один контейнер AWG Admin может управлять несколькими AWG-серверами на одном или разных MikroTik. У каждого сервера собственное API-подключение, AWG-контейнер, endpoint клиентов, значения по умолчанию и состояние ошибок. Выбранный в шапке сервер сохраняется в браузере.

## Правило имён

Первый сервер задаётся обычными переменными без суффикса. Второй использует суффикс `_2`, третий — `_3` и так далее. Номера могут идти с пропусками: например, серверы без суффикса, `_2` и `_5` являются допустимой конфигурацией.

Дополнительный сервер объявляется переменной `MIKROTIK_HOST_N`. Для него обязательны:

- `MIKROTIK_HOST_N`;
- `MIKROTIK_USERNAME_N`;
- `MIKROTIK_PASSWORD_N`;
- `AWG_CLIENT_ENDPOINT_N`.

Если хотя бы одного обязательного параметра объявленного сервера нет, AWG Admin не запускается и сообщает имя отсутствующей переменной.

## Пример второго сервера

```routeros
/container/envs/add list=awg-admin key=MIKROTIK_HOST_2 value=192.168.50.1
/container/envs/add list=awg-admin key=MIKROTIK_USERNAME_2 value=awg-admin
/container/envs/add list=awg-admin key=MIKROTIK_PASSWORD_2 value="ЗАМЕНИТЕ_ПАРОЛЬ"
/container/envs/add list=awg-admin key=AWG_CLIENT_ENDPOINT_2 value=branch.example.org:51820
/container/envs/add list=awg-admin key=AWG_CONTAINER_NAME_2 value=wg-awg-branch
/container/envs/add list=awg-admin key=AWG_SERVER_NAME_2 value="Филиал"
/container/envs/add list=awg-admin key=MIKROTIK_API_SSL_2 value=yes
/container/envs/add list=awg-admin key=MIKROTIK_SSL_VERIFY_2 value=no
/container/envs/add list=awg-admin key=CLIENT_DEFAULT_DNS_2 value=1.1.1.1,8.8.8.8
```

Все необязательные параметры сервера поддерживают тот же суффикс. Если они не заданы, для каждого сервера независимо применяются значения по умолчанию из [справочника env](ENVIRONMENT_RU.md).

## Общие параметры панели

Авторизация и интерфейс принадлежат самой панели, поэтому следующие переменные не дублируются для серверов: `ADMIN_USERNAME`, `ADMIN_PASSWORD`, `AUTH_DISABLE`, `COOKIE_SECURE`, `SESSION_SECRET`, `DEFAULT_LANG`, `DEFAULT_THEME`, `LOG_LEVEL`, `LOCALES_DIR`.

## Ошибки отдельных серверов

Панель проверяет все настроенные серверы при запуске. Ошибка подключения или конфигурации одного дополнительного сервера не блокирует работу исправных серверов: проблемный сервер остаётся в списке с предупреждением, а его ошибка отображается при выборе. Контейнер AWG Admin прекращает запуск только тогда, когда не доступен ни один настроенный сервер. Недостаточные права API, как и раньше, оставляют диагностический интерфейс доступным.
