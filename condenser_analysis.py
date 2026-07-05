#!/usr/bin/env python3
"""Water condenser evaluator for AI server rooms.

This script estimates:
- Hourly water recovered by condensation
- Net heat impact on the server room
- Economic break-even based on water and electricity costs
- A break-even graph across inlet temperature and humidity
"""

from __future__ import annotations

import argparse
import math
from dataclasses import dataclass
from pathlib import Path

STD_PRESSURE_KPA = 101.325
LATENT_HEAT_KJ_PER_KG = 2450.0
AIR_DENSITY_KG_PER_M3 = 1.2


@dataclass
class RoomInputs:
    inlet_temp_c: float
    inlet_rh: float
    outlet_temp_c: float
    outlet_rh: float
    airflow_m3_per_h: float
    condenser_power_kw: float
    electricity_usd_per_kwh: float
    water_usd_per_liter: float


def saturation_vapor_pressure_kpa(temp_c: float) -> float:
    """Tetens equation approximation, good for typical HVAC ranges."""
    return 0.61078 * math.exp((17.2694 * temp_c) / (temp_c + 237.3))


def humidity_ratio_kg_per_kg_dry_air(temp_c: float, rh: float) -> float:
    rh_clamped = max(0.0, min(1.0, rh))
    p_sat = saturation_vapor_pressure_kpa(temp_c)
    p_v = rh_clamped * p_sat
    return 0.62198 * p_v / (STD_PRESSURE_KPA - p_v)


def condensed_water_lph(
    inlet_temp_c: float,
    inlet_rh: float,
    outlet_temp_c: float,
    outlet_rh: float,
    airflow_m3_per_h: float,
) -> float:
    w_in = humidity_ratio_kg_per_kg_dry_air(inlet_temp_c, inlet_rh)
    w_out = humidity_ratio_kg_per_kg_dry_air(outlet_temp_c, outlet_rh)
    delta_w = max(0.0, w_in - w_out)
    dry_air_kg_per_h = airflow_m3_per_h * AIR_DENSITY_KG_PER_M3
    condensed_kg_per_h = dry_air_kg_per_h * delta_w
    return condensed_kg_per_h


def latent_cooling_kw(water_kg_per_h: float) -> float:
    return (water_kg_per_h * LATENT_HEAT_KJ_PER_KG) / 3600.0


def evaluate(inputs: RoomInputs) -> dict[str, float | bool | str]:
    water_lph = condensed_water_lph(
        inputs.inlet_temp_c,
        inputs.inlet_rh,
        inputs.outlet_temp_c,
        inputs.outlet_rh,
        inputs.airflow_m3_per_h,
    )
    latent_kw = latent_cooling_kw(water_lph)

    # Positive => adds net heat to room, Negative => net cooling effect.
    net_heat_kw = inputs.condenser_power_kw - latent_kw

    water_value_per_h = water_lph * inputs.water_usd_per_liter
    energy_cost_per_h = inputs.condenser_power_kw * inputs.electricity_usd_per_kwh
    net_usd_per_h = water_value_per_h - energy_cost_per_h

    better_water_than_heat = water_lph > 0.0 and net_heat_kw <= 0.0
    economically_break_even_or_better = net_usd_per_h >= 0.0

    if better_water_than_heat and economically_break_even_or_better:
        verdict = "Saving water and not adding net heat (and at/above economic break-even)."
    elif better_water_than_heat:
        verdict = "Physically favorable (water recovery with no net heat gain), but below economic break-even."
    elif economically_break_even_or_better:
        verdict = "Economically favorable, but unit still adds net heat to the room."
    else:
        verdict = "Not favorable: adds net heat and below economic break-even."

    return {
        "water_lph": water_lph,
        "latent_kw": latent_kw,
        "net_heat_kw": net_heat_kw,
        "water_value_per_h": water_value_per_h,
        "energy_cost_per_h": energy_cost_per_h,
        "net_usd_per_h": net_usd_per_h,
        "better_water_than_heat": better_water_than_heat,
        "economically_break_even_or_better": economically_break_even_or_better,
        "verdict": verdict,
    }


