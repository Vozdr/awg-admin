# Несколько AWG-серверов

[Главная](../README.md) · [English](MULTI_SERVER_EN.md) · [Все переменные окружения](ENVIRONMENT_RU.md) · [Диагностика](TROUBLESHOOTING_RU.md)

Один контейнер AWG Admin может управлять несколькими AWG-серверами на одном или разных MikroTik. У каждого сервера собственное API-подключение, AWG-контейнер, endpoint клиентов, значения по умолчанию и состояние ошибок. Выбранный в шапке сервер сохраняется в браузере.

## Правило имён

Первый сервер задаётся обычными переменными без суффикса. Второй использует суффикс `_2`, третий — `_3` и так далее. Номера могут идти с пропусками: например, серверы без суффикса, `_2` и `_5` являются допустимой конфигурацией.

Дополнительный сервер объявляется переменной `MIKROTIK_HOST_N`. Для него обязательны:

- `MIKROTIK_HOST_N`;
- `MIKROTIK_USERNAME_N`;
- `MIKROTIK_PASSWORD_N`;
- `AWG_CLIENT_ENDPOINT_N`.

Это проверка структуры env, выполняемая до подключения к MikroTik. Если у любого объявленного сервера отсутствует или пуст хотя бы один из четырёх обязательных параметров, конфигурация всей панели считается неполной: AWG Admin не запускается и сообщает имя переменной. Такой сервер нельзя оставить в списке с предупреждением, потому что данных недостаточно даже для создания подключения.

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

После успешной проверки обязательных env панель подключается к каждому описанному серверу и проверяет его ресурсы в MikroTik. Недоступность MikroTik, ошибка API или проблема с AWG-контейнером одного сервера не блокирует работу исправных серверов: проблемный сервер остаётся в списке с предупреждением, а полученная ошибка отображается при выборе.

AWG Admin прекращает запуск, если обязательные env неполны хотя бы у одного объявленного сервера либо если после проверок не осталось ни одного работоспособного сервера. Исключение — недостаточные права API: при этой ошибке диагностический интерфейс остаётся доступным.
