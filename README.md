# AWG Admin

[English](README_EN.md) · [Все переменные окружения](docs/ENVIRONMENT_RU.md) · [Несколько серверов](docs/MULTI_SERVER_RU.md) · [Диагностика](docs/TROUBLESHOOTING_RU.md)

AWG Admin — веб-панель управления клиентами AmneziaWG для MikroTik. Она рассчитана на серверный контейнер [`amneziawg-mikrotik-c` от timbrs](https://github.com/timbrs/amneziawg-mikrotik-c) и работает с RouterOS напрямую через API или API-SSL.

Панель не использует базу данных и локальные конфиги. Клиенты, комментарии, адреса, ключи и статистика берутся непосредственно из MikroTik. Список публичных ключей синхронизируется с `AWG_CLIENT_PUBS` контейнера proxy.

Возможности:

- автоматическое назначение клиенту первого свободного IPv4-адреса из выбранной подсети интерфейса WireGuard;
- добавление, включение, отключение и удаление peer;
- генерирование QR-кода, `.conf`-файла и ссылки с конфигурацией для peer;
- статистика по каждому peer;
- просмотр настроек AWG-сервера;
- управление несколькими AWG-серверами из одной панели;
- локализация интерфейса.

## Быстрый запуск на MikroTik

Требуются RouterOS v7 с пакетом Container, настроенный `awg-proxy` в режиме сервера и доступный сервис `api` либо `api-ssl`. AWG-контейнер может быть остановлен — это не мешает работе панели.

Создайте отдельную группу и пользователя. Панели нужны только `read`, `write`, `sensitive` и `api`:

```routeros
/user/group/add name=awg-admin policy=read,write,sensitive,api
/user/add name=awg-admin group=awg-admin password="ЗАМЕНИТЕ_ПАРОЛЬ" address=172.17.0.2/32
```

В примере адрес MikroTik — `172.17.0.1`, адрес контейнера — `172.17.0.2`:

```routeros
/interface/veth/add name=veth-awg-admin address=172.17.0.2/24 gateway=172.17.0.1
/ip/address/add address=172.17.0.1/24 interface=veth-awg-admin

/container/envs/add list=awg-admin key=MIKROTIK_HOST value=172.17.0.1
/container/envs/add list=awg-admin key=MIKROTIK_USERNAME value=awg-admin
/container/envs/add list=awg-admin key=MIKROTIK_PASSWORD value="ЗАМЕНИТЕ_ПАРОЛЬ"
/container/envs/add list=awg-admin key=AWG_CLIENT_ENDPOINT value=vpn.example.org:51820
/container/envs/add list=awg-admin key=ADMIN_USERNAME value=admin
/container/envs/add list=awg-admin key=ADMIN_PASSWORD value="ЗАМЕНИТЕ_ПАРОЛЬ_ПАНЕЛИ"
/container/envs/add list=awg-admin key=SESSION_SECRET value=[:rndstr length=64]

/container/add remote-image=ghcr.io/vozdr/awg-admin:latest interface=veth-awg-admin envlists=awg-admin name=awg-admin logging=yes start-on-boot=yes
```

Дождитесь состояния `stopped`, затем запустите контейнер:

```routeros
/container/start [find where name="awg-admin"]
```

Панель слушает TCP-порт `8080` по адресу контейнера. `AWG_CLIENT_ENDPOINT` используется только при генерации клиентских конфигов и не проверяется на доступность.

`SESSION_SECRET` генерируется командой RouterOS один раз и сохраняется в списке переменных окружения. Поэтому активные сессии панели сохраняются после перезапуска контейнера.

Если AWG-контейнер называется не `wg-awg-server-1`, добавьте `AWG_CONTAINER_NAME`. Полный справочник находится в [описании переменных окружения](docs/ENVIRONMENT_RU.md).

Для подключения второго и последующих серверов параметры сервера получают числовой суффикс: например, `MIKROTIK_HOST_2` и `AWG_CLIENT_ENDPOINT_2`. Общая авторизация панели и `SESSION_SECRET` остаются без суффикса. Подробная схема приведена в инструкции [«Несколько серверов»](docs/MULTI_SERVER_RU.md).

## Безопасность

Панель умеет показывать приватные ключи клиентов и изменять конфигурацию MikroTik. Ограничьте API MikroTik адресом veth-контейнера и не публикуйте панель в интернет без HTTPS и дополнительного ограничения доступа.

Исходный код: [GitHub](https://github.com/Vozdr/awg-admin). Образы: [GitHub Container Registry](https://github.com/Vozdr/awg-admin/pkgs/container/awg-admin).
