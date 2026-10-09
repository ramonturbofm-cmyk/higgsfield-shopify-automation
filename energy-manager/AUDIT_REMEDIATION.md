# Audit-remediatie — Energy Manager 0.4.0

Bron: [docs/Energy_Manager_Audit_v0.2.0.md](docs/Energy_Manager_Audit_v0.2.0.md). Dit document zegt per
bevinding wat de oorzaak was, wat er is veranderd (met bestanden), welke test het aantoont en de status.

**Statussen.** `FIXED` = geïmplementeerd **en** getest (geautomatiseerd). `PARTIAL` = deels opgelost, de
rest staat erbij. `OPEN` = niet opgelost. `BLOCKED BY HARDWARE` = alleen met echte apparatuur aan te tonen.
Niets in deze release is met echte hardware getest; "getest" betekent: unit-, simulator-, API-, browser- of
CI-test (Windows-runner, Ubuntu-runner, Docker amd64 en arm64 onder QEMU-emulatie).

Samenvatting: **P0: 7 FIXED, 1 PARTIAL** (P0-08: bewezen in simulatie, niet met hardware-in-the-loop).
**P1: 33 FIXED, 5 PARTIAL, 1 OPEN** (P1-34 multi-site). **P2: 8 FIXED, 4 PARTIAL, 1 BLOCKED** (code signing).

---

## P0 — kritieke veiligheid

| ID | Oorzaak | Oplossing en bestanden | Test | Status |
|---|---|---|---|---|
| P0-01 Capabilities niet typegebonden | Één brede capabilitylijst; UI liet alle bedieningen zien zodra er één `control_*` was | Eén bron: `TYPE_SCHEMAS` per apparaatsoort; apparaatcapabilities = driver ∩ type (`device_capabilities()`); nieuwe fijnmazige capabilities (o.a. `control_battery_power` los van `control_battery_mode`, `control_soc_limit`, `control_temp_setpoint`). `backend/ems/devices/capabilities.py`, `devices/base.py`, `core/models.py`, alle aanroepers | `test_audit_p0.py::test_01…`, `test_02…`, `test_audit_ui_schema.py::test_19_*` | FIXED |
| P0-02 Geen validatie per actie | Overrides controleerden alleen of de capability bestond | Centrale `can_execute(device, command, user, control_state)`: rol, typecapability, drivercapability, bereik uit de eigen apparaatlimieten, control state, gate-modus; endpoint `GET /devices/{id}/actions`; `POST /overrides` weigert met reden. `control/authority.py`, `core/engine.py`, `api/routes_core.py`, `api/routes_devices.py` | `test_audit_p0.py::test_01/02/05`, `test_distributed.py` (20 kW → 422) | FIXED |
| P0-03 Automatiseringen met ongeldige acties | Globale actielijst, alleen schema-validatie | Catalogus per apparaat met alleen ondersteunde acties en bereiken; server weigert onverenigbare combinaties bij opslaan én bij uitvoeren. `api/routes_more.py`, `server/runtime.py`, `web/js/views/automations.js` | `test_audit_p0.py::test_04…` | FIXED |
| P0-04 Control state niet eenduidig | UI toonde "AUTO — het EMS regelt" ook bij read-only/schaduw | Eén gezaghebbende state (READ_ONLY, SHADOW, LIMITED_CONTROL, FULL_CONTROL, MANUAL_OVERRIDE, SAFE_MODE, OFFLINE, ERROR); uitvoeringsketen gewenst → gevalideerd → verzonden → bevestigd/onbevestigd/geen terugmelding → gemeten; "EMS zou doen" naast "EMS doet nu". `control/authority.py` (`ConfirmationTracker`), `core/engine.py`, `web/js/views/common.js`, `devices.js`, `dashboard.js` | `test_audit_p0.py::test_confirmation_tracker_states`, `test_05…`; browser (`tests/ui/ui_acceptance.py`) | FIXED |
| P0-05 Back-up met sleutels standaard aan | `checked: true`, sleutel onversleuteld in zip | Sleutels standaard uit; met sleutels is een wachtwoord verplicht en wordt het hele bestand versleuteld (scrypt + Fernet); herstellen vraagt het wachtwoord; CLI `--include-keys` vraagt wachtwoord. `services/backup.py`, `api/routes_more.py`, `__main__.py`, `web/js/views/system.js` | `test_api.py::test_backup_restore_roundtrip` (standaard zonder sleutel, 422 zonder wachtwoord, versleuteld bestand, fout wachtwoord geweigerd) | FIXED |
| P0-06 Token in localStorage en WS-URL | Bearer-token in `localStorage`, `?token=` in WebSocket en downloads | HttpOnly-sessiecookie (SameSite=Strict, Secure op HTTPS), CSRF double-submit (`X-CSRF-Token`), sliding rotatie, uitloggen trekt de sessie in, eenmalig WS-ticket (30 s), geen tokens in URL's. Scripts gebruiken `Authorization: Bearer`. LAN/TLS-eisen in [INSTALL_LINUX.md](INSTALL_LINUX.md). `api/deps.py`, `security/auth.py`, `api/app.py`, `api/routes_core.py`, `web/js/lib.js`, `app.js` | `test_audit_p0.py::test_p0_06_*`, `test_api.py::test_websocket_live_updates`, browser (cookie HttpOnly, geen localStorage-token) | FIXED |
| P0-07 Geen bewijs van de veiligheidsketen | Alleen losse unit tests | Testmatrix: 9 commandotypes × 8 situaties (vol, beperkt, schaduw, alleen-lezen, verbindingstest, offline, verouderde data, geen regelrecht) door de echte CommandGate; plus grenzen, SOC, capability, rate limit. Volledige regeling vereist bij echte hardware een op hardware bewezen driver (`manifest.verified`) en wordt anders geweigerd. `tests/test_safety_matrix.py`, `control/safety.py`, `server/commissioning.py` | `test_safety_matrix.py` (75 tests) | FIXED (hardware: BLOCKED BY HARDWARE) |
| P0-08 Geen regelen op oude netdata | Bewijs ontbrak | Verouderde netmeting → fail-safe, alleen vrijgave-opdrachten; netwerkpartitie → lease verloopt → apparaten terug op eigen regeling; herstart en node-uitval getest. Volledige regeling blijft voor niet-bewezen hardware geblokkeerd. | `test_safety_matrix.py::test_stale_grid_meter_*`, `test_engine.py`, `test_distributed.py::test_split_brain_and_partition` | PARTIAL — simulatie bewezen; hardware-in-the-loop BLOCKED BY HARDWARE |

