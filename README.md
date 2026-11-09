# AWG Admin

[Docker Hub](https://hub.docker.com/r/w03zd8rc/awg-admin) · [English](README_EN.md) · [Все переменные окружения](docs/ENVIRONMENT_RU.md) · [Диагностика](docs/TROUBLESHOOTING_RU.md)

AWG Admin — веб-панель управления клиентами AmneziaWG для MikroTik. Она рассчитана на серверный контейнер [`ghcr.io/timbrs/awg-proxy`](https://github.com/timbrs/awg-proxy) и работает с RouterOS напрямую через API или API-SSL.

Панель не использует базу данных и локальные конфиги. Клиенты, комментарии, адреса, ключи и статистика берутся непосредственно из MikroTik. Список публичных ключей синхронизируется с `AWG_CLIENT_PUBS` контейнера proxy.

Возможности:

- создание клиента с первым свободным IPv4-адресом из выбранной подсети интерфейса WireGuard;
- изменение имени, комментария, DNS, AllowedIPs, keepalive и PresharedKey;
- включение, отключение и удаление peer;
- повторный показ приватного конфига, QR-кода и ссылки `vpn://` для Amnezia VPN;
- статистика handshake, endpoint, RX и TX;
- просмотр настроек AWG-сервера;
- русский и английский интерфейс, светлая и тёмная темы;
- образы `linux/amd64`, `linux/arm64` и `linux/arm/v7`.

## Быстрый запуск на MikroTik

Требуются RouterOS v7 с пакетом Container, уже работающий `awg-proxy` в режиме сервера и доступный сервис `api` либо `api-ssl`.

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

/container/config/set registry-url=https://registry-1.docker.io
/container/add remote-image=w03zd8rc/awg-admin:latest interface=veth-awg-admin envlist=awg-admin name=awg-admin logging=yes start-on-boot=yes
```

Дождитесь состояния `stopped`, затем запустите контейнер:

```routeros
/container/start [find where name="awg-admin"]
```

Панель слушает TCP-порт `8080` по адресу контейнера. `AWG_CLIENT_ENDPOINT` используется только при генерации клиентских конфигов и не проверяется на доступность.

`SESSION_SECRET` генерируется командой RouterOS один раз и сохраняется в envlist. Поэтому активные сессии панели сохраняются после перезапуска контейнера.

Если AWG-контейнер называется не `wg-awg-server-1`, добавьте `AWG_CONTAINER_NAME`. Полный справочник находится в [описании переменных окружения](docs/ENVIRONMENT_RU.md).

## Безопасность

Панель умеет показывать приватные ключи клиентов и изменять конфигурацию MikroTik. Ограничьте API MikroTik адресом veth-контейнера и не публикуйте панель в интернет без HTTPS и дополнительного ограничения доступа.

Исходный код: [GitHub](https://github.com/Vozdr/awg-admin). Образы: [Docker Hub](https://hub.docker.com/r/w03zd8rc/awg-admin).
