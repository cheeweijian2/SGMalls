"""
Mall Amenities Dashboard.

"""

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
st.set_page_config(page_title="Mall Amenities Dashboard v3", layout="wide")

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
    threshold, in metres. Equivalent to a buffer + spatial-join against bike
    rack locations, done here as a min-distance check so it's cheap enough
    to run on every slider move."""
    mall_x, mall_y = _TO_SVY21.transform(mall_lon.to_numpy(), mall_lat.to_numpy())
    dx = mall_x[:, None] - bike_x[None, :]
    dy = mall_y[:, None] - bike_y[None, :]
    dist = np.sqrt(dx**2 + dy**2)
    return (dist.min(axis=1) <= distance_m).astype(int)


# Defaults reproduce a close match to the original pipeline's satisfy-flag
# criteria. A mall must satisfy all three groups below; within the third
# group, meeting either listed condition is enough:
#   - bike rack / playground
#   - HPB events (on its own)
#   - HDP outlets / supermarkets
DEFAULT_INCLUDE_BIKE_RACK = True
DEFAULT_INCLUDE_PLAYGROUND = True
DEFAULT_MIN_HPB_EVENTS = 1
DEFAULT_MIN_HDP_OUTLETS = 3
DEFAULT_MIN_SUPERMARKET = 1


def compute_satisfy_groups(
    df,
    include_bike_rack=DEFAULT_INCLUDE_BIKE_RACK,
    include_playground=DEFAULT_INCLUDE_PLAYGROUND,
    min_hpb_events=DEFAULT_MIN_HPB_EVENTS,
    min_hdp_outlets=DEFAULT_MIN_HDP_OUTLETS,
    min_supermarket=DEFAULT_MIN_SUPERMARKET,
):
    """The three satisfy-criteria groups, as individual boolean Series
    rather than one combined flag — shared by recompute_satisfy() (which
    just ANDs them together) and by anything that wants to show *which*
    group(s) a mall is failing. A mall must meet all three groups; the
    third group is met by either of its listed conditions:
      - bike rack (if include_bike_rack), or playground (if
        include_playground) — if neither is enabled, this group is treated
        as satisfied automatically (the check is effectively switched off)
      - hpb_event_count >= min_hpb_events (on its own)
      - HDP outlets >= min_hdp_outlets, or supermarket_count >=
        min_supermarket (only if that column is loaded)
    """
    if not include_bike_rack and not include_playground:
        active_living_ok = pd.Series(True, index=df.index)
    else:
        active_living_ok = pd.Series(False, index=df.index)
        if include_bike_rack:
            active_living_ok = active_living_ok | (df["has_bike_rack"] >= 1)
        if include_playground:
            active_living_ok = active_living_ok | (df["has_playground"] >= 1)

    events_ok = df["hpb_event_count"] >= min_hpb_events

    retail_dining_ok = df["HDP Outlet"] >= min_hdp_outlets
    if "supermarket_count" in df.columns:
        retail_dining_ok = retail_dining_ok | (df["supermarket_count"] >= min_supermarket)

    return active_living_ok, events_ok, retail_dining_ok


def recompute_satisfy(
    df,
    include_bike_rack=DEFAULT_INCLUDE_BIKE_RACK,
    include_playground=DEFAULT_INCLUDE_PLAYGROUND,
    min_hpb_events=DEFAULT_MIN_HPB_EVENTS,
    min_hdp_outlets=DEFAULT_MIN_HDP_OUTLETS,
    min_supermarket=DEFAULT_MIN_SUPERMARKET,
):
    """Re-applies the 'satisfy' criteria using whatever has_bike_rack is
    currently in df, with every threshold configurable instead of hardcoded.
    Gyms/sports facilities are not part of this criteria at all (removed by
    request). See compute_satisfy_groups() for what the three groups are —
    a mall must meet all three."""
    active_living_ok, events_ok, retail_dining_ok = compute_satisfy_groups(
        df,
        include_bike_rack=include_bike_rack,
        include_playground=include_playground,
        min_hpb_events=min_hpb_events,
        min_hdp_outlets=min_hdp_outlets,
        min_supermarket=min_supermarket,
    )
    return (active_living_ok & events_ok & retail_dining_ok).astype(int)


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
bike_ok = True
try:
    bike_x, bike_y = load_bike_rack_points(BIKE_RACK_GEOJSON_PATH)
    summary["has_bike_rack"] = compute_has_bike_rack(
        summary["longitude"], summary["latitude"], bike_x, bike_y, bike_distance_m
    )
except FileNotFoundError:
    bike_ok = False
    st.sidebar.warning(
        "Couldn't find LTABicycleRackGEOJSON.geojson in _data — showing "
        "has_bike_rack as-is from the loaded data instead."
    )

# ---------------------------------------------------------------------------
# Satisfy criteria (configurable) — see recompute_satisfy() above for exactly
# how these combine.
# ---------------------------------------------------------------------------
st.sidebar.header("Satisfy criteria")

if "satisfy_include_bike_rack" not in st.session_state:
    st.session_state["satisfy_include_bike_rack"] = DEFAULT_INCLUDE_BIKE_RACK
if "satisfy_include_playground" not in st.session_state:
    st.session_state["satisfy_include_playground"] = DEFAULT_INCLUDE_PLAYGROUND
if "satisfy_min_hpb" not in st.session_state:
    st.session_state["satisfy_min_hpb"] = DEFAULT_MIN_HPB_EVENTS
if "satisfy_min_hdp" not in st.session_state:
    st.session_state["satisfy_min_hdp"] = DEFAULT_MIN_HDP_OUTLETS
if "satisfy_min_supermarket" not in st.session_state:
    st.session_state["satisfy_min_supermarket"] = DEFAULT_MIN_SUPERMARKET

def _reset_satisfy_criteria():
    st.session_state["satisfy_include_bike_rack"] = DEFAULT_INCLUDE_BIKE_RACK
    st.session_state["satisfy_include_playground"] = DEFAULT_INCLUDE_PLAYGROUND
    st.session_state["satisfy_min_hpb"] = DEFAULT_MIN_HPB_EVENTS
    st.session_state["satisfy_min_hdp"] = DEFAULT_MIN_HDP_OUTLETS
    st.session_state["satisfy_min_supermarket"] = DEFAULT_MIN_SUPERMARKET

st.sidebar.button("Reset to default criteria", on_click=_reset_satisfy_criteria)

satisfy_include_bike_rack = st.sidebar.checkbox(
    "Bike rack nearby counts", key="satisfy_include_bike_rack"
)
satisfy_include_playground = st.sidebar.checkbox(
    "Playground counts", key="satisfy_include_playground"
)
satisfy_min_hpb = st.sidebar.slider(
    "Minimum HPB events", 0, 10, key="satisfy_min_hpb"
)

has_supermarket_data = "supermarket_count" in summary.columns
satisfy_min_hdp = st.sidebar.slider(
    "Minimum HDP outlets", 0, 10, key="satisfy_min_hdp"
)
if has_supermarket_data:
    satisfy_min_supermarket = st.sidebar.slider(
        "...or minimum supermarkets", 0, 5, key="satisfy_min_supermarket"
    )
else:
    satisfy_min_supermarket = DEFAULT_MIN_SUPERMARKET
    st.sidebar.caption(
        "(Supermarket count isn't available in the loaded data, so only "
        "HDP outlets count here.)"
    )

# Recomputed regardless of whether the bike geojson loaded above (bike_ok) —
# if it failed, has_bike_rack just falls back to whatever was already in the
# loaded CSV, and satisfy still reflects the criteria chosen below.
summary["satisfy"] = recompute_satisfy(
    summary,
    include_bike_rack=satisfy_include_bike_rack,
    include_playground=satisfy_include_playground,
    min_hpb_events=satisfy_min_hpb,
    min_hdp_outlets=satisfy_min_hdp,
    min_supermarket=satisfy_min_supermarket,
)

def _describe_satisfy_criteria():
    active_living_parts = []
    if satisfy_include_bike_rack:
        active_living_parts.append("a bike rack nearby")
    if satisfy_include_playground:
        active_living_parts.append("a playground")
    lines = []
    if active_living_parts:
        lines.append(", or ".join(active_living_parts))
    lines.append(f"At least {satisfy_min_hpb} HPB event{'s' if satisfy_min_hpb != 1 else ''}")
    if has_supermarket_data:
        lines.append(
            f"At least {satisfy_min_hdp} HDP outlet{'s' if satisfy_min_hdp != 1 else ''}, "
            f"or at least {satisfy_min_supermarket} supermarket{'s' if satisfy_min_supermarket != 1 else ''}"
        )
    else:
        lines.append(
            f"At least {satisfy_min_hdp} HDP (Healthier Dining Programme) "
            f"outlet{'s' if satisfy_min_hdp != 1 else ''}"
        )
    return lines

# ---------------------------------------------------------------------------
# Sidebar filters
# ---------------------------------------------------------------------------
st.sidebar.header("Filters")
search = st.sidebar.text_input("Search mall name")
current_hpm_only = st.sidebar.checkbox("Show current HPMs")
satisfy_current = st.sidebar.checkbox("Show malls that satisfy current HPM criteria")
_criteria_lines = "".join(f" \n - {line}" for line in _describe_satisfy_criteria())
st.sidebar.info(f"To satisfy current HPM criteria, the mall must meet ALL of the following (each can be met in more than one way):{_criteria_lines} \n\n"
"Note: NOT ALL data are available, so current HPM malls might show up as not satisfying current HPM criteria.")

filtered = summary.copy()
if search:
    filtered = filtered[filtered["mall_name"].str.contains(search, case=False, na=False)]
if current_hpm_only:
    filtered = filtered[filtered["HPM"] == 1]
if satisfy_current:
    filtered = filtered[filtered["satisfy"] == 1]

# ---------------------------------------------------------------------------
# KPIs — shared across both tabs below
# ---------------------------------------------------------------------------
col1, col2, col3, col4 = st.columns(4)
col1.metric("Malls shown", len(filtered))
col2.metric("Current HPM Malls", len(filtered[filtered["HPM"] == 1]))
col3.metric("Malls satisfying criteria", int(filtered["satisfy"].sum()))
col4.metric(
    "% satisfying criteria",
    f"{(filtered['satisfy'].mean() * 100 if len(filtered) else 0):.0f}%",
)

# ---------------------------------------------------------------------------
# Tabs — the two things that used to be separate scripts. Everything above
# this point (data load, criteria, filters, KPIs) and the map below it are
# shared by both.
# ---------------------------------------------------------------------------
st.divider()
tab_criteria, tab_score = st.tabs(["Criteria table", "Weighted HPM Index"])

with tab_criteria:
    st.subheader("Malls matching current HPM criteria")
    st.caption(
        "Raw amenity counts that feed into the satisfy criteria above — no "
        "composite scoring here, see the Weighted HPM Index tab for that."
    )
    table_df = filtered.sort_values(["satisfy", "mall_name"], ascending=[False, True])
    table_cols = [
        "mall_name", "HPM", "satisfy", "has_bike_rack", "has_playground",
        "Gym/Sports (CSV)", "Gym (GeoJSON)", "hpb_event_count", "HDP Outlet",
    ]
    if "supermarket_count" in table_df.columns:
        table_cols.append("supermarket_count")
    st.dataframe(
        table_df[table_cols].rename(columns={"satisfy": "Satisfies criteria"}),
        width="stretch",
        hide_index=True,
    )

    # -----------------------------------------------------------------
    # Current HPM malls that DON'T satisfy the criteria above — built from
    # the full (unfiltered-by-checkbox) summary, not `filtered`, since the
    # "Show current HPMs" / "Show malls that satisfy..." sidebar checkboxes
    # would otherwise hide exactly the malls this section exists to show.
    # The search box still applies, for consistency with the rest of the
    # page. Uses compute_satisfy_groups() so the failing group(s) can be
    # called out per mall rather than just showing a single satisfy flag.
    # -----------------------------------------------------------------
    st.divider()
    st.subheader("Current HPM malls not satisfying criteria")
    st.caption(
        "Current HPM malls (HPM == 1) that don't meet the satisfy criteria "
        "above, given the current thresholds. Ignores the \"Show current "
        "HPMs\" / \"Show malls that satisfy...\" filters (those would "
        "otherwise hide this exact set) but still respects the search box."
    )

    hpm_gap = summary[(summary["HPM"] == 1) & (summary["satisfy"] == 0)].copy()
    if search:
        hpm_gap = hpm_gap[hpm_gap["mall_name"].str.contains(search, case=False, na=False)]

    st.metric("Current HPM malls not satisfying criteria", len(hpm_gap))

    if hpm_gap.empty:
        st.success("Every current HPM mall satisfies the current criteria.")
    else:
        active_living_ok, events_ok, retail_dining_ok = compute_satisfy_groups(
            hpm_gap,
            include_bike_rack=satisfy_include_bike_rack,
            include_playground=satisfy_include_playground,
            min_hpb_events=satisfy_min_hpb,
            min_hdp_outlets=satisfy_min_hdp,
            min_supermarket=satisfy_min_supermarket,
        )
        hpm_gap["Active living OK"] = active_living_ok
        hpm_gap["HPB events OK"] = events_ok
        hpm_gap["Retail/dining OK"] = retail_dining_ok

        gap_cols = [
            "mall_name", "Active living OK", "HPB events OK", "Retail/dining OK",
            "has_bike_rack", "has_playground", "hpb_event_count", "HDP Outlet",
        ]
        if "supermarket_count" in hpm_gap.columns:
            gap_cols.append("supermarket_count")

        st.dataframe(
            hpm_gap[gap_cols].sort_values("mall_name"),
            width="stretch",
            hide_index=True,
        )
        st.caption(
            "A False in any of the three \"OK\" columns is the group that "
            "mall is failing — see recompute_satisfy() / "
            "compute_satisfy_groups() for exactly how each is defined."
        )

with tab_score:
    st.subheader("Proposed HPM Index")
    st.caption(
        "Cat A: HDP outlets" + (" + supermarkets" if "supermarket_count" in filtered.columns else "") + ". "
        "Cat B: bike racks + playgrounds + gyms/sports facilities (GeoJSON + CSV). "
        "Cat C: HPB event/session counts, total participation, and H365 (EDSH store "
        "count + 12-month scans). "
        "Cat D: CHAS + PHPC clinics. "
        "Cat E: catchment population + catchment seniors."
    )
    st.latex(r"""
    \text{HPM Index} =
    \left( \bar{r}_A \times Weight_A \right) +
    \left( \bar{r}_B \times Weight_B \right) +
    \left( \bar{r}_C \times Weight_C \right) +
    \left( \bar{r}_D \times Weight_D \right) +
    \left( \bar{r}_E \times Weight_E \right)
    """)
    st.caption(
        "Each Cat's r̄ (\"r-bar\") is the average, across that category's own "
        "fields, of (that field's value ÷ its own max across all malls) — so "
        "every field counts equally toward its category regardless of raw "
        "scale (e.g. Cat C's edsh_scan_count_12m, which can run into the "
        "thousands, no longer drowns out hpb_event_count, which is usually "
        "single digits), before the category's weight is applied."
    )
    st.space("medium")

    scored = filtered.copy()

    # Each category sums the raw columns that feed it (see the caption above);
    # Cat A optionally folds in supermarket_count when that column is loaded,
    # same as the satisfy criteria's retail/dining group.
    _cat_a_cols = ["HDP Outlet"] + (["supermarket_count"] if "supermarket_count" in scored.columns else [])
    CATEGORY_DEFS = [
        ("a", "Cat A: Healthy Dining Ecosystem", _cat_a_cols),
        ("b", "Cat B: Active Living Infrastructure", ["has_bike_rack", "has_playground", "Gym (GeoJSON)", "Gym/Sports (CSV)"]),
        ("c", "Cat C: HPB & H365 Engagement", ["hpb_event_count", "hpb_session_count", "total_participation", "edsh_store_count", "edsh_scan_count_12m"]),
        ("d", "Cat D: Healthcare Ecosystem", ["Clinic (PHPC)", "Clinic (CHAS)"]),
        ("e", "Cat E: Population Demographics", ["catchment_population", "catchment_seniors"]),
    ]
    # Colours are the dataviz skill's validated categorical palette, slots
    # 1-5 in fixed order (never cycled): blue, orange, aqua, yellow, magenta.
    CATEGORY_COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4"]

    # Normalize each field to 0-1 (value / that field's own max across all
    # malls) BEFORE combining anything, then average the normalized fields
    # within each category. That average is already a 0-1 ratio, so no
    # further per-category max-normalization step is needed before
    # weighting — unlike summing the raw fields first (the previous
    # approach), where a category could be dominated by whichever field
    # happened to have the largest raw scale.
    for key, _label, cols in CATEGORY_DEFS:
        normalized_fields = pd.DataFrame({
            col: scored[col] / max(scored[col].max(), 1) for col in cols
        })
        scored[f"cat_{key}_ratio"] = normalized_fields.mean(axis=1)

    # Sliders with callbacks — every category's weight key defaults once,
    # then _rebalance_weights() keeps all of them summing to 100 regardless
    # of how many categories there are (generalizes the old hardcoded
    # pairwise 3-category rebalance to N categories).
    DEFAULT_WEIGHTS = {"a": 25, "b": 20, "c": 20, "d": 20, "e": 15}  # sums to 100
    weight_keys = [f"w_cat_{key}" for key, _label, _cols in CATEGORY_DEFS]
    for key, default in DEFAULT_WEIGHTS.items():
        wkey = f"w_cat_{key}"
        if wkey not in st.session_state:
            st.session_state[wkey] = default

    def _rebalance_weights(changed_key):
        """Whatever slider the user just moved keeps its new value; the
        remainder (100 - changed) is redistributed across the other
        sliders in proportion to their current relative weights (or split
        evenly if those are all currently zero). The last of the "other"
        sliders absorbs the rounding remainder so the total is always
        exactly 100."""
        rem = 100 - st.session_state[changed_key]
        other_keys = [k for k in weight_keys if k != changed_key]
        other_sum = sum(st.session_state[k] for k in other_keys)
        allocated = 0
        for k in other_keys[:-1]:
            share = int(round(rem * (st.session_state[k] / other_sum))) if other_sum > 0 else rem // len(other_keys)
            st.session_state[k] = share
            allocated += share
        st.session_state[other_keys[-1]] = rem - allocated

    def _make_on_change(key):
        return lambda: _rebalance_weights(key)

    weight_cols = st.columns(len(CATEGORY_DEFS))
    weights = {}
    for col, (key, label, _cols) in zip(weight_cols, CATEGORY_DEFS):
        wkey = f"w_cat_{key}"
        weights[key] = col.slider(f"{label} (%)", 0, 100, key=wkey, on_change=_make_on_change(wkey))

    for key, _label, _cols in CATEGORY_DEFS:
        scored[f"cat_{key}_score"] = scored[f"cat_{key}_ratio"] * weights[key]

    score_cols = [f"cat_{key}_score" for key, _label, _cols in CATEGORY_DEFS]
    scored["readiness_score"] = scored[score_cols].sum(axis=1)

    score_top_n = st.slider("Show top N by HPM Index", 5, 50, 15, key="score_top_n")
    top_scored = scored.sort_values("readiness_score", ascending=False).head(score_top_n)

    top_scored_long = top_scored.melt(
        id_vars=["mall_name", "readiness_score"],
        value_vars=score_cols,
        var_name="category",
        value_name="score_contribution"
    )

    category_labels = {f"cat_{key}_score": label for key, label, _cols in CATEGORY_DEFS}
    category_order = [label for _key, label, _cols in CATEGORY_DEFS]
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
                    domain=category_order,
                    range=CATEGORY_COLORS
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

    score_table_rename = {"readiness_score": "HPM Index"}
    score_table_rename.update({f"cat_{key}_score": f"Cat {key.upper()} Score" for key, _label, _cols in CATEGORY_DEFS})
    st.dataframe(
        top_scored[["mall_name", "readiness_score"] + score_cols + ["HPM"]].rename(columns=score_table_rename),
        width="stretch",
        hide_index=True,
    )

# ---------------------------------------------------------------------------
# Map — every mall in view, sized/coloured by criteria & HPM status. Shared
# by both tabs above (identical in both source scripts), so it lives here
# once rather than being duplicated per tab.
# ---------------------------------------------------------------------------
st.divider()
st.subheader("Malls map")
map_df = filtered.dropna(subset=["latitude", "longitude"]).copy()

if map_df.empty:
    st.info("No malls with coordinates in the current filter.")
else:
    col1, col2 = st.columns(2)
    size = col1.slider("Size slider", 0, 1000, 330, key="map_size")
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
