"""Generic, user-configured read-only integrations (Modbus TCP, HTTP/JSON, MQTT).

These drivers contain NO device knowledge: no register addresses, endpoints or topics.
The installer copies them from the device's own official documentation into a
value mapping (``connection.values``). The drivers never write to a device.
"""