## P1 — tarieven en financiën

| ID | Oorzaak | Oplossing en bestanden | Test | Status |
|---|---|---|---|---|
| P1-01 NL 2026 € 0,09157 | Verkeerd getal in de tabel | € 0,09161 excl. btw (Belastingdienst: € 0,1108481 incl. 21 % btw). `tariffs/taxes.py` | `test_audit_tariffs.py::test_07_*` | FIXED |
| P1-02 Alleen eerste schijf | Eén tarief per jaar | Schijvenmodel (0–10.000 / 10.000–50.000 / 50.000–10 mln / daarboven) met verdeling over schijven; hogere schijven 2026 uit een gepubliceerd overzicht, gemarkeerd "nog controleren"; > 10 mln kWh: handmatig. `tariffs/taxes.py` | `test_07_nl_2026_first_bracket_and_brackets` | FIXED |
| P1-03 Settlement één benadering | Saldering altijd tegen gemiddelde afnameprijs | Contractafhankelijk: `tax_only` (dynamisch: alleen energiebelasting + btw vervalt) of `import_price` (vast/variabel), instelbaar; resultaat heet `estimate`, scenario's heten `scenario`. `services/settlement.py`, `core/config.py` | `test_07_settlement_is_contract_dependent` | FIXED |
| P1-04 Jaarvergelijking ceteris paribus | Beperking niet getoond | Expliciete beperkingstekst in API en UI. `services/settlement.py`, `views/finance.js` | `test_08_scenario_comparison_states_its_limitation` | FIXED |
| P1-05 Handmatige prijzen per uur | `resolution_min: 60` hard-coded | 15/60 min, tijdzone verplicht, grens-, dubbel- en eenheidscontrole, gatenrapport (ook rond zomer-/wintertijd), CSV-bestand, "Controleren" vóór opslaan. `prices/providers.py`, `api/routes_core.py`, `views/settings.js` | `test_audit_prices.py::test_09_manual_*` | FIXED |
| P1-06 "Nu"-prijs uit verlopen data | `findIndex()-1` | Server bepaalt de prijs met `start ≤ nu < einde`; anders "niet beschikbaar". `prices/service.py`, `api/routes_core.py`, `views/prices.js` | `test_10_*` (2 tests) | FIXED |
| P1-07 Interval-status niet zichtbaar | — | Status per interval bevestigd/schatting/ontbreekt, tabel met markt/afname/teruglevering; schattingen nooit als bekende prijs; min/max alleen over bevestigde prijzen. `prices/service.py`, `views/prices.js` | `test_10_current_price_requires_interval_overlap` | FIXED |
| P1-08 Vaste kosten niet marginaal | Niet aangetoond | Getest dat vaste kosten nooit in kWh-prijzen of de energiepost vallen. | `test_11_vat_and_fixed_costs_are_not_marginal`, `test_07_settlement_*` | FIXED |
| P1-09 Baseline onverklaard | "Zonder EMS" niet gedefinieerd | Ladder B0 (geen PV/batterij) → B1 (PV, batterij ongebruikt) → B2 (batterij op eigen zelfconsumptieregeling, gesimuleerd) → B3 (werkelijk), met definities en aannames; datadekking prominent. `services/finance.py`, `views/finance.js` | `test_12_finance_baselines_reconcile_*` | FIXED |
| P1-10 Dubbeltellingen | Posten overlapten | Posten = verschillen tussen opeenvolgende baselines → tellen exact op tot B0 − B3 (`reconciled`); indicatoren (negatieve prijzen, warmtepomp) apart en niet opgeteld. | idem | FIXED |
| P1-11 Saldering/vergoeding 2027 | Globale vlag | Regels per datum (2026 salderen, 2027 niet), contractparameters voor terugleververgoeding/-kosten/btw, netting-methode per contract; negatieve terugleverprijzen kosten geld. | `test_08_year_boundary_*`, `test_08_negative_export_*` | FIXED |
| P1-12 Resolutie contract ≠ markt | Eén resolutie | Markt 15 min, contract 15/60 min (`price_resolution_min`, uurgemiddelde), optimizerstap en aggregatie los. `tariffs/engine.py`, `optimizer/service.py`, `services/settlement.py` | `test_09_hourly_contract_on_quarter_hour_market` | FIXED |

