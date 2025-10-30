# app.py (offline-friendly)
import math
import streamlit as st
import folium
from streamlit_folium import st_folium

st.set_page_config(page_title="Route Status & Vehicle Recommender", layout="wide")
st.title("🚚 Route Status → Vehicle Recommendation (MVP, offline-friendly)")

# --- Helpers ---
def haversine_km(a, b):
    R = 6371.0
    lat1, lon1 = math.radians(a[0]), math.radians(a[1])
    lat2, lon2 = math.radians(b[0]), math.radians(b[1])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    x = math.sin(dlat/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin(dlon/2)**2
    return 2*R*math.asin(math.sqrt(x))

def estimate_speed_kmh(traffic, weather, flood):
    # baseline 35 km/h for urban
    v = 35.0
    if traffic == "Medium":
        v *= 0.8
    elif traffic == "High":
        v *= 0.6
    if weather == "Rain":
        v *= 0.85
    elif weather == "Storm":
        v *= 0.6
    if flood == "Local":
        v *= 0.85
    elif flood == "Widespread":
        v *= 0.5
    return max(8.0, v)

def recommend(size, urgency, traffic, weather, flood, dist_km, drone_limit_km):
    s = size.split()[0].lower()  # small/medium/large/bulky
    u = urgency.lower()

    allow_drone = (weather in ["Clear", "Rain"]) and (flood != "Widespread") and (dist_km is not None and dist_km <= drone_limit_km)

    if s == "small":
        if u.startswith("critical"):
            if allow_drone and traffic == "High":
                return ["Drone", "E-bike/Motorbike"]
            return ["E-bike/Motorbike"]
        else:
            if traffic == "High" and weather != "Storm":
                return ["E-bike/Motorbike"] + (["Drone"] if allow_drone else [])
            return ["Motorbike", "E-van (short range)"] + (["Drone"] if allow_drone else [])
    elif s == "medium":
        if u in ["high", "critical (≤2h)"]:
            if weather == "Storm" or flood != "None":
                return ["Motorbike (weatherproof)", "Van"]
            return ["Motorbike", "Van"] + (["Drone"] if allow_drone else [])
        else:
            return ["Van", "Motorbike"] + (["Drone"] if allow_drone else [])
    elif s == "large":
        if flood == "Widespread":
            return ["Truck (high clearance)", "Van (gầm cao)"]
        return ["Van", "Truck"]
    else:  # bulky/over
        return ["Truck", "Specialized vehicle"]

# --- UI ---
mode = st.radio("Chọn cách nhập điểm:", ["Nhập địa chỉ mẫu (có sẵn)", "Nhập toạ độ (lat, lon)"], horizontal=True)

# A few safe preset addresses with coordinates to avoid external geocoding
presets = {
    "Hanoi Tower, Hanoi": (21.026754, 105.846083),
    "My Dinh Bus Station, Hanoi": (21.028762, 105.776900),
    "Noi Bai Airport, Hanoi": (21.214184, 105.802827),
    "Hoan Kiem Lake, Hanoi": (21.028511, 105.852005),
}

if mode == "Nhập địa chỉ mẫu (có sẵn)":
    colA, colB = st.columns(2)
    with colA:
        origin_name = st.selectbox("Điểm xuất phát (mẫu)", list(presets.keys()), index=0)
    with colB:
        dest_name = st.selectbox("Điểm đến (mẫu)", list(presets.keys()), index=1)
    origin = presets[origin_name]
    destination = presets[dest_name]
else:
    colA, colB = st.columns(2)
    with colA:
        o_lat = st.number_input("Xuất phát - lat", value=21.026754, format="%.6f")
        o_lon = st.number_input("Xuất phát - lon", value=105.846083, format="%.6f")
    with colB:
        d_lat = st.number_input("Điểm đến - lat", value=21.028762, format="%.6f")
        d_lon = st.number_input("Điểm đến - lon", value=105.776900, format="%.6f")
    origin = (o_lat, o_lon)
    destination = (d_lat, d_lon)

col1, col2, col3 = st.columns(3)
with col1:
    traffic = st.selectbox("Mật độ giao thông", ["Low", "Medium", "High"])
with col2:
    weather = st.selectbox("Thời tiết", ["Clear", "Rain", "Storm"])
with col3:
    flood = st.selectbox("Ngập lụt", ["None", "Local", "Widespread"])

st.markdown("### Thông tin đơn hàng")
col3, col4, col5 = st.columns(3)
with col3:
    size = st.selectbox("Kích cỡ hàng", ["Small (≤5kg)", "Medium (≤20kg)", "Large (≤200kg)", "Bulky/Over"])
with col4:
    urgency = st.selectbox("Mức khẩn cấp", ["Low", "Normal", "High", "Critical (≤2h)"])
with col5:
    distance_limit_for_drone_km = st.number_input("Giới hạn km cho drone", min_value=1, max_value=30, value=10)

if st.button("Tính toán & Vẽ tuyến"):
    dist_km = haversine_km(origin, destination)
    speed = estimate_speed_kmh(traffic, weather, flood)
    est_minutes = max(1, int((dist_km / speed) * 60))

    recs = recommend(size, urgency, traffic, weather, flood, dist_km, distance_limit_for_drone_km)
    st.success(f"Đề xuất phương tiện: {', '.join(recs)}")
    st.info(f"Quãng đường ước lượng (đường thẳng) ~ {dist_km:.1f} km • Thời gian ước lượng ~ {est_minutes} phút (v={speed:.0f} km/h)")

    # Draw simple straight line on map (offline-friendly; no external routing call)
    mid = ((origin[0] + destination[0]) / 2, (origin[1] + destination[1]) / 2)
    m = folium.Map(location=mid, zoom_start=12)
    folium.Marker(origin, tooltip="Xuất phát").add_to(m)
    folium.Marker(destination, tooltip="Điểm đến").add_to(m)
    folium.PolyLine([origin, destination], weight=5).add_to(m)
    status = f"Traffic: {traffic} • Weather: {weather} • Flood: {flood}"
    folium.Marker(mid, tooltip=status, popup=status).add_to(m)
    st_folium(m, width=900, height=500)

st.caption("Gợi ý: bản này không gọi API bên ngoài để tránh lỗi kết nối. Bạn có thể chuyển sang nhập toạ độ nếu cần địa điểm khác.")