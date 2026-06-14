# Basemap Tile Server Proxy and LAN Access

Date: 2026-06-13

This document records the local tile-server integration used by the InSAR Management System v2 Windows launcher.

## Goals

- Start and stop `D:\Code\tile-server` from the main `start_system.bat` / `stop_system.bat` workflow.
- Serve basemap tiles through the main Nginx entry instead of exposing tile-server directly to browsers.
- Keep tile access token configuration in local `.env`; do not commit real tokens.
- Allow optional LAN client IP whitelisting.
- Show a clear restricted-access page for non-whitelisted clients.

## Runtime Topology

```text
LAN browser
  -> http://<server-ip>/
  -> main nginx :80
     -> frontend dist
     -> /api/     -> FastAPI backend on 127.0.0.1:<PORT>
     -> /tiles/   -> tile-server on 127.0.0.1:8910
     -> /geojson/ -> tile-server on 127.0.0.1:8910
```

The tile-server can stay bound to `127.0.0.1:8910`. LAN clients should not call `8910` directly.

## `.env` Settings

Tile-server frontend URL should normally be empty, which means same-origin routing through Nginx:

```env
VITE_TILE_SERVER_URL=
VITE_TILE_SERVER_TOKEN=change_me
TILE_SERVER_AUTO_START=true
TILE_SERVER_AUTO_STOP=true
TILE_SERVER_ROOT=D:\Code\tile-server
TILE_SERVER_START_SCRIPT=start-all.bat
TILE_SERVER_STOP_SCRIPT=stop-all.bat
```

The real `VITE_TILE_SERVER_TOKEN` belongs only in local `.env`.

## LAN IP Whitelist

Use `NGINX_ALLOWED_CLIENT_IPS` to restrict clients:

```env
NGINX_ALLOWED_CLIENT_IPS=192.168.1.10;192.168.1.23
```

Accepted separators are semicolon, comma, or whitespace. CIDR values are also accepted:

```env
NGINX_ALLOWED_CLIENT_IPS=192.168.1.0/28
```

Empty value means no client IP restriction:

```env
NGINX_ALLOWED_CLIENT_IPS=
```

When the whitelist is enabled, the launcher generates `nginx/client_allow.conf` with `allow` rules plus `deny all`.

## Downstream Router Cases

When client devices are behind another router, write the IP that the InSAR server actually sees.

### NAT router mode

If a downstream router has an upstream LAN address such as `192.168.1.63`, and its clients are hidden behind NAT, Nginx will see every downstream client as:

```text
192.168.1.63
```

Whitelist the router IP:

```env
NGINX_ALLOWED_CLIENT_IPS=192.168.1.63
```

This allows all clients behind that NAT router because the server cannot distinguish them by their private downstream IPs.

### AP or bridge mode

If the downstream device works as an AP/bridge, clients usually receive addresses from the main LAN, for example:

```text
192.168.1.80
192.168.1.81
```

Whitelist the actual device IPs:

```env
NGINX_ALLOWED_CLIENT_IPS=192.168.1.80;192.168.1.81
```

### Routed subnet without NAT

If the downstream router forwards another subnet without NAT, the server may see the real downstream subnet addresses, for example:

```text
192.168.63.10
192.168.63.11
```

Whitelist exact client IPs:

```env
NGINX_ALLOWED_CLIENT_IPS=192.168.63.10;192.168.63.11
```

Or whitelist the whole downstream subnet when that is acceptable:

```env
NGINX_ALLOWED_CLIENT_IPS=192.168.63.0/24
```

### How to decide

Open the system from the downstream device before whitelisting it. The restricted-access page displays the detected client IP. Put that displayed IP, or its trusted CIDR range, into `NGINX_ALLOWED_CLIENT_IPS`.

## Restricted Client Page

Nginx maps `403` responses to `nginx/access_denied.html`. The page displays:

- access restricted message
- detected client IP
- requested host
- operator hint to update `NGINX_ALLOWED_CLIENT_IPS`

## Applying Changes

After changing `.env`, run `start_system.bat` or reload Nginx:

```powershell
C:\nginx-1.29.6\nginx.exe -t -c D:\Code\Insar_management_system_v2\nginx\nginx.conf
C:\nginx-1.29.6\nginx.exe -s reload -c D:\Code\Insar_management_system_v2\nginx\nginx.conf
```

From an allowed client:

```text
http://<server-ip>/
http://<server-ip>/tiles/gaode_image/7/107/42.webp?token=<token>
```

From a denied client, Nginx should return the restricted-access page with HTTP 403.