## P1 — planning en optimizer

| ID | Oorzaak | Oplossing en bestanden | Test | Status |
|---|---|---|---|---|
| P1-13 Horizon hard-coded 36 h | UI-constante | Horizon uit instelling; UI toont tot wanneer prijzen bekend zijn. `server/runtime.py::plan_view`, `views/planning.js` | `test_plan_api_follows_horizon_setting_and_aggregates` | FIXED |
| P1-14 "Per uur" = elk 4e slot | Sampling | Server-aggregatie op tijd: energie opgeteld, prijs/vermogen tijdgewogen, min/max, SOC eind van het uur; werkt voor 5/15/30/60 min. `optimizer/explain.py::aggregate` | `test_22_any_interval_and_true_hourly_aggregation` (5/15/60) | FIXED |
| P1-15 Uitleg veronderstelt 15 min | `i+49` | Vensters op tijdstempels (12 h). `optimizer/explain.py` | `test_reasons_are_structured_and_independent_of_slot_length` | FIXED |
| P1-16 "Waarom?" verzonnen in de browser | Heuristiek in `planning.js` | Redencodes uit dezelfde optimizerrun (run-id): beslisvariabelen, actieve grenzen (SOC, aansluiting, exportlimiet, piek), break-even; frontend-heuristiek verwijderd. `optimizer/explain.py`, `optimizer/model.py`, `views/planning.js` | idem + `test_no_cycling_reason_cites_break_even` | FIXED |
| P1-17 Winst per slot onbewezen | — | Alleen optimizercijfers (energie + slijtage); break-even-functie met exact dezelfde kostentermen als het model; getest dat onder break-even niet wordt gehandeld. Bugfix: gerapporteerd netladen kon > 0 zijn zonder laden. `optimizer/model.py` | `test_11_break_even_spread_includes_losses_and_wear` | FIXED (zie beperking "verwacht voordeel" in KNOWN_LIMITATIONS) |
| P1-18 `pv_w` vs `pv_forecast_w` | Twee velden, inconsistent gebruikt | Contract vastgelegd: `pv_forecast_w` = beschikbare PV, `pv_w` = gepland na afregelen, `curtail_w` = verschil; UI gebruikt overal `pv_w`. `optimizer/model.py`, `views/planning.js` | `test_22_*` | FIXED |
| P1-19 WP-modus geschat in de UI | Drempel in `common.js` | `hp_action` expliciet uit de planner; controller gebruikt hetzelfde veld; frontendheuristiek verwijderd. `optimizer/explain.py::hp_action`, `control/optimizing.py` | `test_heat_pump_action_is_explicit_single_rule` | FIXED |
| P1-20 `Math.max([])` | Geen lege-lijst-afhandeling | Reden `insufficient_price_info`; UI "onvoldoende prijsinformatie". | `test_reasons_are_structured…` | FIXED |

