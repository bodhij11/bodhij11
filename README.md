# Water Condenser Savings Model (AI Server Room)

This repo now includes `condenser_analysis.py`, a standalone program that estimates how much water an air-water condenser can recover in a server room and whether the setup is beneficial.

## What it evaluates

Given temperature, humidity, airflow, and cost variables, the script computes:

- Water recovered (L/hr)
- Latent cooling equivalent (kW)
- Net heat impact to the room (kW)
- Net economic value per hour ($/hr)
- Inequality checks:
  - `water_recovery_with_no_net_heat_gain`: `water_lph > 0` and `net_heat_kw <= 0`
  - `economic_break_even_or_better`: `net_usd_per_h >= 0`

It also writes an SVG graph (`break_even.svg` by default) showing the economic break-even boundary across inlet temperature and humidity.

## Run

```bash
python3 condenser_analysis.py
```

## Example with custom inputs

```bash
python3 condenser_analysis.py \
  --inlet-temp-c 34 \
  --inlet-rh 0.62 \
  --outlet-temp-c 17 \
  --outlet-rh 0.95 \
  --airflow-m3h 1800 \
  --condenser-power-kw 2.8 \
  --electricity-usd-kwh 0.13 \
  --water-usd-liter 0.005 \
  --graph-out my_break_even.svg
```

Open the SVG in a browser to view the break-even curve.
