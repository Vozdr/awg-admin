# AWGM Admin troubleshooting

[Home](../README_EN.md) · [Русский](TROUBLESHOOTING_RU.md) · [Environment reference](ENVIRONMENT_EN.md)

In the interface, the error code is available in the expandable “Technical details” section. A startup failure is logged as a single line prefixed with `AWG_ADMIN_STARTUP_ERROR`. View the log with:

```routeros
/log/print where message~"AWG"
```

With multiple servers, the code applies to the selected server. A failure of one fully configured additional server does not stop the panel while at least one other server is available. If no server works, the panel stops; `MIKROTIK_PERMISSION_DENIED` is the exception, leaving the diagnostic interface available.

## Environment errors before startup

These errors do not have individual codes: the container exits and the log line starts with `AWG_ADMIN_STARTUP_ERROR`.

| Message or fragment | Cause and solution |
|---|---|
| `Missing required environment variables: ...` | The listed required variables are empty. The first server requires `MIKROTIK_HOST`, `MIKROTIK_USERNAME`, `MIKROTIK_PASSWORD`, `AWG_CLIENT_ENDPOINT` and, when authentication is enabled, `SESSION_SECRET`. For a declared additional server, check the four variables carrying its suffix, such as `_2`. |
| `... must be yes/no, true/false, on/off or 1/0` | A Boolean variable contains an unsupported value. Use one of the listed value pairs. |
| `... must be an integer` | A numeric variable is not an integer. Correct the variable named in the message. |
| `... must be between ... and ...` | A number or port is outside its allowed range. Use a value within the range shown in the message. |
| `AWG_CLIENT_ENDPOINT... must use host:port format` | The endpoint has no port. Use `host:port`, for example `vpn.example.org:51820`. |
| `AWG_CLIENT_ENDPOINT... host is empty` | The server address before the colon is missing. Enter an IP address or DNS name. |
| `AWG_CLIENT_ENDPOINT... port must be an integer` | The value after the colon is not a numeric port. |
| `AWG_CLIENT_ENDPOINT... port must be between 1 and 65535` | Enter a valid port number. |
| `DEFAULT_THEME must be dark or light` | Set `DEFAULT_THEME=dark` or `DEFAULT_THEME=light`. |
| `DEFAULT_LANG contains unsupported characters` | A locale code may contain only letters, digits, `_`, and `-`. Use `ru` or `en` for the built-in locales. |
| `UNKNOWN: ...` | An unexpected application or locale loading error occurred. Save the complete log line, verify image integrity, and open a [GitHub issue](https://github.com/Vozdr/awgm-admin/issues). |

See the [environment reference](ENVIRONMENT_EN.md) for every variable and range.

## MikroTik connection

| Code | Meaning | Resolution |
|---|---|---|
| `MIKROTIK_UNREACHABLE` | A TCP or TLS connection to the API could not be established. | Check `MIKROTIK_HOST`, `MIKROTIK_API_SSL`, `MIKROTIK_API_PORT`, the `api`/`api-ssl` service, and its address restriction under `/ip/service`. For a self-signed certificate, either install a trusted certificate or set `MIKROTIK_SSL_VERIFY=no`. |
| `MIKROTIK_AUTH_FAILED` | MikroTik rejected the API username or password. | Check `MIKROTIK_USERNAME` and `MIKROTIK_PASSWORD`, and verify that the RouterOS user exists and is enabled. |
| `MIKROTIK_PERMISSION_DENIED` | The API account lacks permission for a command. | Assign `read,write,sensitive,api` policies to its group. `sensitive` is required to retrieve private client configuration. Retry the operation after changing permissions; the panel need not be restarted. |
| `MIKROTIK_CONNECTION_LOST` | The connection closed during a command, timed out, or returned an invalid API response. | Check connectivity and the RouterOS log. On a slow device, increase `MIKROTIK_COMMAND_TIMEOUT`, then retry. |
| `MIKROTIK_RESOURCE_NOT_FOUND` | MikroTik reports that the requested object no longer exists. | Refresh the panel. If the object was removed manually, repeat the action using current data. |
| `MIKROTIK_API_ERROR` | RouterOS rejected the command for a reason other than permissions or a missing object. | Expand Technical details to see the RouterOS response. Correct that condition and retry. |

Check the API on MikroTik with:

```routeros
/ip/service/print where name="api" or name="api-ssl"
/user/print detail where name="awgm-admin"
```

## AWG container and WireGuard

| Code | Meaning | Resolution |
|---|---|---|
| `AWG_CONTAINER_NOT_FOUND` | No container has a `name` or `comment` matching `AWG_CONTAINER_NAME`, or the container disappeared during an operation. | Run `/container/print detail`, correct `AWG_CONTAINER_NAME` in the AWGM Admin environment, or restore the container. Restart the panel if this occurred during startup. |
| `AWG_ENVLISTS_MISSING` | The AWG container has no list assigned through `envlists`. | Assign one or more environment lists to the container. AWGM Admin merges variables from every assigned list. |
| `AWG_ENV_INVALID` | A required key is absent from the merged `envlists`, or a managed record disappeared during an operation. | Identify the key in Technical details. Startup requires `AWG_MODE`, `AWG_LISTEN`, `AWG_REMOTE`, `AWG_JC`, `AWG_JMIN`, `AWG_JMAX`, `AWG_S1`, `AWG_S2`, `AWG_H1`–`AWG_H4`, `AWG_SERVER_PUB`, and `AWG_CLIENT_PUBS`. Do not remove the `AWG_CLIENT_PUBS` record: the panel updates it when peers change. |
| `AWG_MODE_INVALID` | `AWG_MODE` is not `server`. | Set `AWG_MODE=server` in one of the lists assigned to the AWG container. |
| `WG_INTERFACE_NOT_FOUND` | Exactly one WireGuard interface whose `public-key` matches `AWG_SERVER_PUB` could not be found. | Compare `/interface/wireguard/print detail` with `/container/envs/print detail`. Correct the key; if multiple interfaces match, keep it only on the intended interface. |
| `WG_INTERFACE_DISABLED` | The matched WireGuard interface is disabled. | Enable the interface on MikroTik. |
| `WG_ADDRESSES_MISSING` | The matched WireGuard interface has no active IPv4 address. | Add and enable an address with a subnet mask under `/ip/address`. The panel allocates client addresses from this subnet. |
| `AWG_WG_PORT_MISMATCH` | The port in `AWG_REMOTE` differs from the WireGuard interface `listen-port`. | Use the same internal port in both settings. The external client endpoint may still use another port. |
| `PROXY_RESTART_FAILED` | After `AWG_CLIENT_PUBS` changed, a running AWG container did not stop or start within the allowed time. | Inspect `/container/print detail` and the AWG container log. Correct the failure and start it manually, then check key synchronization in the panel. |

A stopped AWG container alone is not a fatal error. Its behavior is described in the log-message section below.

## Editing server settings

| Code | Meaning | Resolution |
|---|---|---|
| `ENVLIST_NOT_ASSIGNED` | The entered `envlist` name already exists under `/container/envs` but is not assigned to the current AWG container. | Select an envlist assigned to this container or enter a unique name for a new envlist. |
| `ENV_VARIABLE_MANAGED` | A request attempted to edit, disable, or delete a panel-managed variable. | Refresh the settings. `AWG_CLIENT_PUB`, `AWG_CLIENT_PUBS`, `AWG_REMOTE`, `AWG_SERVER_PUB`, and `AWG_HEADER_PROTECTION_KEY` change only through their related panel operations. |
| `ENV_VARIABLE_NOT_FOUND` | The variable was removed after the settings window was opened. | Close and reopen the settings. |
| `SERVER_SETTINGS_CONFLICT` | Environment records, addresses, the port, or the WireGuard key changed in MikroTik after the window was opened. | Close and reopen the window, then apply the intended edits to the current data. |
| `SERVER_SETTINGS_ROLLED_BACK` | The AWG container could not start with the proposed settings. The panel restored the previous values and successfully returned the container to its original running state. | Expand Technical details: the panel retrieves the latest failed-start reason through `/container/log/print`, using the container name. Correct the parameters and save again. |
| `SERVER_ADDRESS_INVALID` | An interface address is not a valid IPv4 address with a CIDR prefix. | Correct it, for example to `192.168.177.1/24`. |
| `SERVER_ADDRESS_NOT_FOUND` | An address was removed from MikroTik after the window was opened. | Close and reopen the settings. |
| `SERVER_ADDRESS_DUPLICATE` | The same address was entered more than once. | Remove the duplicate field. |
| `SERVER_ADDRESSES_EMPTY` | The staged changes would remove every address from the WireGuard interface. | Retain or add at least one address with a prefix. |
| `AWG_REMOTE_INVALID` | The current `AWG_REMOTE` value has no recognizable address and port. | Correct `AWG_REMOTE` in MikroTik, then retry the internal-port change. |

## Client operations

| Code | Meaning | Resolution |
|---|---|---|
| `CLIENT_KEY_INVALID` | PublicKey, PrivateKey, or PresharedKey is not a valid 32-byte WireGuard key; or key storage/use is enabled while the field is empty. | Paste the complete Base64 key without extra characters, generate a new pair, or clear the corresponding key storage/use checkbox. |
| `CLIENT_KEY_PAIR_MISMATCH` | The entered PublicKey does not correspond to the PrivateKey. | Correct either key or select “Generate new pair.” |
| `ADDRESS_POOL_INVALID` | The selected subnet is no longer assigned to the WireGuard interface. | Close the dialog, refresh data, and select an existing pool. Check active interface addresses under `/ip/address`. |
| `ADDRESS_POOL_EXHAUSTED` | The selected subnet has no free addresses. | Select another pool, release an address, or add another subnet to the interface. |
| `ADDRESS_INVALID` | A manual address has invalid syntax, lies outside the selected IPv4 subnet, or is the network/broadcast address. | Enter a free address from the selected subnet or use automatic allocation. |
| `ADDRESS_IN_USE` | The manual address is already used by the interface or another peer. | Select another address or automatic allocation. |
| `CLIENT_NAME_EXISTS` | A peer with the same name already exists on the interface. | Enter another name or leave it empty and let RouterOS assign one. |
| `CLIENT_CREATE_FAILED` | After the add command, RouterOS did not return the created peer or its public key. | Refresh the list and inspect peers on MikroTik. If the peer is absent, retry; if the problem repeats, save Technical details and the RouterOS version. |
| `CLIENT_NOT_FOUND` | The peer was deleted or changed after the list was loaded. | Refresh the client list and repeat the action on an existing peer. |
| `CLIENT_UPDATE_FAILED` | The peer has no public key after an update. | Inspect the peer on MikroTik, correct its keys, and retry. |
| `CLIENT_CONFIG_UNAVAILABLE` | `show-client-config` returned no configuration from which PrivateKey could be read. | Check command support in the installed RouterOS version and the `sensitive` policy. If the peer does not store its private key, the complete configuration cannot be recovered; use the client's original PrivateKey. |
| `CLIENT_CONFIG_INVALID` | The WireGuard interface public key was unavailable while building client configuration. | Check the matched interface key and `AWG_SERVER_PUB`; restore the server key pair if necessary. |

## General panel errors

| Code | Meaning | Resolution |
|---|---|---|
| `SERVER_NOT_FOUND` | The browser requested a server no longer present in the panel configuration. | Refresh the page. If its environment was removed, select another server; if it should exist, restore its variables and restart AWGM Admin. |
| `VALIDATION_ERROR` | One or more form fields failed type, length, or range validation. | Review the form fields. Technical details list the affected fields and constraints. |
| `UNKNOWN_ERROR` | An unexpected internal error occurred. | Retry once. If it persists, save the Request ID, Technical details, and log, then open a [GitHub issue](https://github.com/Vozdr/awgm-admin/issues). |
| `NETWORK_ERROR` | The browser could not reach the panel; this code is generated by the UI, not the server. | Verify that the AWGM Admin container is running and the panel address is reachable from the browser. |
| `HTTP_400`, `HTTP_403`, and other `HTTP_<number>` | The server returned no standard error object. This commonly means an expired session, invalid CSRF token, or unconfirmed action. | Refresh the page and sign in again. If it repeats, expand Technical details and inspect the log. |

An invalid username or password is shown on the login page without a code. After repeated failed attempts, login from that address is temporarily rate-limited; wait for the limit to expire and retry with the correct credentials.

## Log messages that do not stop the panel

| Message | Meaning |
|---|---|
| `AWG_CONTAINER_STOPPED` | The AWG container is stopped. The panel remains available. If it is stopped when `AWG_CLIENT_PUBS` changes, the variable is updated but the panel does not start or restart the container. |
| `AUTH_DISABLED` | Authentication is disabled by `AUTH_DISABLE=yes`. This is a security warning, not a startup failure. |
| `DEFAULT_CREDENTIALS` | The default `admin/admin` credentials are in use. The panel works, but the username and password should be changed. |

## What to include in a bug report

- the error code and Request ID, when shown;
- the contents of Technical details;
- the AWGM Admin version shown at the bottom of the page;
- the RouterOS version;
- relevant log lines.

Do not publish passwords, private keys, or `SESSION_SECRET`. AWGM Admin redacts recognized secrets from Technical details, but review logs before sharing them.
