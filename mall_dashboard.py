import json
from pathlib import Path

import altair as alt
import numpy as np
import pandas as pd
import pydeck as pdk
import streamlit as st
from pyproj import Transformer

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
st.set_page_config(page_title="Mall Amenities Dashboard", layout="wide")

@st.cache_data
def load_summary(path):
    df = pd.read_csv(path)
    # Strip whitespace from column headers to prevent KeyError
    df.columns = df.columns.str.strip()
    df = df.rename(columns={df.columns[0]: "mall_name"})
    return df

# Resolve relative to this script (not a local machine path) so it works
# the same way here and once deployed — _data/LTABicycleRackGEOJSON.geojson
# is committed alongside this file in the repo.
APP_DIR = Path(__file__).resolve().parent
BIKE_RACK_GEOJSON_PATH = APP_DIR / "_data" / "LTABicycleRackGEOJSON.geojson"

_TO_SVY21 = Transformer.from_crs("EPSG:4326", "EPSG:3414", always_xy=True)


@st.cache_data
def load_bike_rack_points(path):
    """Loads raw bike rack point coordinates and projects them to SVY21
    (metres) once. Recomputing has_bike_rack for a new slider value is then
    just a distance check against these cached points, not a re-parse of
    the geojson."""
    with open(path) as f:
        gj = json.load(f)
    lonlat = np.array([feat["geometry"]["coordinates"][:2] for feat in gj["features"]])
    x, y = _TO_SVY21.transform(lonlat[:, 0], lonlat[:, 1])
    return x, y


def compute_has_bike_rack(mall_lon, mall_lat, bike_x, bike_y, distance_m):
    """Recomputes has_bike_rack (0/1) per mall for an arbitrary distance
    threshold, in metres. Equivalent to the buffer + spatial-join in the
    archived mall_pipeline_lib.py's compute_bike_rack_flags(), done here as
    a min-distance check so it's cheap enough to run on every slider move."""
    mall_x, mall_y = _TO_SVY21.transform(mall_lon.to_numpy(), mall_lat.to_numpy())
    dx = mall_x[:, None] - bike_x[None, :]
    dy = mall_y[:, None] - bike_y[None, :]
    dist = np.sqrt(dx**2 + dy**2)
    return (dist.min(axis=1) <= distance_m).astype(int)


def recompute_satisfy(df):
    """Re-applies the 'satisfy' criteria using whatever has_bike_rack is
    currently in df. Same formula as apply_datamart_and_satisfy() in the
    archived mall_pipeline_lib.py (supermarket_count condition included
    there but commented out, kept consistent here)."""
    has_bike_or_playground = (df["has_bike_rack"] >= 1) | (df["has_playground"] >= 1)
    total_gyms = df["Gym/Sports (CSV)"] + df["Gym (GeoJSON)"]
    return (
        has_bike_or_playground
        & (total_gyms >= 1)
        & (df["hpb_event_count"] >= 1)
        & (df["HDP Outlet"] >= 3)
    ).astype(int)


st.title("🏬 Mall Amenities Dashboard")
st.caption("HDP outlets, gyms, clinics, and bike rack proximity across Singapore malls.")

# ---------------------------------------------------------------------------
# Data source — mall_locations_summary.csv is committed directly in this
# (private) repo, right alongside this script. See
# mall_transformation_pipeline.py in the main repo for how it's produced.
# ---------------------------------------------------------------------------
summary_file = st.file_uploader("Upload CSV", type="csv")
butt = st.checkbox("Use sample data")

DEFAULT_SUMMARY_PATH = APP_DIR / "mall_locations_summary.csv"
if butt:
    summary_file = DEFAULT_SUMMARY_PATH

if summary_file is None:
    st.info("Upload a CSV to get started.")
    st.stop()

summary = load_summary(summary_file)

# ---------------------------------------------------------------------------
# Bike rack distance (dynamic) — recomputes has_bike_rack + satisfy live.
# Does not change mall_locations_summary.csv on disk in any way — only
# what this dashboard session shows.
# ---------------------------------------------------------------------------
st.sidebar.header("Bike rack proximity")
bike_distance_m = st.sidebar.slider(
    "Bike rack distance threshold (m)", 0, 1000, 200, step=10,
    help="How close a bike rack must be to a mall to count as 'has a bike rack'. "
    "Recomputes has_bike_rack and the satisfy criteria live for this session only."
)
try:
    bike_x, bike_y = load_bike_rack_points(BIKE_RACK_GEOJSON_PATH)
    summary["has_bike_rack"] = compute_has_bike_rack(
        summary["longitude"], summary["latitude"], bike_x, bike_y, bike_distance_m
    )
    summary["satisfy"] = recompute_satisfy(summary)
