# AWG Admin troubleshooting

[Home](../README_EN.md) · [Environment reference](ENVIRONMENT_EN.md) · [Docker Hub](https://hub.docker.com/r/w03zd8rc/awg-admin)

## The panel container stops immediately

At startup, AWG Admin deliberately validates its prerequisites. A fatal error is logged and the process exits so an unusable container is not left running.

Inspect the logs:

```routeros
/log/print where message~"AWG"
/container/print detail where name="awg-admin"
```

Validation covers required env, API connectivity and credentials, AWG container presence, its assigned `envlists` and server mode, matching `AWG_SERVER_PUB` to a WireGuard interface, interface addresses, and the port. Variables are merged from every list assigned through `envlists`.

A stopped AWG container does not prevent the panel from starting: an ERROR-level warning is logged and the panel remains available. When `AWG_CLIENT_PUBS` changes, a currently running container is restarted. A container that is stopped at that moment remains stopped.

Insufficient API permissions are the exception: the panel remains available and shows a clear GUI error so the RouterOS group can be corrected.

## API connection fails

Check the service and address restriction:

```routeros
/ip/service/print where name="api" or name="api-ssl"
/user/print detail where name="awg-admin"
```

Plain API uses `MIKROTIK_API_SSL=no` and port `8728`; API-SSL uses `yes` and port `8729`. The user needs `read,write,sensitive,api`. The `sensitive` policy is required to retrieve a private client configuration again.

## AWG container not found

By default, AWG Admin looks for a container whose `name` or `comment` is `wg-awg-server-1`:

```routeros
/container/print detail
```

If the name differs, add `AWG_CONTAINER_NAME` to the AWG Admin environment list and restart the panel.

## WireGuard interface not found

The panel reads `AWG_SERVER_PUB` from the proxy container's assigned `envlists` and requires exactly one WireGuard interface with that `public-key`:

```routeros
/container/envs/print detail
/interface/wireguard/print detail
```

Correct the key or relationship. The panel intentionally does not select an interface by name alone, avoiding accidental changes to another server.

## Config or QR cannot be displayed

AWG Admin invokes `interface/wireguard/peers/show-client-config`. Verify that:

- the RouterOS version supports this command;
- the API user has `read` and `sensitive`;
- MikroTik stores a private key for the peer;
- the returned config contains the client `PrivateKey` and server `PublicKey`.

## Key synchronization warning

It appears when peers were changed manually or the AWG container was edited separately. “Synchronize” writes the current public keys of every peer on the detected interface to `AWG_CLIENT_PUBS`. A running AWG proxy container is restarted; a stopped one remains stopped.

## Error technical details

Every known error has a clear message and an expandable block with the RouterOS response and request ID. An unknown runtime error also does not terminate the container. Recognizable secrets are redacted before technical text is sent to the browser.
