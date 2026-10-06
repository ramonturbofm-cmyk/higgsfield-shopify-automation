"""A fake HomeWizard P1 Meter (API v2) following the official documentation:
HTTPS with a CA-signed certificate whose CN is appliance/p1dongle/<serial>,
POST /api/user button-press pairing, Bearer auth, /api, /api/measurement,
/api/system/identify, and the /api/ws WebSocket protocol."""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import ssl
from http import HTTPStatus

import websockets
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from websockets.datastructures import Headers
from websockets.http11 import Response


def make_ca_and_cert(tmp_path, serial: str = "5c2fafaabbcc", cn: str | None = None):
    now = dt.datetime.now(dt.UTC)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Test HomeWizard"),
                         x509.NameAttribute(NameOID.COMMON_NAME, "Test Appliance Access CA")])
    ca = (x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name).public_key(ca_key.public_key())
          .serial_number(1).not_valid_before(now - dt.timedelta(days=1)).not_valid_after(now + dt.timedelta(days=30))
          .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
          .sign(ca_key, hashes.SHA256()))
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn or f"appliance/p1dongle/{serial}")])
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(ca_name).public_key(key.public_key())
            .serial_number(2).not_valid_before(now - dt.timedelta(days=1)).not_valid_after(now + dt.timedelta(days=30))
            .sign(ca_key, hashes.SHA256()))
    ca_path, cert_path, key_path = tmp_path / "ca.pem", tmp_path / "cert.pem", tmp_path / "key.pem"
    ca_path.write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                           serialization.NoEncryption()))
    return ca_path, cert_path, key_path


MEASUREMENT = {
    "protocol_version": 50, "meter_model": "ISKRA  2M550T-101", "timestamp": "2024-06-28T14:12:34",
    "tariff": 2, "energy_import_kwh": 13779.338, "energy_import_t1_kwh": 10830.511,
    "energy_import_t2_kwh": 2948.827, "energy_export_kwh": 0, "power_w": -543, "power_l1_w": -676,
    "power_l2_w": 133, "power_l3_w": 0, "current_a": 6, "current_l1_a": -4, "current_l2_a": 2,
    "current_l3_a": 0, "voltage_l1_v": 230.1, "voltage_l2_v": 231.0, "voltage_l3_v": 229.8,
}


class FakeP1:
    def __init__(self, cert_path, key_path, serial="5c2fafaabbcc"):
        self.serial = serial
        self.ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        self.ctx.load_cert_chain(cert_path, key_path)
        self.button_pressed = False
        self.tokens: set[str] = set()
        self.measurement = dict(MEASUREMENT)
        self.ws_enabled = True
        self.identify_calls = 0
        self.server = None
        self.port = None

    def _json(self, status: int, data) -> Response:
        body = json.dumps(data).encode()
        headers = Headers({"Content-Type": "application/json", "Content-Length": str(len(body)), "Connection": "close"})
        return Response(status, HTTPStatus(status).phrase, headers, body)

    def _authorized(self, request) -> bool:
        auth = request.headers.get("Authorization", "")
        return auth.startswith("Bearer ") and auth[7:] in self.tokens

    async def process_request(self, connection, request):
        path = request.path
        if path == "/api/ws":
            return None if self.ws_enabled else self._json(404, {"error": "not found"})
        if path == "/api/user":
            # websockets only parses GET requests; pairing is exercised via the dedicated test hook below.
            return self._json(405, {"error": "use pair()"})
        if not self._authorized(request):
            return self._json(401, {"error": "user:unauthorized"})
        if path == "/api":
            return self._json(200, {"product_name": "P1 Meter", "product_type": "HWE-P1", "serial": self.serial,
                                    "firmware_version": "6.00", "api_version": "2.0.0"})
        if path == "/api/measurement":
            return self._json(200, self.measurement)
        return self._json(404, {"error": "not found"})

    async def handler(self, ws):
        await ws.send(json.dumps({"type": "authorization_requested", "data": {"api_version": "2.0.0"}}))
        msg = json.loads(await ws.recv())
        if msg.get("type") != "authorization" or msg.get("data") not in self.tokens:
            await ws.send(json.dumps({"type": "error", "data": {"message": "user:unauthorized"}}))
            return
        await ws.send(json.dumps({"type": "authorized"}))
        sub = json.loads(await ws.recv())
        assert sub == {"type": "subscribe", "data": "measurement"}
        while True:
            await ws.send(json.dumps({"type": "measurement", "data": self.measurement}))
            await asyncio.sleep(0.2)

    async def start(self):
        self.server = await websockets.serve(self.handler, "127.0.0.1", 0, ssl=self.ctx,
                                             process_request=self.process_request)
        self.port = self.server.sockets[0].getsockname()[1]
        return self

    async def stop(self):
        self.server.close()
        await self.server.wait_closed()
