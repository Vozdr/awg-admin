# Multiple AWG servers

[Home](../README_EN.md) · [Русский](MULTI_SERVER_RU.md) · [Environment reference](ENVIRONMENT_EN.md) · [Troubleshooting](TROUBLESHOOTING_EN.md)

One AWG Admin container can manage multiple AWG servers on the same or different MikroTik routers. Each server has an independent API connection, AWG container, client endpoint, client defaults, and error state. The server selected in the header is stored in the browser.

## Naming rule

The first server uses regular, unsuffixed variables. The second uses `_2`, the third `_3`, and so on. Gaps are allowed; for example, the unsuffixed server plus `_2` and `_5` is valid.

`MIKROTIK_HOST_N` declares an additional server. That server requires:

- `MIKROTIK_HOST_N`;
- `MIKROTIK_USERNAME_N`;
- `MIKROTIK_PASSWORD_N`;
- `AWG_CLIENT_ENDPOINT_N`.

This is a structural env validation performed before connecting to MikroTik. If any declared server has one of the four required variables missing or empty, the entire panel configuration is considered incomplete: AWG Admin does not start and reports the variable by name. Such a server cannot remain in the selector with a warning because there is not enough information to create its connection.

## Second server example

```routeros
/container/envs/add list=awg-admin key=MIKROTIK_HOST_2 value=192.168.50.1
/container/envs/add list=awg-admin key=MIKROTIK_USERNAME_2 value=awg-admin
/container/envs/add list=awg-admin key=MIKROTIK_PASSWORD_2 value="CHANGE_ME"
/container/envs/add list=awg-admin key=AWG_CLIENT_ENDPOINT_2 value=branch.example.org:51820
/container/envs/add list=awg-admin key=AWG_CONTAINER_NAME_2 value=wg-awg-branch
/container/envs/add list=awg-admin key=AWG_SERVER_NAME_2 value="Branch office"
/container/envs/add list=awg-admin key=MIKROTIK_API_SSL_2 value=yes
/container/envs/add list=awg-admin key=MIKROTIK_SSL_VERIFY_2 value=no
/container/envs/add list=awg-admin key=CLIENT_DEFAULT_DNS_2 value=1.1.1.1,8.8.8.8
```

Every optional server setting supports the same suffix. When omitted, defaults from the [environment reference](ENVIRONMENT_EN.md) apply independently to that server.

## Shared panel settings

Authentication and UI belong to the panel itself, so these variables are never duplicated per server: `ADMIN_USERNAME`, `ADMIN_PASSWORD`, `AUTH_DISABLE`, `COOKIE_SECURE`, `SESSION_SECRET`, `DEFAULT_LANG`, `DEFAULT_THEME`, `LOG_LEVEL`, and `LOCALES_DIR`.

## Per-server failures

After the required env values pass validation, the panel connects to every described server and checks its MikroTik resources. An unreachable MikroTik router, an API error, or a problem with one server's AWG container does not block healthy servers: the failed server remains in the selector with a warning and the resulting error is shown when selected.

AWG Admin does not start if any declared server has incomplete required env values, or if no usable server remains after the runtime checks. Insufficient API permissions are the exception: as before, this error keeps the diagnostic UI available.
