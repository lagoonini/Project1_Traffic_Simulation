import pandas as pd
from pathlib import Path

# ---------------------------------------------------------
# CONFIG
# ---------------------------------------------------------
# Path to the tripinfo.xml you want to analyze
TRIPINFO_PATH = Path("outputs/tripinfo.xml")

# Service time (discharge/loading) per logistics truck in seconds
SERVICE_TIME_PER_TRUCK_S = 600.0

# EV / Diesel vTypes
EV_TYPES = {"truck_ev"}
DIESEL_TYPES = {"truck_euro6"}


# ---------------------------------------------------------
# HELPERS
# ---------------------------------------------------------
def load_tripinfo_with_emissions(path: Path) -> pd.DataFrame:
    """
    Load tripinfo.xml and join the <emissions> child data.
    Result: one row per vehicle with travel + emissions info.
    """
    # one row per <tripinfo> (vehicle)
    trip = pd.read_xml(path, xpath=".//tripinfo")

    # one row per <emissions> (aligned by order with tripinfo)
    em = pd.read_xml(path, xpath=".//emissions")

    # join on index => each vehicle gets its emissions columns
    df = trip.join(em)
    return df


def classify_vehicle(veh_id: str) -> str:
    """
    Assign each vehicle to a group based on its id.
    Adjust if you change your naming scheme.
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
    Works with current naming:
      T_SPAR_*, T_UCS_*, T_TGW_*, T_ROS2_*, T_ROS34_*
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
    Classify powertrain based on vType id.
    EV_TYPES and DIESEL_TYPES are defined in the config.
    """
    if pd.isna(vtype):
        return "unknown"
    if vtype in EV_TYPES:
        return "EV"
    if vtype in DIESEL_TYPES:
        return "Diesel"
    # simple heuristic fallback
    s = str(vtype).lower()
    if "ev" in s or "electric" in s:
        return "EV"
    return "Other"