## P1 — apparaten, wizard, regeling

| ID | Oorzaak | Oplossing en bestanden | Test | Status |
|---|---|---|---|---|
| P1-21 Generieke drivers claimen functies | Manifest met alle `read_*` | Manifest leeg; capabilities alleen uit een gevalideerde mapping; generieke drivers schrijven nooit. `integrations/generic/*` | `test_03_unmapped_modbus_device_claims_nothing` | FIXED |
| P1-22 Simple toont protocollen | Driverlijst | Stap "Merk en model"; generieke koppelingen onder "Mijn apparaat staat er niet tussen"; driver-id/protocol alleen in Expert. `views/devices.js` | browser (`ui_acceptance.py`, schermen in 3 niveaus) | PARTIAL — automatische apparaatdetectie alleen voor HomeWizard (mDNS) |
| P1-23 Geen bewerkflow | — | "Bewerken": naam, adres/verbinding, fase, apparaatgegevens; zelfde apparaat-ID en historie; server valideert. `views/devices.js`, `api/routes_devices.py` | `test_api.py` (update), browser | FIXED |
| P1-24 Fase altijd zichtbaar | — | Fase alleen bij elektrisch relevante soorten (typeschema); "NA" voor de rest; L1/L2/L3/driefasig. `api/routes_devices.py`, `core/config.py` | API-tests, `test_audit_ui_schema.py` | FIXED |
| P1-25 "Toch toevoegen" | Tekst | "Opslaan als niet-verbonden" + uitleg dat regelen pas na test/inbedrijfstelling kan. | browser | FIXED |
| P1-26 HomeWizard standaard primair | Vervangt bestaande meter stil | Vervangen alleen na expliciete bevestiging (`replace_primary`), 409 met huidige meter; wizard vinkt niet aan als er al een primaire is. `gridmeter/assign.py`, `api/routes_devices.py` | `test_audit_meter.py` | FIXED |
| P1-27 Commissioning generiek | Vaste veldenset | Procedure per apparaat: verbindingstest, bestuurbare functies, driver met documentatie, veiligheidsinstellingen van dat type, schaduw waargenomen, schrijftest, op hardware bewezen. `server/commissioning.py`, `views/devices.js` | `test_api.py::test_devices_crud_test_and_commissioning`, `test_18_*` | FIXED |
| P1-28 Volledige regeling met `confirm()` | Browserdialoog | Getypte apparaatnaam + geslaagde schrijftest + bewezen driver + veiligheidsinstellingen; server dwingt af. | `test_18_hardware_writes_need_commissioning_and_documentation`, `test_distributed.py` | FIXED |
| P1-29 Ruwe sleutels in de UI | Geen labels | Eén labeltabel voor alle meetwaarden (`METRIC_LABELS_NL`, `/metric-labels`); ruwe sleutels en diagnose alleen in Uitgebreid/Expert. | browser | FIXED |
| P1-30 Geen bereiken op invoer | Vrije getallen | Bereiken uit apparaatlimieten in UI (`min`/`max`) én server (422); waarde zonder ingestelde limiet → "apparaatlimiet niet ingesteld". | `test_02…`, `test_distributed.py` | FIXED |
| P1-31 EV-SOC verondersteld | — | Zonder `read_ev_soc`: "Gewenste energie per laadsessie (kWh)"; optimizer gebruikt die. `devices/capabilities.py`, `optimizer/service.py` | schema-test | FIXED |

## P1 — platformen en nodes

