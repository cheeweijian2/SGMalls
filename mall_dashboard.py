import altair as alt
import pandas as pd
import pydeck as pdk
import streamlit as st

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
st.set_page_config(page_title="Mall Amenities Dashboard", layout="wide")

@st.cache_data
def load_summary(path):
    df = pd.read_csv(path)
    df = df.rename(columns={df.columns[0]: "mall_name"})
    return df

 
st.title("🏬 Mall Amenities Dashboard")
st.caption("HDP outlets, gyms, clinics, and bike rack proximity across Singapore malls.")
 
# ---------------------------------------------------------------------------
# Data source — upload the CSV produced by mall_data_pipeline.py
# ---------------------------------------------------------------------------
summary_file = st.file_uploader("Upload CSV", type="csv")
butt = st.checkbox("Use default data")
if butt:
    summary_file = "mall_locations_summary.csv"

if summary_file is None:
    st.info("Upload a CSV to get started.")
    st.stop()
 
summary = load_summary(summary_file)

category_cols = [
    c for c in summary.columns
    if c not in ("mall_name", "Total", "has_bike_rack", "longitude", "latitude", "HPM")
]

# ---------------------------------------------------------------------------
# Sidebar filters
# ---------------------------------------------------------------------------
st.sidebar.header("Filters")
search = st.sidebar.text_input("Search mall name")
bike_only = st.sidebar.checkbox("Only malls with a bike rack nearby")
current_hpm_only = st.sidebar.checkbox("Show current HPMs")
min_total = st.sidebar.slider("Minimum total amenities", 0, int(summary["Total"].max()), 0)
selected_categories = st.sidebar.multiselect(
    "Must have at least one of these categories", category_cols, default=[]
)

filtered = summary.copy()
if search:
    filtered = filtered[filtered["mall_name"].str.contains(search, case=False, na=False)]
if bike_only:
    filtered = filtered[filtered["has_bike_rack"] == 1]
if current_hpm_only:
    filtered = filtered[filtered["HPM"] == 1]
filtered = filtered[filtered["Total"] >= min_total]
if selected_categories:
    filtered = filtered[(filtered[selected_categories] > 0).any(axis=1)]

# ---------------------------------------------------------------------------
# KPIs
# ---------------------------------------------------------------------------
col1, col2, col3, col4 = st.columns(4)
col1.metric("Malls shown", len(filtered))
col2.metric("Total amenities", int(filtered["Total"].sum()))
col3.metric("Malls with bike racks", int(filtered["has_bike_rack"].sum()))
col4.metric("Avg amenities / mall", round(filtered["Total"].mean(), 1) if len(filtered) else 0)

st.divider()

# ---------------------------------------------------------------------------
# Top malls chart
# ---------------------------------------------------------------------------
st.subheader("Top malls by total amenities")
top_n = st.slider("Show top N malls", 5, 50, 15)
top_malls = filtered.sort_values("Total", ascending=False).head(top_n)

# st.bar_chart always sorts its x-axis alphabetically, so we use Altair
# directly and pass an explicit sort order (mall names ranked by Total).
mall_order = top_malls["mall_name"].tolist()
top_malls_long = top_malls.melt(
    id_vars="mall_name", value_vars=category_cols, var_name="category", value_name="count"
)
top_chart = (
    alt.Chart(top_malls_long)
    .mark_bar()
    .encode(
        x=alt.X("mall_name:N", sort=mall_order, title="Mall"),
        y=alt.Y("count:Q", title="Amenities"),
        color=alt.Color("category:N", title="Category"),
    )
)
st.altair_chart(top_chart, width="stretch")

# ---------------------------------------------------------------------------
# Category breakdown across all shown malls
# ---------------------------------------------------------------------------
st.subheader("Category totals across shown malls")
st.bar_chart(filtered[category_cols].sum())

# ---------------------------------------------------------------------------
# Mall readiness score — weighted composite of clinics, gyms, HDP outlets,
# and bike rack access. Each component is normalised to 0-1 against the max
# in the currently filtered view, then combined using the slider weights.
# Clinics + Gyms + HDP weights are adjustable; bike rack takes the remainder
# so the four always sum to 100%.
# ---------------------------------------------------------------------------
st.divider()
st.subheader("Mall readiness score")
st.caption("Weight each amenity type to build a composite 0-100 score per mall.")

