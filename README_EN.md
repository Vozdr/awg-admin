# AWG Admin

[Docker Hub](https://hub.docker.com/r/w03zd8rc/awg-admin) · [Environment reference](docs/ENVIRONMENT_EN.md) · [Troubleshooting](docs/TROUBLESHOOTING_EN.md)

AWG Admin is a web panel for managing AmneziaWG clients on MikroTik. It is designed for the [`amneziawg-mikrotik-c` by timbrs](https://github.com/timbrs/amneziawg-mikrotik-c) server container, image `ghcr.io/timbrs/awg-proxy`, and talks directly to RouterOS through API or API-SSL.

The panel has no database or local configuration files. Clients, comments, addresses, keys, and statistics are read directly from MikroTik. Public peer keys are synchronized with the proxy container's `AWG_CLIENT_PUBS` variable.

Features:

- allocate the first free IPv4 address from a selected WireGuard interface subnet;
- add, enable, disable, and delete peers;
- generate a QR code, `.conf` file, and configuration link for a peer;
- show statistics for each peer;
- inspect AWG server settings;
- interface localization.

## Quick start on MikroTik

RouterOS v7 with the Container package, a configured server-mode `awg-proxy`, and the `api` or `api-ssl` service are required. The AWG container may be stopped without preventing the panel from running.

Create a dedicated group and user. AWG Admin needs only `read`, `write`, `sensitive`, and `api`:

```routeros
/user/group/add name=awg-admin policy=read,write,sensitive,api
/user/add name=awg-admin group=awg-admin password="CHANGE_ME" address=172.17.0.2/32
```

In this example, MikroTik uses `172.17.0.1` and the container uses `172.17.0.2`:

```routeros
/interface/veth/add name=veth-awg-admin address=172.17.0.2/24 gateway=172.17.0.1
/ip/address/add address=172.17.0.1/24 interface=veth-awg-admin

/container/envs/add list=awg-admin key=MIKROTIK_HOST value=172.17.0.1
/container/envs/add list=awg-admin key=MIKROTIK_USERNAME value=awg-admin
/container/envs/add list=awg-admin key=MIKROTIK_PASSWORD value="CHANGE_ME"
/container/envs/add list=awg-admin key=AWG_CLIENT_ENDPOINT value=vpn.example.org:51820
/container/envs/add list=awg-admin key=ADMIN_USERNAME value=admin
/container/envs/add list=awg-admin key=ADMIN_PASSWORD value="CHANGE_PANEL_PASSWORD"
/container/envs/add list=awg-admin key=SESSION_SECRET value=[:rndstr length=64]

/container/config/set registry-url=https://registry-1.docker.io
/container/add remote-image=w03zd8rc/awg-admin:latest interface=veth-awg-admin envlists=awg-admin name=awg-admin logging=yes start-on-boot=yes
```

Wait until the container is `stopped`, then start it:

```routeros
/container/start [find where name="awg-admin"]
```

The panel listens on TCP port `8080` at the container address. `AWG_CLIENT_ENDPOINT` is used only to generate client configurations and is never probed.

RouterOS generates `SESSION_SECRET` once and stores it in the environment list, so active panel sessions survive container restarts.

Set `AWG_CONTAINER_NAME` when the proxy container is not named `wg-awg-server-1`. See the [environment reference](docs/ENVIRONMENT_EN.md) for every option.

## Security

The panel can reveal client private keys and modify MikroTik configuration. Restrict the MikroTik API service to the panel's veth address and do not expose the panel to the internet without HTTPS and an additional access restriction.

Source: [GitHub](https://github.com/Vozdr/awg-admin). Images: [Docker Hub](https://hub.docker.com/r/w03zd8rc/awg-admin).