def break_even_net_usd_per_h(
    inlet_temp_c: float,
    inlet_rh: float,
    outlet_temp_c: float,
    outlet_rh: float,
    airflow_m3_per_h: float,
    condenser_power_kw: float,
    electricity_usd_per_kwh: float,
    water_usd_per_liter: float,
) -> float:
    water_lph = condensed_water_lph(
        inlet_temp_c, inlet_rh, outlet_temp_c, outlet_rh, airflow_m3_per_h
    )
    return (water_lph * water_usd_per_liter) - (condenser_power_kw * electricity_usd_per_kwh)


def interpolate_zero(x0: float, y0: float, x1: float, y1: float) -> float:
    if y1 == y0:
        return (x0 + x1) / 2.0
    return x0 + (0.0 - y0) * (x1 - x0) / (y1 - y0)


def build_break_even_curve(
    temp_min: float,
    temp_max: float,
    rh_min: float,
    rh_max: float,
    temp_steps: int,
    rh_steps: int,
    outlet_delta_c: float,
    outlet_rh_factor: float,
    airflow_m3_per_h: float,
    condenser_power_kw: float,
    electricity_usd_per_kwh: float,
    water_usd_per_liter: float,
) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    for i in range(rh_steps + 1):
        rh = rh_min + (rh_max - rh_min) * i / rh_steps
        prev_t = None
        prev_val = None
        for j in range(temp_steps + 1):
            t = temp_min + (temp_max - temp_min) * j / temp_steps
            out_t = t - outlet_delta_c
            out_rh = min(1.0, max(0.0, rh * outlet_rh_factor))
            val = break_even_net_usd_per_h(
                t,
                rh,
                out_t,
                out_rh,
                airflow_m3_per_h,
                condenser_power_kw,
                electricity_usd_per_kwh,
                water_usd_per_liter,
            )
            if prev_t is not None and prev_val is not None:
                crossed = (prev_val <= 0.0 <= val) or (prev_val >= 0.0 >= val)
                if crossed:
                    t_zero = interpolate_zero(prev_t, prev_val, t, val)
                    points.append((t_zero, rh))
                    break
            prev_t = t
            prev_val = val
    return points


