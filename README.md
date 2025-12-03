You’re right, I hadn’t actually looked into the zip before — I do now. Here’s a **concise README** that matches your project structure and scripts:

````markdown
# SUMO Freight Simulation – 4 Scenario Study

This repository contains a SUMO-based case study with four scenarios:

- `Baseline/` – current road network, diesel trucks  
- `Roundabout/` – redesigned intersections / roundabouts  
- `50-50/` – mixed fleet, ~50% electric logistics trucks  
- `Full electrical fleet/` – 100% electric logistics trucks  

Each scenario is self-contained with its own network, demand, config and output folder.

---

## Running a Scenario

From the repository root:

```bash
cd "Baseline"              # or: "Roundabout", "50-50", "Full electrical fleet"
sumo -c cfg/base.sumocfg   # or: sumo-gui -c cfg/base.sumocfg
````

The config already points to the correct `net/`, `demand/` and `add/` files.

---

## Outputs

After running SUMO, XML outputs are written to the scenario’s `outputs/` folder, e.g.:

* `outputs/tripinfo.xml`
* `outputs/emissions.xml`
* `outputs/summary.xml`
* (EV scenarios also use `outputs/battery.xml`)

These files are the input for the Python data analysis.

---

## Python Analysis

Each scenario includes a Python script for processing the SUMO output:

```bash
cd "Baseline"              # or "Roundabout", "50-50", "Full electrical fleet"
python analyze_results.py
````

The script:

* reads `outputs/tripinfo.xml` and (if available) `battery.xml`
* computes travel, emissions and energy metrics
* writes CSV outputs such as:

  * `vehicles_with_emissions.csv`
  * `logistics_trucks_detailed.csv`
  * `summary_by_group.csv`
  * `summary_trucks_by_hub.csv`

### Python Dependencies

Only two libraries are required:

```python
import pandas as pd
from pathlib import Path
```

Install pandas if missing:

```bash
pip install pandas
```

If you want, I can shrink this even more for a “README-short.md” or adapt the text to match your seminar wording.
```
