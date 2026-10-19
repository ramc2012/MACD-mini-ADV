# Broker access on an IPv6-only host

On 19 October 2026 the Mac had working IPv6 upstream access but no IPv4
default route. Fyers login reached an IPv6 address that the application's
IPv4-only Docker bridge could not route. Saving an auth code consequently
failed during the token exchange with `Network is unreachable`.

The machine-local `compose.override.yaml` keeps the original app network
and attaches the strategy engine to a second, external IPv6-enabled bridge.
Only the engine calls Fyers. No host routing or Docker Desktop settings need
to change.

Create the network once (also needed after resetting Docker's networks):

```sh
docker network create --ipv6 macd-mini-parallel_broker_ipv6
```

Merge this fragment into the local Compose override, preserving any local
terminal port bindings:

```yaml
services:
  engine:
    networks:
      - default
      - broker_ipv6
networks:
  broker_ipv6:
    external: true
    name: macd-mini-parallel_broker_ipv6
```

To restore broker connectivity on an already running engine without a restart:

```sh
docker network connect macd-mini-parallel_broker_ipv6 macd-mini-parallel-engine-1
```

An already attached network does not need attaching again. The Compose
override preserves the attachment when the engine is recreated. It is
intentionally ignored by Git because other machines may not need this network
and may have different Tailscale addresses.

After restoring connectivity, complete login in **Settings → Broker
connection**. Check that `/api/system/health` reports `status=connected`,
`token_expired=false`, `feed_alive=true`, zero history errors and completed
indicator warm-up. A connected socket before the market opens does not prove
fresh regular-session quotes or permission to send live orders.
