# Образы AWG Admin

[Главная](../README.md) · [English](GHCR_EN.md) · [Все переменные окружения](ENVIRONMENT_RU.md) · [Диагностика](TROUBLESHOOTING_RU.md)

Публичные образы AWG Admin публикуются в [GitHub Container Registry](https://github.com/Vozdr/awg-admin/pkgs/container/awg-admin):

- `ghcr.io/vozdr/awg-admin:latest` — актуальная сборка ветки `main`;
- `ghcr.io/vozdr/awg-admin:X.Y.Z` — неизменяемая релизная версия.

Для загрузки образа непосредственно в MikroTik укажите GHCR как реестр. Имя реестра в `remote-image` повторно не добавляется:

```routeros
/container/config/set registry-url=https://ghcr.io
/container/add remote-image=vozdr/awg-admin:latest interface=veth-awg-admin envlists=awg-admin name=awg-admin logging=yes start-on-boot=yes
```

Образ публичный, поэтому логин и токен GitHub для его загрузки не требуются.
