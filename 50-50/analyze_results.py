import pandas as pd
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------
# CONFIG
# ---------------------------------------------------------
TRIPINFO_PATH = Path("outputs/tripinfo.xml")   # tripinfo with <emissions> child
BATTERY_PATH  = Path("outputs/battery.xml")    # optional battery output (for EVs)

# Service time per logistics truck in seconds (your stop duration)
SERVICE_TIME_PER_TRUCK_S = 600.0

# Grid CO2 factor (Austria typical order: ~0.15–0.25 kg/kWh; set what your report needs)
GRID_CO2_KG_PER_KWH = 0.20

# EV vType ids (match your types.add.xml). Add more ids if needed.
EV_TYPES = {"truck_ev"}
DIESEL_TYPES = {"truck_euro6"}  # extend if you have more ICE types


# ---------------------------------------------------------
# HELPERS
# ---------------------------------------------------------
def load_tripinfo_with_emissions(path: Path) -> pd.DataFrame:
    """
    Load tripinfo.xml and join the <emissions> child data.
    Returns one row per vehicle with travel + emissions info.
    """
    trip = pd.read_xml(path, xpath=".//tripinfo")

    # Normalize some expected columns if missing
    if "id" not in trip.columns:
        raise ValueError("tripinfo.xml missing 'id' attribute on <tripinfo>.")
    if "routeLength" not in trip.columns:
        trip["routeLength"] = pd.NA
    if "duration" not in trip.columns:
        trip["duration"] = pd.NA
    if "vType" not in trip.columns:
        trip["vType"] = trip.get("type", pd.NA)

    em = pd.read_xml(path, xpath=".//emissions")
    # If emissions rows mismatch count, align by index order (SUMO writes one child per tripinfo)
    df = trip.join(em, how="left", lsuffix="", rsuffix="_em")

    # Standardize common emission columns if they exist
    # SUMO HBEFA absolute totals are typically *_abs (mg), fuel_abs (mg)
    for col in ("CO2_abs", "fuel_abs"):
        if col not in df.columns:
            df[col] = 0.0

    return df


def load_battery_totals(path: Path) -> Optional[pd.DataFrame]:
    """
    Load battery.xml and return a per-vehicle total energy table.
    This function is defensive: it tries multiple common battery-output shapes.

    Assumptions:
      - Energy values are in Wh (consistent with SUMO battery 'capacity' unit).
      - We'll build 'energy_Wh' (sum over the simulation) per vehicle id.
    """
    if not path.exists():
        return None

    # Try the most common shapes:
    energy_rows = []

    try:
        # Shape A: nested vehicles under timesteps
        # Read all <vehicle> nodes anywhere
        vdf = pd.read_xml(path, xpath=".//vehicle")
        if not vdf.empty:
            # Harmonize id column name
            if "id" not in vdf.columns:
                id_col = next((c for c in vdf.columns if c.lower() in ("vehicle", "vehid", "name")), None)
                if id_col:
                    vdf = vdf.rename(columns={id_col: "id"})
            # Collect any numeric energy columns we recognize
            energy_cols = [c for c in vdf.columns if c.lower() in
                           ("energyconsumed", "totalenergyconsumed", "chargingenergy", "dischargingenergy", "energy")]
            # Keep only what's numeric
            for c in energy_cols:
                vdf[c] = pd.to_numeric(vdf[c], errors="coerce")
            if "id" in vdf.columns and energy_cols:
                agg = vdf.groupby("id")[energy_cols].sum(min_count=1).reset_index()
                # Prioritize the most direct "consumed" measure
                if "energyConsumed" in agg.columns:
                    agg["energy_Wh"] = agg["energyConsumed"]
                elif "totalEnergyConsumed" in agg.columns:
                    agg["energy_Wh"] = agg["totalEnergyConsumed"]
                elif "energy" in agg.columns:
                    agg["energy_Wh"] = agg["energy"]
                else:
                    # As a fallback, sum all known columns into a generic energy (may overcount charging)
                    agg["energy_Wh"] = agg[energy_cols].sum(axis=1, min_count=1)
                energy_rows.append(agg[["id", "energy_Wh"]])
    except Exception:
        pass

    if not energy_rows:
        return None

    out = pd.concat(energy_rows, ignore_index=True)
    out = out.groupby("id", as_index=False)["energy_Wh"].sum(min_count=1)
    return out


