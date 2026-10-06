"""Demo Home: one complete simulated site to test the whole software without hardware."""

from __future__ import annotations

DEMO_CONFIG = {
    "version": 1,
    "runtime": {"mode": "demo", "simulation_mode": True, "dry_run": False, "demo_speed": 1.0},
    "site": {"id": "demo", "name": "Demo Home", "latitude": 52.09, "longitude": 5.12,
             "timezone": "Europe/Amsterdam", "annual_consumption_kwh": 3500},
    "grid": {"phases": 3, "ampere_per_phase": 25, "max_import_kw": 17, "max_export_kw": 17},
    "control": {"interval_s": 5},
    "optimizer": {"interval_minutes": 5, "horizon_hours": 36},
    "battery": {"min_soc": 10, "max_soc": 95, "reserve_soc": 15, "degradation_cost_per_kwh": 0.03,
                "min_arbitrage_spread_eur": 0.05},
    "heatpump": {"comfort_temperature": 21.0, "min_temperature": 20.5, "max_preheat_temperature": 22.0},
    "strategy": {"profile": "lowest_cost", "export_mode": "smart", "export_price_threshold_eur": 0.0},
    # Example contract values for the demo only — enter your own contract in Instellingen > Tarief.
    "tariff": {"contract_name": "Demo dynamisch contract", "supplier": "Voorbeeld", "contract_type": "dynamic",
               "import_markup_eur_kwh": 0.02, "energy_tax_eur_kwh": 0.10, "vat_pct": 21,
               "export_markup_eur_kwh": -0.02, "fixed_monthly_eur": 6.0, "grid_monthly_eur": 40.0},
    "prices": {"provider": "demo"},
    "forecast": {"weather_provider": "demo"},
    "devices": [
        {"id": "p1", "name": "Slimme meter (P1)", "category": "smart_meter", "driver": "mock.smart_meter",
         "role": "primary_grid_meter"},
        {"id": "pv_roof", "name": "PV dak (5,6 kWp)", "category": "pv_inverter", "driver": "mock.pv_inverter",
         "params": {"peak_power_kw": 5.6, "azimuth_deg": 180, "sim": {"peak_power_kw": 5.6, "azimuth_deg": 180}}},
        {"id": "pv_garage", "name": "PV garage (2,4 kWp)", "category": "pv_inverter", "driver": "mock.pv_inverter",
         "phase": "L2",
         "params": {"peak_power_kw": 2.4, "azimuth_deg": 250, "sim": {"peak_power_kw": 2.4, "azimuth_deg": 250}}},
        {"id": "battery", "name": "Thuisbatterij 15 kWh", "category": "battery", "driver": "mock.battery",
         "params": {"capacity_kwh": 15.0, "max_charge_w": 6000, "max_discharge_w": 6000,
                    "sim": {"max_charge_w": 6000, "max_discharge_w": 6000, "soc_pct": 45}}},
        {"id": "heatpump", "name": "Warmtepomp", "category": "heat_pump", "driver": "mock.heat_pump", "phase": "L1",
         "params": {"heat_loss_w_per_k": 150, "thermal_capacity_kwh_per_k": 7.5, "thermal_max_w": 6000,
                    "sim": {"heat_loss_w_per_k": 150, "thermal_capacity_kwh_per_k": 7.5, "thermal_max_w": 6000}}},
        {"id": "ev", "name": "Laadpaal + auto", "category": "ev_charger", "driver": "mock.ev_charger",
         "params": {"charge_mode": "smart", "min_current_a": 6, "max_current_a": 16, "battery_kwh": 60,
                    "target_soc_pct": 80, "departure": "07:30",
                    "sim": {"battery_kwh": 60, "arrival_soc_pct": 35, "arrive": "17:30", "depart": "07:30"}}},
    ],
}

PRODUCTION_DEFAULT = {
    "version": 1,
    "runtime": {"mode": "production", "simulation_mode": False},
    "site": {"id": "home", "name": "Mijn woning"},
    "devices": [],
}