scored = filtered.copy()
scored["clinic_count"] = scored["Clinic (CHAS)"] + scored["Clinic (PHPC)"]
scored["gym_count"] = scored["Gym (GeoJSON)"] + scored["Gym/Sports (CSV)"]
scored["hdp_count"] = scored["HDP Outlet"]

max_clinic = max(scored["clinic_count"].max(), 1)
max_gym = max(scored["gym_count"].max(), 1)
max_hdp = max(scored["hdp_count"].max(), 1)

w_col1, w_col2, w_col3 = st.columns(3)
w_clinic = w_col1.slider("Clinics weight (%)", 0, 100, 25)
w_gym = w_col2.slider("Gyms weight (%)", 0, 100, 25)
w_hdp = w_col3.slider("HDP outlets weight (%)", 0, 100, 25)
w_bike = 100 - (w_clinic + w_gym + w_hdp)

if w_bike < 0:
    st.error(
        f"Clinics + Gyms + HDP weights add up to {w_clinic + w_gym + w_hdp}%, over 100%. "
        "Lower one of the sliders — bike rack weight can't go negative."
    )
    w_bike = 0
else:
    st.caption(f"Bike rack weight (remainder): **{w_bike}%**")

scored["clinic_norm"] = scored["clinic_count"] / max_clinic
scored["gym_norm"] = scored["gym_count"] / max_gym
scored["hdp_norm"] = scored["hdp_count"] / max_hdp
scored["bike_norm"] = scored["has_bike_rack"]  # already 0 or 1

scored["readiness_score"] = (
    scored["clinic_norm"] * w_clinic
    + scored["gym_norm"] * w_gym
    + scored["hdp_norm"] * w_hdp
    + scored["bike_norm"] * w_bike
)

score_top_n = st.slider("Show top N by readiness score", 5, 50, 15, key="score_top_n")
top_scored = scored.sort_values("readiness_score", ascending=False).head(score_top_n)

score_chart = (
    alt.Chart(top_scored)
    .mark_bar()
    .encode(
        x=alt.X("mall_name:N", sort=top_scored["mall_name"].tolist(), title="Mall"),
        y=alt.Y("readiness_score:Q", title="Readiness score"),
        tooltip=["mall_name", "readiness_score", "clinic_count", "gym_count", "hdp_count", "has_bike_rack"],
    )
)
st.altair_chart(score_chart, width="stretch")

st.dataframe(
    top_scored[
        ["mall_name", "readiness_score", "clinic_count", "gym_count", "hdp_count", "has_bike_rack"]
    ].rename(columns={"has_bike_rack": "bike_rack"}),
    width="stretch",
)

# ---------------------------------------------------------------------------
# Map — every mall in view, sized/coloured by total amenities
# ---------------------------------------------------------------------------
st.subheader("Malls map")
map_df = filtered.dropna(subset=["latitude", "longitude"]).copy()
if map_df.empty:
    st.info("No malls with coordinates in the current filter.")
else:
    max_total = max(map_df["Total"].max(), 1)
    map_df["radius"] = 200 + (map_df["Total"] / max_total) * 250
    map_df["color"] = map_df["has_bike_rack"].apply(
        lambda has_rack: [0, 128, 255, 160] if has_rack else [220, 60, 60, 160]
    )
    # HPM malls get a gold ring around the bubble; non-HPM malls get no ring
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
    st.pydeck_chart(pdk.Deck(layers=[layer], initial_view_state=view_state, tooltip=tooltip))
    st.caption(
        "Bubble size = total amenities. Blue = bike rack within 200m, red = none. "
        "Gold ring = current HPM mall."
    )

# ---------------------------------------------------------------------------
# Full table + download
# ---------------------------------------------------------------------------
st.subheader("Mall details")
st.dataframe(filtered.sort_values("Total", ascending=False), width="stretch")

st.download_button(
    "Download filtered data as CSV",
    data=filtered.to_csv(index=False).encode("utf-8"),
    file_name="filtered_mall_summary.csv",
    mime="text/csv",
)
