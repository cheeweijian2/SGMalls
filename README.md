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
   `full_df.csv`. 

2. **`mall_transformation_pipeline.py`** (this repo's main script)
   - `build_amenity_summary()` — joins `full_df.csv` against every amenity
     source (postal-code merges for clinics/gyms/HDP outlets, a 200m spatial
     buffer join for bike racks, a name match for playgrounds) and produces
     a per-mall summary with amenity counts.
   - `apply_datamart_and_satisfy()` — merges in `datamart_summary.csv` and
     computes the `satisfy` flag (bike rack or playground, at least one gym,
     at least one HPB event, at least 3 HDP outlets).
   - Writes `mall_locations_summary2.csv`.

## Required input files

All expected under `DATA_DIR` (currently hardcoded at the top of
`mall_transformation_pipeline.py` — update this path for your machine):

| File | Source | Used for |
|---|---|---|
| `full_df.csv` | output of `openv5.py` | master mall list (name, postal, lat/lon, HPM) |
| `CHASClinics.geojson` | data.gov.sg | CHAS clinic locations |
| `GymsSGGEOJSON.geojson` | data.gov.sg | gym locations (geojson source) |
| `LTABicycleRackGEOJSON.geojson` | data.gov.sg | bike rack locations (spatial join, 200m) |
| `mall_playgrounds.csv` | manual/compiled | playground presence by mall name |
| `clean_hdp_FINAL.csv` | data.gov.sg + manually checked | Healthier Dining Partner outlets |
| `SportFacilities.csv` | data.gov.sg | gym/sports facilities (CSV source) |
| `clinic_df.csv` | data.gov.sg | polyclinic/PHPC clinic locations |
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
