# Mall Amenity Data Pipeline

Builds a per-mall amenity summary for Singapore shopping malls (clinics, gyms,
bike racks, playgrounds, healthier-dining outlets) and flags which malls
satisfy the current criteria set.

## Pipeline order

```
openv5.py  ->  full_df.csv  ->  mall_transformation_pipeline.py  ->  mall_locations_summary2.csv
```

1. **`openv5.py`** (run separately, first)
   Geocodes the raw mall name list via the OneMap API (with pagination and a
   manual override table for known multi-postal-code malls) and writes
   `full_df.csv`. This step is kept out of the combined script on purpose —
   it hits a live, rate-limited external API and shouldn't be re-run just to
   tweak a merge or a threshold downstream.

2. **`mall_transformation_pipeline.py`** (this repo's main script)
   - `build_amenity_summary()` — joins `full_df.csv` against every amenity
     source (postal-code merges for clinics/gyms/HDP outlets, a 200m spatial
     buffer join for bike racks, a name match for playgrounds) and produces
     a per-mall summary with amenity counts.
   - `apply_datamart_and_satisfy()` — merges in `datamart_summary.csv` and
     computes the `satisfy` flag (bike rack or playground, at least one gym,
     at least one HPB event, at least 3 HDP outlets).
   - Runs top-level (no `main()` guard) and writes `mall_locations_summary2.csv`.

## Required input files

All expected under `DATA_DIR` (currently hardcoded at the top of
`mall_transformation_pipeline.py` — update this path for your machine):

| File | Source | Used for |
|---|---|---|
| `full_df.csv` | output of `openv5.py` | master mall list (name, postal, lat/lon, HPM) |
| `CHASClinics.geojson` | data.gov.sg | CHAS clinic locations |
| `GymsSGGEOJSON.geojson` | data.gov.sg | gym locations (geojson source) |
| `LTABicycleRackGEOJSON.geojson` | LTA | bike rack locations (spatial join, 200m) |
| `mall_playgrounds.csv` | manual/compiled | playground presence by mall name |
| `clean_hdp_FINAL.csv` | HPB | Healthier Dining Partner outlets |
| `SportFacilities.csv` | manual/compiled | gym/sports facilities (CSV source) |
| `clinic_df.csv` | PHPC | polyclinic/PHPC clinic locations |
| `datamart_summary.csv` | internal datamart | HPB event counts, supermarket counts, etc. |

## Output

- **`mall_locations_summary2.csv`** — one row per mall with amenity counts by
  category, `has_bike_rack`, `has_playground`, coordinates, HPM flag,
  datamart fields, and the final `satisfy` column.

## Notes

- Postal codes are normalized to zero-padded 6-digit strings throughout.
- Bike racks are matched by proximity (200m buffer, SVY21/EPSG:3414), not
  exact postal code, since racks usually sit just outside a mall's own
  postal boundary.
- The `satisfy` criteria currently checks: (bike rack OR playground) AND
  (≥1 gym, CSV + geojson combined) AND (≥1 HPB event) AND (≥3 HDP outlets).
  The `supermarket_count` condition is present in the code but commented out.
- No intermediate CSVs (old `mall_locations_summary.csv`, debug dirty-row
  files) are written — everything is chained in memory in a single run.
