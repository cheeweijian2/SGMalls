"""
Mall amenity data transformation pipeline.

Combines amenity merges + spatial joins (per-mall summary) with the
datamart merge + 'satisfy' flag computation (merging in
datamart_summary.csv and computing the 'satisfy' flag), which used to be
a separate step.

Upstream dependency (NOT included here, run separately):
  - openv5.py  -> produces full_df.csv via OneMap geocoding.
    Kept separate on purpose: it calls a live, paginated, rate-limited API
    and shouldn't be re-run every time the amenity/criteria logic changes.

Required input files (all under DATA_DIR):
  - full_df.csv                    (output of openv5.py)
  - CHASClinics.geojson
  - GymsSGGEOJSON.geojson
  - LTABicycleRackGEOJSON.geojson
  - mall_playgrounds.csv
  - clean_hdp_FINAL.csv
  - SportFacilities.csv
  - clinic_df.csv
  - datamart_summary.csv

Output:
  - mall_locations_summary.csv    (final per-mall summary with 'satisfy' flag)

Note: this no longer writes a separate pre-datamart-merge intermediate
summary or any debug side-files — everything is chained in memory and only
the final CSV (mall_locations_summary.csv) is written. If you still want
the intermediate amenity-summary CSV for debugging, uncomment the marked
line near the bottom of build_amenity_summary().
"""

import geopandas as gpd
import pandas as pd
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
DATA_DIR = "/Users/weijianchee/Downloads/Projects/intern/HPM_index/_data"
X_METERS = 200  # bike rack proximity threshold, in metres


# ---------------------------------------------------------------------------
# Shared geojson helpers (CHAS / gym / bike geojson sources only — the CSV
# sources don't need HTML parsing or a geometry column)
# ---------------------------------------------------------------------------
def parse_html_table(html_str):
    """Parse the ESRI-style HTML table stored in a geojson 'Description' field
    into a flat dict of {field_name: value}."""
    if not isinstance(html_str, str):
        return {}
    soup = BeautifulSoup(html_str, 'html.parser')
    data = {}
    for row in soup.find_all('tr'):
        th = row.find('th')
        td = row.find('td')
        if th and td:
            data[th.get_text(strip=True)] = td.get_text(strip=True)
    return data


def clean(df):
    """Expand the 'Description' HTML column into real columns, split geometry
    into longitude/latitude, and normalise any postal code column to a
    zero-padded 6-digit string."""
    parsed_data = df['Description'].apply(parse_html_table)
    extracted_df = pd.DataFrame(list(parsed_data))

    clean_gdf = pd.concat([df.drop(columns=['Description']), extracted_df], axis=1)
    clean_gdf['longitude'] = clean_gdf['geometry'].x
    clean_gdf['latitude'] = clean_gdf['geometry'].y
    clean_gdf = clean_gdf.drop(columns=['geometry'])

    postal_cols = ['POSTAL_CD', 'ADDRESSPOSTALCODE', 'POSTAL_CODE', 'PostalCode']
    for col in postal_cols:
        if col in clean_gdf.columns:
            clean_gdf[col] = (
                clean_gdf[col]
                .astype(str)
                .str.split('.').str[0]  # drop trailing '.0' from float parsing
                .str.zfill(6)            # pad back to 6 digits
            )

    print(f"There are {len(clean_gdf)} rows.")
    return clean_gdf