except FileNotFoundError:
    st.sidebar.warning(
        "Couldn't find LTABicycleRackGEOJSON.geojson in _data — showing "
        "has_bike_rack / satisfy as-is from the loaded data instead."
    )

# ---------------------------------------------------------------------------
# Sidebar filters
# ---------------------------------------------------------------------------
st.sidebar.header("Filters")
search = st.sidebar.text_input("Search mall name")
current_hpm_only = st.sidebar.checkbox("Show current HPMs")
satisfy_current = st.sidebar.checkbox("Show malls that satisfy current HPM criteria")
st.sidebar.info("To satisfy current HPM criteria, the mall must have at least: \n - 1 Fitness facility \n - Have bike racks or playgrounds \n - At least 3 HDP (Healthier Dining Programme) outlets. \n\n"
"Note: NOT ALL data are available, so current HPM malls might show up as not satisfying current HPM criteria.")

filtered = summary.copy()
if search:
    filtered = filtered[filtered["mall_name"].str.contains(search, case=False, na=False)]
if current_hpm_only:
    filtered = filtered[filtered["HPM"] == 1]
if satisfy_current:
    filtered = filtered[filtered["satisfy"] == 1]

# ---------------------------------------------------------------------------
# KPIs
# ---------------------------------------------------------------------------
col1, col2, col3, col4 = st.columns(4)
col1.metric("Malls shown", len(filtered))
col2.metric("Current HPM Malls", len(filtered[filtered["HPM"] == 1]))

# ---------------------------------------------------------------------------
# Mall Readiness Score
# ---------------------------------------------------------------------------
st.divider()
st.subheader("Proposed HPM Index")
st.latex(r"""
\text{HPM Index} = 
\left( \frac{\text{No. of Cat A}}{\text{Max No. of A}} \times Weight_A \right) + 
\left( \frac{\text{No. of Cat B}}{\text{Max No. of B}} \times Weight_B \right) + 
\left( \frac{\text{No. of Cat D}}{\text{Max No. of D}} \times Weight_D \right)
""")
st.space("medium")

scored = filtered.copy()
scored["cat_a_count"] = scored["HDP Outlet"]
scored["cat_b_count"] = scored["Gym (GeoJSON)"] + scored["has_bike_rack"] + scored["Gym/Sports (CSV)"]
scored["cat_d_count"] = scored["Clinic (PHPC)"] + scored["Clinic (CHAS)"]

max_cat_a = max(scored["cat_a_count"].max(), 1)
max_cat_b = max(scored["cat_b_count"].max(), 1)
max_cat_d = max(scored["cat_d_count"].max(), 1)

# Sliders with callbacks
if "w_cat_a" not in st.session_state:
    st.session_state.w_cat_a = 35
if "w_cat_b" not in st.session_state:
    st.session_state.w_cat_b = 35
if "w_cat_d" not in st.session_state:
    st.session_state.w_cat_d = 30

def update_a():
    rem = 100 - st.session_state.w_cat_a
    other_sum = st.session_state.w_cat_b + st.session_state.w_cat_d
    if other_sum > 0:
        st.session_state.w_cat_b = int(round(rem * (st.session_state.w_cat_b / other_sum)))
        st.session_state.w_cat_d = rem - st.session_state.w_cat_b
    else:
        st.session_state.w_cat_b = rem // 2
        st.session_state.w_cat_d = rem - st.session_state.w_cat_b

def update_b():
    rem = 100 - st.session_state.w_cat_b
    other_sum = st.session_state.w_cat_a + st.session_state.w_cat_d
    if other_sum > 0:
        st.session_state.w_cat_a = int(round(rem * (st.session_state.w_cat_a / other_sum)))
        st.session_state.w_cat_d = rem - st.session_state.w_cat_a
    else:
        st.session_state.w_cat_a = rem // 2
        st.session_state.w_cat_d = rem - st.session_state.w_cat_a

def update_d():
    rem = 100 - st.session_state.w_cat_d
    other_sum = st.session_state.w_cat_a + st.session_state.w_cat_b
    if other_sum > 0:
        st.session_state.w_cat_a = int(round(rem * (st.session_state.w_cat_a / other_sum)))
        st.session_state.w_cat_b = rem - st.session_state.w_cat_a
    else:
        st.session_state.w_cat_a = rem // 2
        st.session_state.w_cat_b = rem - st.session_state.w_cat_a

w_col1, w_col2, w_col3 = st.columns(3)
w_cat_a = w_col1.slider("Category A: Healthy Dining Ecosystem (%)", 0, 100, key="w_cat_a", on_change=update_a)
w_cat_b = w_col2.slider("Category B: Active Living Infrastructure (%)", 0, 100, key="w_cat_b", on_change=update_b)
w_cat_d = w_col3.slider("Category D: Healthcare Ecosystem (%)", 0, 100, key="w_cat_d", on_change=update_d)

