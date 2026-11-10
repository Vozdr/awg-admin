# AWG Admin images

[Home](../README_EN.md) · [Environment reference](ENVIRONMENT_EN.md) · [Troubleshooting](TROUBLESHOOTING_EN.md)

Public AWG Admin images are published in the [GitHub Container Registry](https://github.com/Vozdr/awg-admin/pkgs/container/awg-admin):

- `ghcr.io/vozdr/awg-admin:latest` — the current build of the `main` branch;
- `ghcr.io/vozdr/awg-admin:X.Y.Z` — an immutable release version.

To pull the image directly on MikroTik, configure GHCR as the registry. Do not repeat the registry host in `remote-image`:

```routeros
/container/config/set registry-url=https://ghcr.io
/container/add remote-image=vozdr/awg-admin:latest interface=veth-awg-admin envlists=awg-admin name=awg-admin logging=yes start-on-boot=yes
```

The image is public, so a GitHub username and token are not required to pull it.
