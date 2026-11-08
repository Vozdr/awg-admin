# AWG Admin

[Docker Hub](https://hub.docker.com/r/w03zd8rc/awg-admin) · [Environment reference](docs/ENVIRONMENT_EN.md) · [Troubleshooting](docs/TROUBLESHOOTING_EN.md)

AWG Admin is a web panel for managing AmneziaWG clients on MikroTik. It is designed for a server-mode [`ghcr.io/timbrs/awg-proxy`](https://github.com/timbrs/awg-proxy) container and talks directly to RouterOS through API or API-SSL.

The panel has no database or local configuration files. Clients, comments, addresses, keys, and statistics are read directly from MikroTik. Public peer keys are synchronized with the proxy container's `AWG_CLIENT_PUBS` variable.

Features:

- allocate the first free IPv4 address from a selected WireGuard interface subnet;
- edit the name, comment, DNS, AllowedIPs, keepalive, and PresharedKey;
- enable, disable, and delete peers;
- display private configs, QR codes, and `vpn://` links again;
- show handshake, endpoint, RX, and TX statistics;
- inspect AWG server settings;
- English and Russian UI with dark and light themes;
- `linux/amd64`, `linux/arm64`, and `linux/arm/v7` images.

## Quick start on MikroTik

RouterOS v7 with the Container package, a running server-mode `awg-proxy`, and the `api` or `api-ssl` service are required.

Create a dedicated group and user. AWG Admin needs only `read`, `write`, `sensitive`, and `api`:

```routeros
/user/group/add name=awg-admin policy=read,write,sensitive,api
/user/add name=awg-admin group=awg-admin password="CHANGE_ME" address=172.17.0.3/32
```

The example assumes an existing container bridge, veth address `172.17.0.3`, and a disk named `disk1`:

```routeros
/interface/veth/add name=veth-awg-admin address=172.17.0.3/24 gateway=172.17.0.1
/interface/bridge/port/add bridge=containers interface=veth-awg-admin

/container/envs/add list=awg-admin key=MIKROTIK_HOST value=172.17.0.1
/container/envs/add list=awg-admin key=MIKROTIK_USERNAME value=awg-admin
/container/envs/add list=awg-admin key=MIKROTIK_PASSWORD value="CHANGE_ME"
/container/envs/add list=awg-admin key=AWG_CLIENT_ENDPOINT value=vpn.example.org:51820
/container/envs/add list=awg-admin key=ADMIN_USERNAME value=admin
/container/envs/add list=awg-admin key=ADMIN_PASSWORD value="CHANGE_PANEL_PASSWORD"
/container/envs/add list=awg-admin key=SESSION_SECRET value="A_LONG_RANDOM_STRING"

/container/config/set registry-url=https://registry-1.docker.io tmpdir=disk1/tmp
/container/add remote-image=w03zd8rc/awg-admin:latest interface=veth-awg-admin root-dir=disk1/awg-admin envlist=awg-admin name=awg-admin logging=yes start-on-boot=yes
```

Wait until the container is `stopped`, then start it:

```routeros
/container/start [find where name="awg-admin"]
```

The panel listens on TCP port `8080` at the container address. Port publishing, firewall policy, and the access path are intentionally left to the user. `AWG_CLIENT_ENDPOINT` is used only to generate client configurations and is never probed.

Set `AWG_CONTAINER_NAME` when the proxy container is not named `wg-awg-server-1`. See the [environment reference](docs/ENVIRONMENT_EN.md) for every option.

## Security

The panel can reveal client private keys and modify MikroTik configuration. Restrict the MikroTik API service to the panel's veth address and do not expose the panel to the internet without HTTPS and an additional access restriction.

Source: [GitHub](https://github.com/Vozdr/awg-admin). Images: [Docker Hub](https://hub.docker.com/r/w03zd8rc/awg-admin).