def build_amenity_summary():
    """Runs the full amenity merge/spatial-join pipeline and returns the
    per-mall summary DataFrame (indexed by mall_name)."""

    # -----------------------------------------------------------------
    # Mall reference data — the ONE source every merge/spatial join uses.
    # -----------------------------------------------------------------
    malls_df = pd.read_csv(f"{DATA_DIR}/full_df.csv", dtype={'postal': str})
    print(malls_df.dtypes)

    malls_df['postal'] = malls_df['postal'].astype(str)
    malls_df = malls_df.rename(columns={'name': 'mall_name'})

    check = malls_df[malls_df.duplicated(subset=['postal'], keep=False)]
    print("Malls sharing a postal code:")
    print(check)

    malls_for_merge = malls_df[['postal', 'mall_name']]

    # -----------------------------------------------------------------
    # GEOJSON SOURCES — CHAS clinics, gyms, and bicycle racks
    # -----------------------------------------------------------------
    chas_gdf = clean(gpd.read_file(f"{DATA_DIR}/CHASClinics.geojson")).rename(columns={'POSTAL_CD': 'postal'})
    gym_geo_gdf = clean(gpd.read_file(f"{DATA_DIR}/GymsSGGEOJSON.geojson")).rename(columns={'ADDRESSPOSTALCODE': 'postal'})
    bike_gdf = clean(gpd.read_file(f"{DATA_DIR}/LTABicycleRackGEOJSON.geojson"))

    chas_malls_mapped = pd.merge(chas_gdf, malls_for_merge, how='left', on='postal')
    mapped_chas_clinics = chas_malls_mapped[chas_malls_mapped['mall_name'].notnull()].copy()
    print(f"{len(mapped_chas_clinics)}/{len(chas_gdf)} CHAS clinics mapped to malls")

    gym_geo_malls_mapped = pd.merge(gym_geo_gdf, malls_for_merge, how='left', on='postal')
    mapped_gyms_geo = gym_geo_malls_mapped[gym_geo_malls_mapped['mall_name'].notnull()].copy()
    print(f"{len(mapped_gyms_geo)}/{len(gym_geo_gdf)} geojson gyms mapped to malls")

    # Bike racks get a proximity flag (nearest X metres) rather than an exact
    # postal-code match, since racks usually sit just outside a mall's own
    # postal boundary.
    bike_spatial = gpd.GeoDataFrame(
        bike_gdf, geometry=gpd.points_from_xy(bike_gdf['longitude'], bike_gdf['latitude']), crs="EPSG:4326"
    ).to_crs(epsg=3414)

    malls_spatial = gpd.GeoDataFrame(
        malls_df, geometry=gpd.points_from_xy(malls_df['longitude'], malls_df['latitude']), crs="EPSG:4326"
    ).to_crs(epsg=3414)

    malls_buffered = malls_spatial.copy()
    malls_buffered['geometry'] = malls_buffered.geometry.buffer(X_METERS)

    joined = gpd.sjoin(malls_buffered, bike_spatial, how="left", predicate="intersects")
    match_counts = joined.groupby(joined.index)['index_right'].count()
    malls_df['has_bike_rack'] = (match_counts > 0).reindex(malls_df.index, fill_value=False).astype(int)
    print(f"{malls_df['has_bike_rack'].sum()} malls have a bike rack within {X_METERS}m")

    # -----------------------------------------------------------------
    # Playgrounds — matched directly on mall name (no postal/geometry
    # available), so this doesn't go through the postal merge or
    # bike-rack-style spatial join.
    # -----------------------------------------------------------------
    playground_df = pd.read_csv(f"{DATA_DIR}/mall_playgrounds.csv")
    playground_df = playground_df.rename(columns={'Mall / Location': 'mall_name', 'Playground': 'has_playground'})

    unmatched_playgrounds = playground_df[~playground_df['mall_name'].isin(malls_df['mall_name'])]
    if len(unmatched_playgrounds):
        print("Playground entries with no matching mall_name in malls_df (flag will be dropped for these):")
        print(unmatched_playgrounds['mall_name'].tolist())

    playground_by_mall = (
        playground_df[playground_df['mall_name'].isin(malls_df['mall_name'])]
        .drop_duplicates('mall_name')
        .set_index('mall_name')['has_playground']
    )
    print(f"{len(playground_by_mall)} malls matched to a playground flag")

    # -----------------------------------------------------------------
    # CSV SOURCES — HDP outlets, sports facilities, PHPC clinics
    # -----------------------------------------------------------------

    # --- Healthier Dining Partner (HDP) outlets ---
    hdp_df = pd.read_csv(f"{DATA_DIR}/clean_hdp_FINAL.csv", dtype={'postal': str})
    hdp_df['postal'] = hdp_df['postal'].astype(str).str.zfill(6)
    print(f"{len(hdp_df)} initial HDP outlets")
    hdp_df = hdp_df.drop_duplicates()
    print(f"{len(hdp_df)} unique HDP outlets")

    print(f"Total null rows: {hdp_df['postal'].isnull().sum()}")
    hdp_df_clean = hdp_df[hdp_df['postal'].notnull()]

    stores_malls_mapped = pd.merge(hdp_df_clean, malls_for_merge, how='left', on='postal')
    mapped_stores = stores_malls_mapped[stores_malls_mapped['mall_name'].notnull()].copy()
    print(f"{len(mapped_stores)}/{len(hdp_df)} HDP stores mapped to malls\n")

    # --- Sports facilities (CSV) ---
    gym_csv_df = pd.read_csv(f"{DATA_DIR}/SportFacilities.csv", dtype={"PostalCode": str})
    gym_csv_df = gym_csv_df.rename(columns={"PostalCode": "postal"})
    print(f"{len(gym_csv_df)} initial sports facilities")
    gym_csv_df = gym_csv_df.drop_duplicates()
    print(f"Total null rows: {gym_csv_df['postal'].isnull().sum()}")
    gym_csv_df = gym_csv_df[gym_csv_df['postal'].notnull()]
    print(f"{len(gym_csv_df)} unique sports facilities")

    gyms_csv_malls_mapped = pd.merge(gym_csv_df, malls_for_merge, how='left', on='postal')
    mapped_gyms_csv = gyms_csv_malls_mapped[gyms_csv_malls_mapped['mall_name'].notnull()].copy()
    print(f"{len(mapped_gyms_csv)}/{len(gym_csv_df)} CSV sports facilities mapped to malls\n")

    # --- PHPC clinics ---
    clinic_df = pd.read_csv(f"{DATA_DIR}/clinic_df.csv")
    clinic_df['postal'] = clinic_df['postal'].astype(str).str.zfill(6)

    print(f"{len(clinic_df)} initial PHPC clinics")
    print(f"Total null rows: {clinic_df['postal'].isnull().sum()}")

    clinic_df = clinic_df[clinic_df['postal'].notnull()]
    clinic_malls_mapped = pd.merge(clinic_df, malls_for_merge, how='left', on='postal')
    mapped_clinics_phpc = clinic_malls_mapped[clinic_malls_mapped['mall_name'].notnull()].copy()
    print(f"{len(mapped_clinics_phpc)}/{len(clinic_df)} PHPC clinics mapped to malls\n")

    # -----------------------------------------------------------------
    # Combined per-mall summary across all five countable amenity sources
    # + the bike rack proximity flag
    # -----------------------------------------------------------------
    mapped_stores['category'] = 'HDP Outlet'
    mapped_gyms_csv['category'] = 'Gym/Sports (CSV)'
    mapped_clinics_phpc['category'] = 'Clinic (PHPC)'
    mapped_chas_clinics['category'] = 'Clinic (CHAS)'
    mapped_gyms_geo['category'] = 'Gym (GeoJSON)'

    all_mapped = pd.concat([
        mapped_stores[['mall_name', 'category']],
        mapped_gyms_csv[['mall_name', 'category']],
        mapped_clinics_phpc[['mall_name', 'category']],
        mapped_chas_clinics[['mall_name', 'category']],
        mapped_gyms_geo[['mall_name', 'category']],
    ], ignore_index=True)

    mall_summary = pd.crosstab(all_mapped['mall_name'], all_mapped['category'])
    mall_summary['Total'] = mall_summary.sum(axis=1)

    # Crosstab only keeps malls with >=1 mapped amenity — reindex against
    # the full mall list so malls with zero matches still appear (as
    # all-zero rows)
    mall_summary = mall_summary.reindex(malls_df['mall_name'].unique(), fill_value=0)
    mall_summary.index.name = 'mall_name'

    bike_by_mall = malls_df[['mall_name', 'has_bike_rack']].drop_duplicates('mall_name').set_index('mall_name')['has_bike_rack']
    mall_summary['has_bike_rack'] = bike_by_mall.reindex(mall_summary.index).fillna(0).astype(int)

    mall_summary['has_playground'] = playground_by_mall.reindex(mall_summary.index).fillna(0).astype(int)

    # Bring lat/lon/HPM back in (dropped by the crosstab) so the dashboard
    # can plot malls on a map
    coords_by_mall = malls_df[['mall_name', 'longitude', 'latitude', 'HPM']].drop_duplicates('mall_name').set_index('mall_name')
    mall_summary = mall_summary.join(coords_by_mall, how='left')

    print(mall_summary)

    mall_summary = mall_summary.sort_values(by='Total', ascending=False)

    print(f"""
{len(mapped_stores)} HDP stores out of {len(hdp_df)} mapped.
{len(mapped_gyms_csv)} CSV gyms/sports facilities out of {len(gym_csv_df)} mapped.
{len(mapped_clinics_phpc)} PHPC clinics out of {len(clinic_df)} mapped.
{len(mapped_chas_clinics)} CHAS clinics out of {len(chas_gdf)} mapped.
{len(mapped_gyms_geo)} geojson gyms out of {len(gym_geo_gdf)} mapped.
{malls_df['has_bike_rack'].sum()} malls have a bike rack within {X_METERS}m.
""")

    print("Per-mall amenity summary:")
    print(mall_summary.to_string())

    # Uncomment if you want the old intermediate file for debugging:
    # mall_summary.to_csv("mall_locations_summary.csv")

    return mall_summary.reset_index()


