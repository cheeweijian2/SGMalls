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
butt = st.checkbox("Use sample data")
if butt:
    summary_file = "mall_locations_summary.csv"

if summary_file is None:
    st.info("Upload a CSV to get started.")
    st.stop()
 
summary = load_summary(summary_file)

category_cols = [
    c for c in summary.columns
    if c not in ("mall_name", "Total", "has_bike_rack", "longitude", "latitude", "HPM", "satisfy")
]

# ---------------------------------------------------------------------------
# Sidebar filters
# ---------------------------------------------------------------------------
st.sidebar.header("Filters")
search = st.sidebar.text_input("Search mall name")
current_hpm_only = st.sidebar.checkbox("Show current HPMs")
satisfy_current = st.sidebar.checkbox("Show malls that satisfy current HPM criteria")
st.sidebar.info("To satisfy current HPM criteria, the mall must have at least 1 Fitness facilities, have Bike racks and at least 3 HDP (Healthier Dining Programme) outlets")

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
col2.metric("Current HPM Malls", len(filtered[filtered["HPM"]== 1]))

# ---------------------------------------------------------------------------
# Mall readiness score — dynamically interactive sliders (sum to 100%)
# ---------------------------------------------------------------------------
st.divider()
st.subheader("Mall readiness score")
st.caption("Adjust any slider; the others automatically adjust to maintain a 100% total.")

# Initialize session state for sliders if not already set
if "w_cat_a" not in st.session_state:
    st.session_state.w_cat_a = 35
if "w_cat_b" not in st.session_state:
    st.session_state.w_cat_b = 35
if "w_cat_d" not in st.session_state:
    st.session_state.w_cat_d = 30

# Callback functions to balance remaining sliders dynamically
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

# Sliders bound to session state & callbacks
w_col1, w_col2, w_col3 = st.columns(3)
w_cat_a = w_col1.slider(
    "Category A: HDP Outlets (%)", 0, 100, key="w_cat_a", on_change=update_a
)
w_cat_b = w_col2.slider(
    "Category B: Fitness & Bike (%)", 0, 100, key="w_cat_b", on_change=update_b
)
w_cat_d = w_col3.slider(
    "Category D: Clinics (%)", 0, 100, key="w_cat_d", on_change=update_d
)

# 1. Define combined category metrics
scored = filtered.copy()
scored["cat_a_count"] = scored["HDP Outlet"]
scored["cat_b_count"] = scored["Gym (GeoJSON)"] + scored["has_bike_rack"] + scored["Gym/Sports (CSV)"]
scored["cat_d_count"] = scored["Clinic (PHPC)"] + scored["Clinic (CHAS)"]

# 2. Maximum bounds for normalization
max_cat_a = max(scored["cat_a_count"].max(), 1)
max_cat_b = max(scored["cat_b_count"].max(), 1)
max_cat_d = max(scored["cat_d_count"].max(), 1)

# 3. Calculate weighted scores using interactive dynamic slider values
scored["cat_a_score"] = (scored["cat_a_count"] / max_cat_a) * w_cat_a
scored["cat_b_score"] = (scored["cat_b_count"] / max_cat_b) * w_cat_b
scored["cat_d_score"] = (scored["cat_d_count"] / max_cat_d) * w_cat_d

scored["readiness_score"] = (
    scored["cat_a_score"]
    + scored["cat_b_score"]
    + scored["cat_d_score"]
)

# ---------------------------------------------------------------------------
# Stacked Bar Chart (Readiness Score) — Orange, Green, Blue Theme
# ---------------------------------------------------------------------------
score_top_n = st.slider("Show top N by readiness score", 5, 50, 15, key="score_top_n")
top_scored = scored.sort_values("readiness_score", ascending=False).head(score_top_n)

# Reshape data into long format for Altair stacked bars
top_scored_long = top_scored.melt(
    id_vars=["mall_name", "readiness_score"],
    value_vars=["cat_a_score", "cat_b_score", "cat_d_score"],
    var_name="category",
    value_name="score_contribution"
)

# Friendly display names for chart legend
category_labels = {
    "cat_a_score": "Cat A: HDP Outlets",
    "cat_b_score": "Cat B: Fitness & Bike",
    "cat_d_score": "Cat D: Clinics"
}
top_scored_long["category"] = top_scored_long["category"].map(category_labels)

# Explicit sort order for malls on the x-axis
mall_order = top_scored["mall_name"].tolist()

# Altair Stacked Bar Chart with Orange, Green, Blue scheme
score_chart = (
    alt.Chart(top_scored_long)
    .mark_bar()
    .encode(
        x=alt.X("mall_name:N", sort=mall_order, title="Mall"),
        y=alt.Y("score_contribution:Q", title="Readiness Score (0-100)"),
        color=alt.Color(
            "category:N", 
            title="Category Component",
            scale=alt.Scale(
                domain=["Cat A: HDP Outlets", "Cat B: Fitness & Bike", "Cat D: Clinics"],
                range=["#fc5151", "#23dc1d", "#3745ff"]  # Orange, Green, Blue
            )
        ),
        tooltip=[
            alt.Tooltip("mall_name:N", title="Mall"),
            alt.Tooltip("category:N", title="Category"),
            alt.Tooltip("score_contribution:Q", title="Category Score", format=".1f"),
            alt.Tooltip("readiness_score:Q", title="Total Readiness Score", format=".1f")
        ]
    )
)

st.altair_chart(score_chart, use_container_width=True)
st.dataframe(top_scored, hide_index=True)

# ---------------------------------------------------------------------------
# Map — every mall in view, sized/coloured by total amenities
# ---------------------------------------------------------------------------
st.divider()
st.subheader("Malls map")
map_df = filtered.dropna(subset=["latitude", "longitude"]).copy()
if map_df.empty:
    st.info("No malls with coordinates in the current filter.")
else:
    max_total = max(map_df["Total"].max(), 1)
    col1, col2 = st.columns(2)
    size = col1.slider("Size slider", 0, 1000, 330) # (size, x, y, opacity)
    map_df["radius"] = size #+ (map_df["Total"] / max_total) * 250
    map_df["color"] = map_df["satisfy"].apply(
        lambda satisfy: [0, 255, 0, 200] if satisfy else [255, 0, 0, 160]
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
        ":green[Green] = satisfies current HPM criteria, :red[Red] = does not satisfy current HPM criteria. "
        ":color[Gold]{foreground='rgb(255, 215, 0)'} ring = current HPM mall."
    )

# ---------------------------------------------------------------------------
# Full table + download
# ---------------------------------------------------------------------------
# st.subheader("Mall details")
# st.dataframe(filtered.sort_values("Total", ascending=False), width="stretch", hide_index = True)

# st.download_button(
#     "Download filtered data as CSV",
#     data=filtered.to_csv(index=False).encode("utf-8"),
#     file_name="filtered_mall_summary.csv",
#     mime="text/csv",
# )