| ID | Oorzaak | Oplossing en bestanden | Test | Status |
|---|---|---|---|---|
| P1-32 Nodes niet met echte processen getest | — | In-process gedistribueerde tests (gateway + controller via HTTP-transport). | `test_distributed.py` | PARTIAL — niet op meerdere fysieke machines |
| P1-33 Linux/Pi niet gedraaid | — | `deploy/acceptance-linux.sh`: Docker amd64, Docker arm64 (QEMU), systemd native: start, web-UI, eerste account, cookie + CSRF, geen nepdata, herstart, crashherstel, upgrade met behoud van data. In CI. | CI-jobs `docker`, `linux-native` (groen) | PARTIAL — fysieke Raspberry Pi BLOCKED BY HARDWARE |
| P1-34 Multi-site | — | Niet gebouwd. | — | OPEN |
| P1-35 Pairing/command-robuustheid | — | Opdracht-ID's (replay geweigerd), epoch-fencing, TTL, klokverschil > 10 s blokkeert opdrachten, gefaalde pairing, tweede controller, partitie. | `test_distributed.py`, `test_safety_nodes.py` | PARTIAL — lossy-LAN/spoofing niet in een netwerktestbed |
| P1-36 Ruwe capabilities op Nodes | — | Samenvatting ("3 meetwaarden · bediening: …"); ruw alleen Expert. `views/nodes.js` | browser | FIXED |
| P1-37 Remote node stil primair | — | Zelfde expliciete vervangflow (409 + bevestigen). `api/routes_nodes.py` | `test_audit_meter.py` (zelfde regel) | FIXED |
| P1-38 Rollen/lease onduidelijk | Begrippen | "Apparaatgateway — meet en voert uit, beslist niet" / "controller — beslist"; lease/epoch alleen Expert. | browser | FIXED |
| P1-39 Wie regelt wat | — | Per node: wie beslist over mijn apparaten, wat gebeurt bij uitval (binnen 30 s eigen regeling). | browser | PARTIAL — één site; zie P1-34 |

## P2 — dashboard, UX, toegankelijkheid, release

| ID | Oorzaak | Oplossing | Test | Status |
|---|---|---|---|---|
| P2-01 Vaste kaarten | — | Tegels alleen voor geïnstalleerde soorten. `views/dashboard.js` | browser | FIXED |
| P2-02 Vaste energieflow | — | Overige gemeten verbruikers (boiler, airco, stekkers…) als "waarvan in huis" (geen dubbeltelling); meerdere batterijen/omvormers opgeteld. | browser | PARTIAL — geen knoop per afzonderlijk apparaat |
| P2-03 Balansbron | — | Huis "BEREKEND uit netmeting" gelabeld; balanswaarschuwing met oorzaken. | browser | PARTIAL — onzekerheidsmarge niet gekwantificeerd |
| P2-04 STALE subtiel | — | Label per waarde: LIVE / VERTRAAGD / VEROUDERD (doorgestreept, "laatst bekende waarde") / GESCHAT / BEREKEND / NIET BESCHIKBAAR. | browser | FIXED |
| P2-05 "Wat doet" vs uitgevoerd | — | "EMS doet nu:" alleen bij verzonden; anders "EMS zou:" + status. | browser | FIXED |
| P2-06 Niveaus niet globaal | Alleen in Instellingen | Niveaukeuze in de bovenbalk; menu, wizard, apparaatpagina, nodes volgen het niveau. | browser (2 niveaus × 22 schermen) | PARTIAL — niet iedere tekst op elk scherm per niveau herschreven |
| P2-07 Navigatie dynamisch | — | Menu toont alleen pagina's voor aanwezige apparaten; geavanceerde pagina's niet in Eenvoudig. | browser | FIXED |
| P2-08 Tijdzone/klok | Browserklok | Tijden in site-tijdzone; "nu"-prijs en leeftijden van de server. | `test_10_*`, browser | FIXED |
| P2-09 Grafiekaggregatie | Elk n-de punt | Tijdemmers met gemiddelde + min/max-band; LTTB beschikbaar; datum op as bij > 20 h. `services/aggregate.py`, `charts.js` | `test_22_*` (aggregatie), browser | FIXED |
| P2-10 Time-outs/offline | `fetch` zonder time-out | AbortController (20 s), "Server offline"-indicator, duidelijke meldingen op elk scherm. `web/js/lib.js` | `ui_acceptance.py` (offline + trage API) | FIXED |
| P2-11 Toegankelijkheid | — | Zichtbare focus, toetsenbord, `prefers-reduced-motion`, aria-labels, tabellen voor prijzen/planning, 1366×768 / 1920×1080 / touch zonder horizontaal scrollen. | `ui_acceptance.py` | PARTIAL — geen volledige WCAG-contrastaudit |
| P2-12 Versiebeheer | 0.2.0 in changelogs | Eén bron (`ems.__version__`, `tools/set_version.py`), versie 0.4.0 overal, downgradebescherming in installer en database. | `test_version.py`; Windows-acceptatie stap 9b | FIXED |
| P2-13 Unsigned installer | Geen certificaat | Gepubliceerd als **UNSIGNED TEST BUILD**; ondertekening alleen met echt certificaat (nooit nagebootst). | — | BLOCKED (certificaat) |