# ---------------------------------------------------------
# MAIN ANALYSIS
# ---------------------------------------------------------
def main():
    df = load_tripinfo_with_emissions(TRIPINFO_PATH)

    # Basic metrics
    df["vehicle_group"] = df["id"].apply(classify_vehicle)
    df["distance_km"] = pd.to_numeric(df["routeLength"], errors="coerce") / 1000.0
    df["travel_time_min"] = pd.to_numeric(df["duration"], errors="coerce") / 60.0

    # Emissions & fuel:
    # SUMO's HBEFA values are in mg; convert to g and kg
    df["CO2_abs"] = pd.to_numeric(df.get("CO2_abs", 0.0), errors="coerce")
    df["fuel_abs"] = pd.to_numeric(df.get("fuel_abs", 0.0), errors="coerce")

    df["CO2_g"] = df["CO2_abs"] / 1000.0
    df["CO2_kg"] = df["CO2_abs"] / 1_000_000.0
    df["fuel_g"] = df["fuel_abs"] / 1000.0
    df["fuel_kg"] = df["fuel_abs"] / 1_000_000.0

    # Electricity (for EVs)
    df["electricity_Wh"] = pd.to_numeric(df.get("electricity_abs", 0.0), errors="coerce").fillna(0.0)
    df["electricity_kWh"] = df["electricity_Wh"] / 1000.0

    # Per-km metrics
    dist_nz = df["distance_km"].replace(0, pd.NA)
    df["CO2_kg_per_km"] = df["CO2_kg"] / dist_nz
    df["fuel_kg_per_km"] = df["fuel_kg"] / dist_nz
    df["electricity_kWh_per_km"] = df["electricity_kWh"] / dist_nz

    # Powertrain label from vType
    df["powertrain"] = df.get("vType", "").apply(powertrain_from_vtype)

    # Service / discharge time:
    df["service_time_s"] = 0.0
    df.loc[df["vehicle_group"] == "logistics_truck", "service_time_s"] = SERVICE_TIME_PER_TRUCK_S
    df["driving_time_min"] = (pd.to_numeric(df["duration"], errors="coerce") - df["service_time_s"]) / 60.0

    # -----------------------------------------------------
    # Summary by vehicle group (logistics vs background)
    # -----------------------------------------------------
    group_summary = (
        df.groupby("vehicle_group")
        .agg(
            n_vehicles=("id", "count"),
            mean_travel_time_min=("travel_time_min", "mean"),
            mean_driving_time_min=("driving_time_min", "mean"),
            mean_distance_km=("distance_km", "mean"),
            total_CO2_kg=("CO2_kg", "sum"),
            mean_CO2_kg=("CO2_kg", "mean"),
            mean_CO2_kg_per_km=("CO2_kg_per_km", "mean"),
            total_electricity_kWh=("electricity_kWh", "sum"),
            mean_electricity_kWh=("electricity_kWh", "mean"),
            mean_electricity_kWh_per_km=("electricity_kWh_per_km", "mean"),
        )
        .reset_index()
    )

    print("\n=== Summary by vehicle group ===")
    print(group_summary.to_string(index=False, float_format=lambda x: f"{x:,.3f}"))

    # -----------------------------------------------------
    # Summary by vehicle group & powertrain (EV vs Diesel)
    # -----------------------------------------------------
    group_powertrain_summary = (
        df.groupby(["vehicle_group", "powertrain"])
        .agg(
            n_vehicles=("id", "count"),
            mean_distance_km=("distance_km", "mean"),
            total_CO2_kg=("CO2_kg", "sum"),
            total_electricity_kWh=("electricity_kWh", "sum"),
        )
        .reset_index()
        .sort_values(["vehicle_group", "powertrain"])
    )

    print("\n=== Summary by vehicle group & powertrain ===")
    print(group_powertrain_summary.to_string(index=False, float_format=lambda x: f"{x:,.3f}"))

    # -----------------------------------------------------
    # Summary only for logistics trucks, by hub
    # -----------------------------------------------------
    trucks = df[df["vehicle_group"] == "logistics_truck"].copy()
    if not trucks.empty:
        trucks["hub"] = trucks["id"].apply(hub_from_id)

        hub_summary = (
            trucks.groupby("hub")
            .agg(
                n_vehicles=("id", "count"),
                mean_travel_time_min=("travel_time_min", "mean"),
                mean_driving_time_min=("driving_time_min", "mean"),
                mean_distance_km=("distance_km", "mean"),
                total_CO2_kg=("CO2_kg", "sum"),
                mean_CO2_kg=("CO2_kg", "mean"),
                mean_CO2_kg_per_km=("CO2_kg_per_km", "mean"),
                total_electricity_kWh=("electricity_kWh", "sum"),
                mean_electricity_kWh=("electricity_kWh", "mean"),
                mean_electricity_kWh_per_km=("electricity_kWh_per_km", "mean"),
            )
            .reset_index()
        )

        print("\n=== Logistics trucks by hub ===")
        print(hub_summary.to_string(index=False, float_format=lambda x: f"{x:,.3f}"))
    else:
        hub_summary = pd.DataFrame()
        print("\n(No logistics trucks found in this run.)")

    # -----------------------------------------------------
    # Save detailed tables for Excel
    # -----------------------------------------------------
    outputs_dir = Path("outputs")
    outputs_dir.mkdir(exist_ok=True)

    df.to_csv(outputs_dir / "vehicles_with_emissions.csv", index=False)
    trucks.to_csv(outputs_dir / "logistics_trucks_detailed.csv", index=False)
    group_summary.to_csv(outputs_dir / "summary_by_group.csv", index=False)
    group_powertrain_summary.to_csv(outputs_dir / "summary_by_group_powertrain.csv", index=False)
    if not hub_summary.empty:
        hub_summary.to_csv(outputs_dir / "summary_trucks_by_hub.csv", index=False)

    print("\nCSV files written to the 'outputs/' folder.")
    print("  - vehicles_with_emissions.csv")
    print("  - logistics_trucks_detailed.csv")
    print("  - summary_by_group.csv")
    print("  - summary_by_group_powertrain.csv")
    if not hub_summary.empty:
        print("  - summary_trucks_by_hub.csv")


if __name__ == "__main__":
    main()