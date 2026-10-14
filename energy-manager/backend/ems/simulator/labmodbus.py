"""pymodbus device context for the test environment: values are computed live from the simulated house."""

from __future__ import annotations

import asyncio

from pymodbus.constants import ExcCodes
from pymodbus.datastore import ModbusBaseDeviceContext

TABLES = {1: "coil", 2: "discrete", 3: "holding", 4: "input", 5: "coil", 6: "holding", 15: "coil", 16: "holding",
          23: "holding"}


class LabContext(ModbusBaseDeviceContext):
    def __init__(self, lab, name: str, reader, writer) -> None:
        self.lab, self.name, self.reader, self.writer = lab, name, reader, writer

    def reset(self) -> None:
        return None

    def validate(self, func_code, address, count=1) -> bool:
        return True

    async def async_getValues(self, func_code, address, count=1):
        if (d := self.lab.fault(self.name).delay_s):
            await asyncio.sleep(d)       # slow device: the EMS must time out, not hang
        return self.getValues(func_code, address, count)

    async def async_setValues(self, func_code, address, values):
        if (d := self.lab.fault(self.name).delay_s):
            await asyncio.sleep(d)
        return self.setValues(func_code, address, values)

    def getValues(self, func_code, address, count=1):
        f = self.lab.fault(self.name)
        if f.offline:
            return ExcCodes.GATEWAY_NO_RESPONSE
        if f.exception:
            return ExcCodes(f.exception)
        table = TABLES.get(func_code)
        regs = self.reader()
        out = []
        for a in range(address, address + count):
            v = regs.get((table, a))
            if v is None:
                return ExcCodes.ILLEGAL_ADDRESS
            out.append(v)
        return out

    def setValues(self, func_code, address, values):
        f = self.lab.fault(self.name)
        if f.offline:
            return ExcCodes.GATEWAY_NO_RESPONSE
        if f.exception:
            return ExcCodes(f.exception)
        if TABLES.get(func_code) != "holding":
            return ExcCodes.ILLEGAL_FUNCTION
        regs = self.reader()
        for a in range(address, address + len(values)):
            if ("holding", a) not in regs:
                return ExcCodes.ILLEGAL_ADDRESS
        self.lab.write_log.append((self.name, address, [int(v) for v in values]))
        err = self.writer(address, [int(v) for v in values])
        if err:
            return ExcCodes(err)
        return None