## Acceptatietests uit de audit (§8)

| # | Test | Waar | Resultaat |
|---|---|---|---|
| 1 | WP met alleen aanvoertemp + setpoint: geen batterij/EV, PV_LIMIT geweigerd | `test_audit_p0.py::test_01_*` | PASS |
| 2 | Batterij met alleen SOC + max-SOC: geen Laden/Ontladen, backend weigert | `test_02_*` | PASS |
| 3 | Modbus zonder mapping claimt niets | `test_03_*` | PASS |
| 4 | Automatisering met onverenigbare actie onmogelijk, ook via API | `test_04_*` | PASS |
| 5 | Shadow/read-only schrijft nooit (override en node-import) | `test_05_*` | PASS |
| 6 | Zonder netmeter/fasen geen zero-export/fasebalans | `test_06_*`, `test_06b_*` | PASS |
| 7 | Belastingschijven 2026, € 0,09161, contractafhankelijk | `test_audit_tariffs.py::test_07_*` | PASS |
| 8 | Jaargrens 2026/2027, negatieve terugleverprijs | `test_08_*` | PASS |
| 9 | 92/96/100 kwartieren, DST, 60-min contract op 15-min markt | `test_audit_prices.py::test_09_*` | PASS |
| 10 | "Nu"-prijs niet uit verlopen interval | `test_10_*` | PASS |
| 11 | Btw, vaste kosten, break-even incl. verlies en slijtage | `test_11_*` | PASS |
| 12 | Backtest reproduceerbaar, dekking, geen dubbeltelling | `test_12_*` (finance + backtest) | PASS |
| 13 | Windows: service na sluiten GUI en herstart | `windows/acceptance.ps1` (CI, schone Windows-runner) | PASS (12/12) |
| 14 | Linux en Pi standalone, persistentie, herstart | `deploy/acceptance-linux.sh` (CI: Docker amd64, Docker arm64/QEMU, systemd) | PASS in CI; fysieke Pi: NOT TESTED |
| 15 | Gedistribueerd read/command/ack/audit | `test_distributed.py` (in-process) | PASS (gesimuleerd, niet op 3 machines) |
| 16 | Partitie, pairing, dubbele controller, lease, epoch, herstart, replay | `test_distributed.py`, `test_safety_nodes.py` | PASS |
| 17 | HomeWizard P1 als primaire meter, daarna zero-export | `test_audit_meter.py` + `test_homewizard.py` (nep-apparaat) | PASS gesimuleerd; echte P1: BLOCKED BY HARDWARE |
| 18 | Hardwarecommando's pas na commissioning + documentatie | `test_18_*` | PASS |
| 19 | Alle categorieën zonder irrelevante capabilities/knoppen | `test_audit_ui_schema.py::test_19_*` | PASS |
| 20 | Instellingen met min/max, eenheid, hulptekst, veilige defaults | `test_20_*` (vond en herstelde: standaard regelniveau was "full") | PASS |
| 21 | Trage/offline API | `tests/ui/ui_acceptance.py` | PASS |
| 22 | Optimizer 5/15/60 min, echte uuraggregatie | `test_audit_planning.py::test_22_*` | PASS |
| 23 | 1366×768, 1920×1080, touch, toetsenbord | `tests/ui/ui_acceptance.py` | PASS |
| 24 | Installer: installatie, update, downgradepreventie, verwijderen, config, demo | `windows/acceptance.ps1` + `test_version.py` | PASS (schone Windows-runner; geen aparte VM) |