def apply_datamart_and_satisfy(mall_locations_summary):
    """Merges in datamart_summary.csv and computes the final 'satisfy' flag."""

    mall_datamart_summary = pd.read_csv(f"{DATA_DIR}/datamart_summary.csv")
    mall_datamart_summary = mall_datamart_summary.rename(columns={'MALL_NAME': 'mall_name'}).drop(
        columns=['HPM', 'LAT', 'LNG']
    )

    total_summary = pd.merge(mall_locations_summary, mall_datamart_summary, how="left", on='mall_name')

    # ==================== SATISFY CURRENT CRITERIA =========================
    # Combine gym counts from both CSV and GeoJSON sources
    total_gyms = total_summary['Gym/Sports (CSV)'] + total_summary['Gym (GeoJSON)']

    # bike rack and playground same category
    has_bike_or_playground = (total_summary['has_bike_rack'] >= 1) | (total_summary['has_playground'] >= 1)

    # Set 'satisfy' to 1 if has_bike_or_playground, total gyms >= 1,
    # HPB events count >= 1 and HDP >= 3, else 0
    total_summary['satisfy'] = (
        has_bike_or_playground &
        (total_gyms >= 1) &
        (total_summary['hpb_event_count'] >= 1) &
        # (total_summary['supermarket_outlet_count'] >= 1) &
        (total_summary['HDP Outlet'] >= 3)
    ).astype(int)
    # =========================================================================

    return total_summary


mall_locations_summary = build_amenity_summary()
total_summary = apply_datamart_and_satisfy(mall_locations_summary)

total_summary.to_csv("mall_locations_summary.csv", index=False)

with pd.option_context('display.max_columns', None, 'display.width', 1000):
    print(total_summary)