scored["cat_a_score"] = (scored["cat_a_count"] / max_cat_a) * w_cat_a
scored["cat_b_score"] = (scored["cat_b_count"] / max_cat_b) * w_cat_b
scored["cat_d_score"] = (scored["cat_d_count"] / max_cat_d) * w_cat_d
scored["readiness_score"] = scored["cat_a_score"] + scored["cat_b_score"] + scored["cat_d_score"]

score_top_n = st.slider("Show top N by HPM Index", 5, 50, 15, key="score_top_n")
top_scored = scored.sort_values("readiness_score", ascending=False).head(score_top_n)

top_scored_long = top_scored.melt(
    id_vars=["mall_name", "readiness_score"],
    value_vars=["cat_a_score", "cat_b_score", "cat_d_score"],
    var_name="category",
    value_name="score_contribution"
)

category_labels = {
    "cat_a_score": "Cat A: Healthy Dining Ecosystem",
    "cat_b_score": "Cat B: Active Living Infrastructure",
    "cat_d_score": "Cat D: Healthcare Ecosystem"
}
top_scored_long["category"] = top_scored_long["category"].map(category_labels)

score_chart = (
    alt.Chart(top_scored_long)
    .mark_bar()
    .encode(
        x=alt.X("mall_name:N", sort=top_scored["mall_name"].tolist(), title="Mall"),
        y=alt.Y("score_contribution:Q", title="HPM Index (0-100)"),
        color=alt.Color(
            "category:N", 
            title="Category Component",
            scale=alt.Scale(
                domain=["Cat A: Healthy Dining Ecosystem", "Cat B: Active Living Infrastructure", "Cat D: Healthcare Ecosystem"],
                range=["#ff7f00", "#4daf4a", "#377eb8"]  # Orange, Green, Blue
            )
        ),
        tooltip=[
            alt.Tooltip("mall_name:N", title="Mall"),
            alt.Tooltip("category:N", title="Category"),
            alt.Tooltip("score_contribution:Q", title="Category Score", format=".1f"),
            alt.Tooltip("readiness_score:Q", title="HPM Index", format=".1f")
        ]
    )
)

st.altair_chart(score_chart, width="stretch")

st.dataframe(
    top_scored[
        ["mall_name", "readiness_score", "cat_a_score", "cat_b_score", "cat_d_score", "HPM"]
    ].rename(columns={
        "readiness_score": "HPM Index",
        "cat_a_score": "Cat A Score",
        "cat_b_score": "Cat B Score",
        "cat_d_score": "Cat D Score"
    }),
    width="stretch",
    hide_index=True,
)

# ---------------------------------------------------------------------------
# Map — every mall in view, sized/coloured by criteria & HPM status
# ---------------------------------------------------------------------------
st.divider()
st.subheader("Malls map")
map_df = filtered.dropna(subset=["latitude", "longitude"]).copy()

if map_df.empty:
    st.info("No malls with coordinates in the current filter.")
else:
    col1, col2 = st.columns(2)
    size = col1.slider("Size slider", 0, 1000, 330)
    map_df["radius"] = size
    map_df["color"] = map_df["satisfy"].apply(
        lambda satisfy: [0, 255, 0, 200] if satisfy else [255, 0, 0, 160]
    )
    map_df["line_color"] = map_df["HPM"].apply(lambda hpm: [255, 215, 0, 255] if hpm == 1 else [0, 0, 0, 0])
    map_df["line_width"] = map_df["HPM"].apply(lambda hpm: 40 if hpm == 1 else 0)

    layer = pdk.Layer(
        "ScatterplotLayer",
        data=map_df,
        get_position="[longitude, latitude]",
        get_radius="radius",
        get_fill_color="color",
        get_line_color="line_color",
        get_line_width="line_width",
        stroked=True,
        pickable=True,
    )
    view_state = pdk.ViewState(
        latitude=map_df["latitude"].mean(),
        longitude=map_df["longitude"].mean(),
        zoom=10.5,
    )
    tooltip = {"text": "{mall_name}\nTotal amenities: {Total}\nHPM: {HPM}"}
    st.pydeck_chart(
        pdk.Deck(layers=[layer], initial_view_state=view_state, tooltip=tooltip),
        width="stretch"
    )
    st.caption(
        ":green[Green] = satisfies current HPM criteria, :red[Red] = does not satisfy current HPM criteria. "
        ":color[Gold]{foreground='rgb(255, 215, 0)'} ring = current HPM mall."
    )
