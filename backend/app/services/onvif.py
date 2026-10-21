"""Minimal ONVIF camera discovery (WS-Discovery over UDP, no extra deps).

Full ONVIF media-profile negotiation requires a SOAP client (`onvif-zeep`).
To stay dependency-light, this module implements the standard multicast
WS-Discovery probe so operators can find device endpoint URLs, then paste the
RTSP URL (or use their camera's ONVIF profile tooling) into the camera form.
When ``onvif-zeep`` *is* installed, ``probe_onvif_media`` additionally resolves
an RTSP URL using the supplied credentials.
"""

from __future__ import annotations

import logging
import re
import socket
import uuid
from typing import Any

log = logging.getLogger(__name__)

WSD_PROBE = """<?xml version="1.0" encoding="UTF-8"?>
<e:Envelope xmlns:e="http://www.w3.org/2003/05/soap-envelope"
            xmlns:w="http://schemas.xmlsoap.org/ws/2004/08/addressing"
            xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery"
            xmlns:dn="http://www.onvif.org/ver10/network/wsdl">
  <e:Header>
    <w:MessageID>uuid:{message_id}</w:MessageID>
    <w:To e:mustUnderstand="true">urn:schemas-xmlsoap-org:ws:2005:04:discovery</w:To>
    <w:Action e:mustUnderstand="true">http://schemas.xmlsoap.org/ws/2005/04/discovery/Probe</w:Action>
  </e:Header>
  <e:Body>
    <d:Probe>
      <d:Types>dn:NetworkVideoTransmitter</d:Types>
    </d:Probe>
  </e:Body>
</e:Envelope>"""


def discover(timeout_s: float = 3.0) -> list[dict[str, Any]]:
    """Send a WS-Discovery probe and collect responding ONVIF devices."""
    message_id = str(uuid.uuid4())
    payload = WSD_PROBE.format(message_id=message_id).encode("utf-8")
    devices: dict[str, dict[str, Any]] = {}
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
    sock.settimeout(timeout_s)
    try:
        sock.sendto(payload, ("239.255.255.250", 3702))
        while True:
            try:
                data, addr = sock.recvfrom(65535)
            except socket.timeout:
                break
            text = data.decode("utf-8", errors="ignore")
            xaddrs = re.search(r"<[\w:]*XAddrs>(.*?)</[\w:]*XAddrs>", text, re.S)
            scopes = re.search(r"<[\w:]*Scopes>(.*?)</[\w:]*Scopes>", text, re.S)
            name = None
            if scopes:
                match = re.search(r"(?:name|hardware)/[^/\s]+", scopes.group(1))
                if match:
                    name = match.group(0).split("/", 1)[1]
            endpoint = (xaddrs.group(1).strip() if xaddrs else "").split(" ")[0]
            if not endpoint:
                continue
            devices[endpoint] = {
                "endpoint": endpoint,
                "ip": addr[0],
                "name": name or addr[0],
                "scopes": scopes.group(1).strip() if scopes else "",
                "credentials_required": True,
            }
    finally:
        sock.close()
    return list(devices.values())


def probe_onvif_media(host: str, username: str, password: str, port: int = 80) -> dict[str, Any]:
    """Resolve an RTSP URI from an ONVIF device (requires ``onvif-zeep``)."""
    try:
        from onvif import ONVIFCamera  # type: ignore
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "error": "ONVIF profile resolution requires the optional 'onvif-zeep' package",
            "detail": str(exc)[:200],
        }
    try:
        cam = ONVIFCamera(host, port, username, password)
        media = cam.create_media_service()
        profiles = media.GetProfiles()
        if not profiles:
            return {"ok": False, "error": "No ONVIF media profiles found"}
        uri = media.GetStreamUri(
            {"StreamSetup": {"Stream": "RTP-Unicast", "Transport": {"Protocol": "RTSP"}},
             "ProfileToken": profiles[0].token}
        )
        return {"ok": True, "rtsp_url": uri.Uri, "profile": getattr(profiles[0], "Name", "")}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)[:300]}
