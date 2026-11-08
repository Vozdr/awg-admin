# AWG Admin environment variables

[Home](../README_EN.md) · [Docker Hub](https://hub.docker.com/r/w03zd8rc/awg-admin) · [Troubleshooting](TROUBLESHOOTING_EN.md)

AWG Admin does not read local configuration files and has no database. All application settings come from environment variables, while operational data comes from MikroTik.

## Required

| Variable | Description | Example |
|---|---|---|
| `MIKROTIK_HOST` | MikroTik IP address or DNS name, without a scheme or port. | `172.17.0.1` |
| `MIKROTIK_USERNAME` | RouterOS API user. | `awg-admin` |
| `MIKROTIK_PASSWORD` | RouterOS API user password. | `strong-password` |
| `AWG_CLIENT_ENDPOINT` | Endpoint written to client configurations. It is not probed. | `vpn.example.org:51820` |

## MikroTik connection

| Variable | Default | Description |
|---|---:|---|
| `MIKROTIK_API_SSL` | `no` | `yes` selects API-SSL; `no` selects the plain API. |
| `MIKROTIK_API_PORT` | `8729` with SSL, otherwise `8728` | Custom API port. |
| `MIKROTIK_SSL_VERIFY` | `yes` | Verify the API-SSL certificate. Use `no` for a self-signed certificate when required. |
| `MIKROTIK_CONNECT_TIMEOUT` | `10` | Connection timeout in seconds. |
| `MIKROTIK_COMMAND_TIMEOUT` | `20` | RouterOS command timeout in seconds. |

Plain API is reasonable when the panel runs on the same MikroTik in an isolated container network. API-SSL is recommended for a remote connection.

## AWG server and new clients

| Variable | Default | Description |
|---|---:|---|
| `AWG_CONTAINER_NAME` | `wg-awg-server-1` | The MikroTik AWG proxy container's `name` or `comment`. |
| `AWG_SERVER_NAME` | `AWG Server` | Profile name in the Amnezia VPN link. |
| `CLIENT_DEFAULT_KEEPALIVE` | `30` | New client keepalive in seconds. `0` disables it. |
| `CLIENT_DEFAULT_DNS` | empty | New client DNS, for example `1.1.1.1,8.8.8.8`. |
| `CLIENT_DEFAULT_ALLOWED_IPS` | `0.0.0.0/0,::/0` | New client AllowedIPs. |

Address pools are not configured through env. The panel reads every active IPv4 address assigned to the detected WireGuard interface, derives its subnets, and lets the user choose one when creating a client.

## Panel authentication

| Variable | Default | Description |
|---|---|---|
| `ADMIN_USERNAME` | `admin` | Panel username. It can be set without `ADMIN_PASSWORD`. |
| `ADMIN_PASSWORD` | `admin` | Panel password. It can be set without `ADMIN_USERNAME`. |
| `AUTH_DISABLE` | `no` | `yes` disables authentication entirely. The panel logs an ERROR warning but remains operational. |
| `SESSION_SECRET` | random on each start | Session and CSRF signing key. Set a long random string to preserve sessions across restarts. |
| `COOKIE_SECURE` | `no` | `yes` sends cookies over HTTPS only. Do not enable for direct HTTP access. |

When neither credential is set, `admin/admin` is used and an ERROR-level warning is logged.

## UI and logging

| Variable | Default | Description |
|---|---|---|
| `DEFAULT_LANG` | `ru` | Initial locale: `ru` or `en`. The user's selection is stored in the browser. |
| `DEFAULT_THEME` | `dark` | Initial theme: `dark` or `light`. |
| `LOG_LEVEL` | `INFO` | Python log level: `DEBUG`, `INFO`, `WARNING`, or `ERROR`. |
| `LOCALES_DIR` | `/app/locales` | Internal locale path. It normally does not need to change. |
| `AWG_ADMIN_VERSION` | `dev` | Build version. The published image sets it automatically. |

Boolean values accept `yes/no`, `true/false`, `on/off`, and `1/0`, case-insensitively.
