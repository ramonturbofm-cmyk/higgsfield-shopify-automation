"""GridMeter: the brand-neutral view of what happens at the grid connection.

The optimizer and controllers only ever see a ``GridMeter`` — never which
physical device (HomeWizard P1, direct DSMR, Modbus meter, ...) provides it.
"""

from ems.gridmeter.meter import (  # noqa: F401
    GRID_FEATURES,
    GridMeter,
    GridMeterReading,
    GridMeterSelection,
    GridMeterStatus,
    feature_availability,
    select_primary_grid_meter,
)
