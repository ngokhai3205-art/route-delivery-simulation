# app.py
import streamlit as st
import requests
import folium
from streamlit_folium import st_folium

st.set_page_config(page_title="Route Status & Vehicle Recommender", layout="wide")

st.title("🚚 Route Status → Vehicle Recommendation (MVP)")

col1, col2 = st.columns(2)
with col1:
    origin = st.text_input("Điểm xuất phát", value="Hanoi Tower, Hanoi")
    destination = st.text_input("Điểm đến", value="My Dinh Bus Station, Hanoi")

with col2:
    traffic = st.selectbox("Mật độ giao thông", ["Low", "Medium", "High"])
    weather = st.selectbox("Thời tiết", ["Clear", "Rain", "Storm"])
    flood = st.selectbox("Ngập lụt", ["None", "Local", "Widespread"])

st.markdown("### Thông tin đơn hàng")
col3, col4, col5 = st.columns(3)
with col3:
    size = st.selectbox("Kích cỡ hàng", ["Small (≤5kg)", "Medium (≤20kg)", "Large (≤200kg)", "Bulky/Over"])
with col4:
    urgency = st.selectbox("Mức khẩn cấp", ["Low", "Normal", "High", "Critical (≤2h)"])
with col5:
    distance_limit_for_drone_km = st.number_input("Giới hạn km cho drone", min_value=1, max_value=30, value=10)

def geocode(addr):
    # Nominatim API (free, rate-limited). Dùng thử nghiệm.
    url = "https://nominatim.openstreetmap.org/search"
    params = {"q": addr, "format": "json", "limit": 1}
    r = requests.get(url, params=params, headers={"User-Agent": "streamlit-app"})
    r.raise_for_status()
    data = r.json()
    if not data:
        return None
    return float(data[0]["lat"]), float(data[0]["lon"])

def route_points(latlon_a, latlon_b):
    # OSRM public server (dùng thử). Trả về polyline đơn giản.
    url = f"https://router.project-osrm.org/route/v1/driving/{latlon_a[1]},{latlon_a[0]};{latlon_b[1]},{latlon_b[0]}"
    params = {"overview": "full", "geometries": "geojson"}
    r = requests.get(url, params=params, headers={"User-Agent": "streamlit-app"})
    r.raise_for_status()
    js = r.json()
    if js.get("routes"):
        coords = js["routes"][0]["geometry"]["coordinates"]  # [lon, lat]
        dist_km = js["routes"][0]["distance"] / 1000
        dur_min = js["routes"][0]["duration"] / 60
        latlons = [(c[1], c[0]) for c in coords]
        return latlons, dist_km, dur_min
    return None, None, None

def recommend(size, urgency, traffic, weather, flood, dist_km, drone_limit_km):
    s = size.split()[0].lower()  # small/medium/large/bulky
    u = urgency.lower()

    allow_drone = (weather in ["Clear", "Rain"]) and (flood != "Widespread") and (dist_km is not None and dist_km <= drone_limit_km)

    # Luật tối giản (có thể mở rộng)
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

run = st.button("Tính toán & Vẽ tuyến")

if run:
    try:
        a = geocode(origin)
        b = geocode(destination)
        if not a or not b:
            st.error("Không tìm được toạ độ địa chỉ. Thử đổi địa chỉ cụ thể hơn.")
        else:
            pts, dist_km, dur_min = route_points(a, b)
            if not pts:
                st.error("Không vẽ được tuyến. Thử điểm khác.")
            else:
                recs = recommend(size, urgency, traffic, weather, flood, dist_km, distance_limit_for_drone_km)
                st.success(f"Đề xuất phương tiện: {', '.join(recs)}")
                st.info(f"Quãng đường ~ {dist_km:.1f} km • Thời gian ~ {dur_min:.0f} phút (ước lượng OSRM)")

                m = folium.Map(location=pts[len(pts)//2], zoom_start=12)
                folium.Marker(a, tooltip="Xuất phát").add_to(m)
                folium.Marker(b, tooltip="Điểm đến").add_to(m)
                folium.PolyLine(pts, weight=5).add_to(m)

                status = f"Traffic: {traffic} • Weather: {weather} • Flood: {flood}"
                folium.Marker(pts[len(pts)//2], tooltip=status, popup=status).add_to(m)

                st_folium(m, width=900, height=500)
    except Exception as e:
        st.error(f"Lỗi: {e}")