def classify_vehicle(veh_id: str) -> str:
    """
    Assign each vehicle to a group based on its id prefix.
    """
    if veh_id.startswith("T_"):
        return "logistics_truck"
    if veh_id.startswith("bgt_"):
        return "background_truck"
    if veh_id.startswith("bgc_") or veh_id.startswith("F_"):
        return "background_car"
    return "other"


def hub_from_id(veh_id: str) -> str:
    """
    Map logistics truck IDs to their hub.
    """
    if veh_id.startswith("T_SPAR"):
        return "SPAR"
    if veh_id.startswith("T_UCS"):
        return "UCS"
    if veh_id.startswith("T_TGW"):
        return "TGW"
    if veh_id.startswith("T_ROS2"):
        return "Roswell2"
    if veh_id.startswith("T_ROS34"):
        return "Roswell3&4"
    return "other"


def powertrain_from_vtype(vtype: str) -> str:
    """
    EV vs Diesel based on vType id.
    """
    if pd.isna(vtype):
        return "unknown"
    if vtype in EV_TYPES:
        return "EV"
    if vtype in DIESEL_TYPES:
        return "Diesel"
    # simple heuristic: treat 'ev' substring as EV
    return "EV" if "ev" in str(vtype).lower() else "Other"


# ---------------------------------------------------------
# MAIN ANALYSIS
# ---------------------------------------------------------
def main():
    # --- trip + emissions ---
    df = load_tripinfo_with_emissions(TRIPINFO_PATH)

    # Basic metrics
    df["vehicle_group"] = df["id"].apply(classify_vehicle)
    df["distance_km"] = pd.to_numeric(df.get("routeLength", 0), errors="coerce") / 1000.0
    df["travel_time_min"] = pd.to_numeric(df.get("duration", 0), errors="coerce") / 60.0

    # Emissions & fuel (HBEFA absolute mg -> g / kg)
    df["CO2_g"] = pd.to_numeric(df.get("CO2_abs", 0), errors="coerce") / 1000.0
    df["CO2_kg"] = pd.to_numeric(df.get("CO2_abs", 0), errors="coerce") / 1_000_000.0
    df["fuel_g"] = pd.to_numeric(df.get("fuel_abs", 0), errors="coerce") / 1000.0
    df["fuel_kg"] = pd.to_numeric(df.get("fuel_abs", 0), errors="coerce") / 1_000_000.0

    # Per-km metrics (avoid /0)
    dist_km_nz = df["distance_km"].replace(0, pd.NA)
    df["CO2_kg_per_km"] = df["CO2_kg"] / dist_km_nz
    df["fuel_kg_per_km"] = df["fuel_kg"] / dist_km_nz

    # Service / discharge time:
    df["service_time_s"] = 0.0
    df.loc[df["vehicle_group"] == "logistics_truck", "service_time_s"] = SERVICE_TIME_PER_TRUCK_S
    df["driving_time_min"] = (pd.to_numeric(df.get("duration", 0), errors="coerce") - df["service_time_s"]) / 60.0

    # Powertrain detection from vType
    df["powertrain"] = df["vType"].apply(powertrain_from_vtype)

    # --- battery (energy) ---
    bat = load_battery_totals(BATTERY_PATH)
    if bat is not None and not bat.empty:
        # Merge energy (Wh) -> kWh
        df = df.merge(bat.rename(columns={"id": "id"}), on="id", how="left")
        df["energy_Wh"] = pd.to_numeric(df["energy_Wh"], errors="coerce")
        df["energy_kWh"] = df["energy_Wh"] / 1000.0
    else:
        df["energy_kWh"] = pd.NA

    # Indirect CO2 for EVs only
    df["indirect_CO2_kg"] = 0.0
    ev_mask = df["powertrain"].eq("EV") & df["energy_kWh"].notna()
    df.loc[ev_mask, "indirect_CO2_kg"] = df.loc[ev_mask, "energy_kWh"] * GRID_CO2_KG_PER_KWH

    # Combined CO2: tailpipe (HBEFA) + indirect from electricity
    df["total_CO2_kg_combined"] = df["CO2_kg"].fillna(0) + df["indirect_CO2_kg"].fillna(0)

    # -----------------------------------------------------
    # Summary by vehicle group
    # -----------------------------------------------------
    group_summary = (
        df.groupby("vehicle_group", dropna=False)
        .agg(
            n_vehicles=("id", "count"),
            mean_travel_time_min=("travel_time_min", "mean"),
            mean_driving_time_min=("driving_time_min", "mean"),
            mean_distance_km=("distance_km", "mean"),
            tailpipe_CO2_kg=("CO2_kg", "sum"),
            indirect_CO2_kg=("indirect_CO2_kg", "sum"),
            combined_CO2_kg=("total_CO2_kg_combined", "sum"),
            mean_CO2_kg=("CO2_kg", "mean"),
            mean_CO2_kg_per_km=("CO2_kg_per_km", "mean"),
            mean_energy_kWh=("energy_kWh", "mean"),
            total_energy_kWh=("energy_kWh", "sum"),
        )
        .reset_index()
    )

    print("\n=== Summary by vehicle group ===")
    print(group_summary.to_string(index=False, float_format=lambda x: f"{x:,.3f}"))

    # -----------------------------------------------------
    # Split by group *and* powertrain (shows EV vs Diesel)
    # -----------------------------------------------------
    gp_pt_summary = (
        df.groupby(["vehicle_group", "powertrain"], dropna=False)
        .agg(
            n_vehicles=("id", "count"),
            mean_distance_km=("distance_km", "mean"),
            tailpipe_CO2_kg=("CO2_kg", "sum"),
            indirect_CO2_kg=("indirect_CO2_kg", "sum"),
            combined_CO2_kg=("total_CO2_kg_combined", "sum"),
            total_energy_kWh=("energy_kWh", "sum"),
        )
        .reset_index()
        .sort_values(["vehicle_group", "powertrain"])
    )

    print("\n=== By group & powertrain ===")
    print(gp_pt_summary.to_string(index=False, float_format=lambda x: f"{x:,.3f}"))

    # -----------------------------------------------------
    # Logistics trucks by hub (keeps powertrain columns too)
    # -----------------------------------------------------
    trucks = df[df["vehicle_group"] == "logistics_truck"].copy()
    if not trucks.empty:
        trucks["hub"] = trucks["id"].apply(hub_from_id)

        hub_summary = (
            trucks.groupby(["hub", "powertrain"], dropna=False)
            .agg(
                n_vehicles=("id", "count"),
                mean_travel_time_min=("travel_time_min", "mean"),
                mean_driving_time_min=("driving_time_min", "mean"),
                mean_distance_km=("distance_km", "mean"),
                tailpipe_CO2_kg=("CO2_kg", "sum"),
                indirect_CO2_kg=("indirect_CO2_kg", "sum"),
                combined_CO2_kg=("total_CO2_kg_combined", "sum"),
                total_energy_kWh=("energy_kWh", "sum"),
            )
            .reset_index()
            .sort_values(["hub", "powertrain"])
        )

        print("\n=== Logistics trucks by hub & powertrain ===")
        print(hub_summary.to_string(index=False, float_format=lambda x: f"{x:,.3f}"))
    else:
        hub_summary = pd.DataFrame()
        print("\n(No logistics trucks found in this run.)")

    # -----------------------------------------------------
    # Save detailed tables for Excel
    # -----------------------------------------------------
    outputs_dir = Path("outputs")
    outputs_dir.mkdir(exist_ok=True)

    df.to_csv(outputs_dir / "vehicles_with_emissions_and_energy.csv", index=False)
    trucks.to_csv(outputs_dir / "logistics_trucks_detailed.csv", index=False)
    group_summary.to_csv(outputs_dir / "summary_by_group.csv", index=False)
    gp_pt_summary.to_csv(outputs_dir / "summary_by_group_powertrain.csv", index=False)
    if not hub_summary.empty:
        hub_summary.to_csv(outputs_dir / "summary_trucks_by_hub_powertrain.csv", index=False)

    print("\nCSV files written to the 'outputs/' folder:")
    print("  - vehicles_with_emissions_and_energy.csv")
    print("  - logistics_trucks_detailed.csv")
    print("  - summary_by_group.csv")
    print("  - summary_by_group_powertrain.csv")
    if not hub_summary.empty:
        print("  - summary_trucks_by_hub_powertrain.csv")


if __name__ == "__main__":
    main()