def write_svg(
    out_path: Path,
    points: list[tuple[float, float]],
    temp_min: float,
    temp_max: float,
    rh_min: float,
    rh_max: float,
) -> None:
    width = 900
    height = 600
    left = 90
    right = 40
    top = 40
    bottom = 70
    plot_w = width - left - right
    plot_h = height - top - bottom

    def x_map(t: float) -> float:
        return left + ((t - temp_min) / (temp_max - temp_min)) * plot_w

    def y_map(rh: float) -> float:
        return top + (1.0 - (rh - rh_min) / (rh_max - rh_min)) * plot_h

    polyline = " ".join(f"{x_map(t):.2f},{y_map(rh):.2f}" for t, rh in points)

    svg = f"""<svg xmlns='http://www.w3.org/2000/svg' width='{width}' height='{height}'>
  <rect x='0' y='0' width='{width}' height='{height}' fill='white'/>
  <rect x='{left}' y='{top}' width='{plot_w}' height='{plot_h}' fill='#f8fbff' stroke='#bbb'/>
  <line x1='{left}' y1='{top + plot_h}' x2='{left + plot_w}' y2='{top + plot_h}' stroke='black'/>
  <line x1='{left}' y1='{top}' x2='{left}' y2='{top + plot_h}' stroke='black'/>
  <text x='{width/2:.1f}' y='{height-20}' text-anchor='middle' font-size='16'>Inlet Temperature (°C)</text>
  <text x='25' y='{height/2:.1f}' text-anchor='middle' transform='rotate(-90 25 {height/2:.1f})' font-size='16'>Inlet Relative Humidity (fraction)</text>
  <text x='{left + 10}' y='{top + 25}' font-size='14' fill='#1d4ed8'>Blue curve: economic break-even (net $/hr = 0)</text>
  <text x='{left + 10}' y='{top + 45}' font-size='13' fill='#555'>Right of curve: more water-value saved than electricity cost</text>
  <text x='{left + 10}' y='{top + 63}' font-size='13' fill='#555'>Left of curve: electricity cost exceeds water-value saved</text>
  <polyline points='{polyline}' fill='none' stroke='#1d4ed8' stroke-width='3'/>
</svg>
"""
    out_path.write_text(svg)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="AI server-room water condenser evaluator")
    p.add_argument("--inlet-temp-c", type=float, default=32.0)
    p.add_argument("--inlet-rh", type=float, default=0.60)
    p.add_argument("--outlet-temp-c", type=float, default=16.0)
    p.add_argument("--outlet-rh", type=float, default=0.95)
    p.add_argument("--airflow-m3h", type=float, default=1200.0)
    p.add_argument("--condenser-power-kw", type=float, default=2.2)
    p.add_argument("--electricity-usd-kwh", type=float, default=0.12)
    p.add_argument("--water-usd-liter", type=float, default=0.004)
    p.add_argument("--graph-out", type=Path, default=Path("break_even.svg"))

    p.add_argument("--temp-min", type=float, default=18.0)
    p.add_argument("--temp-max", type=float, default=45.0)
    p.add_argument("--rh-min", type=float, default=0.20)
    p.add_argument("--rh-max", type=float, default=0.95)
    p.add_argument("--temp-steps", type=int, default=220)
    p.add_argument("--rh-steps", type=int, default=80)

    p.add_argument(
        "--graph-outlet-delta-c",
        type=float,
        default=12.0,
        help="For break-even map: outlet_temp = inlet_temp - this value",
    )
    p.add_argument(
        "--graph-outlet-rh-factor",
        type=float,
        default=1.35,
        help="For break-even map: outlet_rh = min(1, inlet_rh * this value)",
    )
    return p.parse_args()


def main() -> None:
    args = parse_args()

    inputs = RoomInputs(
        inlet_temp_c=args.inlet_temp_c,
        inlet_rh=args.inlet_rh,
        outlet_temp_c=args.outlet_temp_c,
        outlet_rh=args.outlet_rh,
        airflow_m3_per_h=args.airflow_m3h,
        condenser_power_kw=args.condenser_power_kw,
        electricity_usd_per_kwh=args.electricity_usd_kwh,
        water_usd_per_liter=args.water_usd_liter,
    )

    result = evaluate(inputs)

    print("=== Condenser Evaluation ===")
    print(f"Water recovered: {result['water_lph']:.3f} L/hr")
    print(f"Latent cooling equivalent: {result['latent_kw']:.3f} kW")
    print(f"Net heat impact: {result['net_heat_kw']:.3f} kW (positive means extra room heat)")
    print(f"Water value per hour: ${result['water_value_per_h']:.4f}")
    print(f"Electricity cost per hour: ${result['energy_cost_per_h']:.4f}")
    print(f"Net economic value per hour: ${result['net_usd_per_h']:.4f}")
    print(f"Inequality check (water recovery with no net heat gain): {result['better_water_than_heat']}")
    print(f"Inequality check (economic break-even or better): {result['economically_break_even_or_better']}")
    print(f"Verdict: {result['verdict']}")

    curve = build_break_even_curve(
        temp_min=args.temp_min,
        temp_max=args.temp_max,
        rh_min=args.rh_min,
        rh_max=args.rh_max,
        temp_steps=args.temp_steps,
        rh_steps=args.rh_steps,
        outlet_delta_c=args.graph_outlet_delta_c,
        outlet_rh_factor=args.graph_outlet_rh_factor,
        airflow_m3_per_h=args.airflow_m3h,
        condenser_power_kw=args.condenser_power_kw,
        electricity_usd_per_kwh=args.electricity_usd_kwh,
        water_usd_per_liter=args.water_usd_liter,
    )

    write_svg(args.graph_out, curve, args.temp_min, args.temp_max, args.rh_min, args.rh_max)
    print(f"Break-even graph written to: {args.graph_out}")


if __name__ == "__main__":
    main()
