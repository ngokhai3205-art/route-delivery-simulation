import math
import datetime as dt
import requests
import streamlit as st
import folium
from streamlit_folium import st_folium
from geopy.geocoders import Nominatim
from geopy.extra.rate_limiter import RateLimiter

# -------------------- CONFIG --------------------
st.set_page_config(page_title="Route Status & Vehicle Recommender", layout="wide")
st.title("🚚 Route Status → Vehicle Recommendation (auto status)")

# -------------------- HELPERS -------------------
def haversine_km(a, b):
    """Khoảng cách đường thẳng giữa 2 tọa độ (lat, lon) theo km."""
    R = 6371.0
    lat1, lon1 = math.radians(a[0]), math.radians(a[1])
    lat2, lon2 = math.radians(b[0]), math.radians(b[1])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    x = math.sin(dlat/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin(dlon/2)**2
    return 2 * R * math.asin(math.sqrt(x))

def estimate_speed_kmh(traffic, weather, flood):
    """Ước lượng tốc độ trung bình theo điều kiện (đơn giản)."""
    v = 35.0  # baseline đô thị
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
    """Luật gợi ý phương tiện (tối giản, có thể mở rộng)."""
    s = size.split()[0].lower()  # small/medium/large/bulky
    u = urgency.lower()
    allow_drone = (weather in ["Clear", "Rain"]) and (flood != "Widespread") \
                  and (dist_km is not None and dist_km <= drone_limit_km)

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

# ---------- Auto status (weather/flood/traffic) ----------
def _weather_from_code(code, wind):
    # theo Open-Meteo weathercode
    if code in [95, 96, 99] or wind >= 50:  # dông, gió mạnh
        return "Storm"
    if (51 <= code <= 67) or (80 <= code <= 82) or (61 <= code <= 65):
        return "Rain"
    return "Clear"

def get_weather_and_flood(lat, lon):
    """Lấy thời tiết hiện tại & lượng mưa 24h từ Open-Meteo (free, no key)."""
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": lat,
        "longitude": lon,
        "current_weather": True,
        "hourly": "precipitation",
        "past_days": 1,
        "timezone": "auto",
    }
    r = requests.get(url, params=params, timeout=10)
    r.raise_for_status()
    js = r.json()

    cw = js["current_weather"]
    code = int(cw["weathercode"])
    wind = float(cw["windspeed"])
    weather = _weather_from_code(code, wind)

    precip = js.get("hourly", {}).get("precipitation", [])
    precip_sum = sum(p for p in precip if isinstance(p, (int, float)))
    if precip_sum >= 100:
        flood = "Widespread"
    elif precip_sum >= 30:
        flood = "Local"
    else:
        flood = "None"

    hour_local = int(cw["time"][11:13]) if "time" in cw else dt.datetime.now().hour
    tzname = js.get("timezone", "local")
    return weather, flood, hour_local, tzname

def estimate_traffic_level(hour_local, weekday, weather):
    """Ước lượng mật độ giao thông theo giờ cao điểm & thời tiết."""
    # weekday: 0=Mon ... 6=Sun
    if weekday < 5 and (7 <= hour_local <= 9 or 17 <= hour_local <= 19):
        level = "High"
    elif weekday < 5:
        level = "Medium"
    else:
        level = "Low"
    bump = {"Clear": 0, "Rain": 1, "Storm": 2}.get(weather, 0)
    order = ["Low", "Medium", "High"]
    return order[min(2, order.index(level) + bump)]

# -------------------- INPUTS --------------------
# 3 chế độ nhập điểm: địa chỉ / tọa độ / mẫu
geolocator = Nominatim(user_agent="route-delivery-sim-ngokhai3205-art")
geocode = RateLimiter(geolocator.geocode, min_delay_seconds=1, swallow_exceptions=True)

mode = st.radio("Chọn cách nhập điểm:",
                ["Nhập địa chỉ", "Nhập tọa độ (lat, lon)", "Chọn địa chỉ mẫu (có sẵn)"],
                horizontal=True)

origin = destination = None

if mode == "Nhập địa chỉ":
    colA, colB = st.columns(2)
    with colA:
        start_addr = st.text_input("Điểm xuất phát (VD: 1 Đại Cồ Việt, Hai Bà Trưng, Hà Nội)")
    with colB:
        dest_addr = st.text_input("Điểm đến (VD: Bến xe Mỹ Đình, Nam Từ Liêm, Hà Nội)")

    if "geo" not in st.session_state:
        st.session_state.geo = {"origin": None, "destination": None}

    if st.button("📍 Lấy tọa độ từ địa chỉ"):
        with st.spinner("Đang tìm tọa độ..."):
            loc1 = geocode(start_addr) if start_addr else None
            loc2 = geocode(dest_addr) if dest_addr else None
        if loc1 and loc2:
            st.session_state.geo["origin"] = (loc1.latitude, loc1.longitude)
            st.session_state.geo["destination"] = (loc2.latitude, loc2.longitude)
            st.success("✅ Đã xác định được tọa độ!")
        else:
            st.error("❌ Không tìm thấy địa chỉ, hãy nhập cụ thể hơn.")

    origin = st.session_state.geo["origin"]
    destination = st.session_state.geo["destination"]

elif mode == "Nhập tọa độ (lat, lon)":
    colA, colB = st.columns(2)
    with colA:
        o_lat = st.number_input("Xuất phát - lat", value=21.026754, format="%.6f")
        o_lon = st.number_input("Xuất phát - lon", value=105.846083, format="%.6f")
    with colB:
        d_lat = st.number_input("Điểm đến - lat", value=21.028762, format="%.6f")
        d_lon = st.number_input("Điểm đến - lon", value=105.776900, format="%.6f")
    origin = (o_lat, o_lon)
    destination = (d_lat, d_lon)
else:
    presets = {
        "Hanoi Tower, Hanoi": (21.026754, 105.846083),
        "My Dinh Bus Station, Hanoi": (21.028762, 105.776900),
        "Noi Bai Airport, Hanoi": (21.214184, 105.802827),
        "Hoan Kiem Lake, Hanoi": (21.028511, 105.852005),
    }
    colA, colB = st.columns(2)
    with colA:
        origin_name = st.selectbox("Điểm xuất phát (mẫu)", list(presets.keys()), index=0)
    with colB:
        dest_name = st.selectbox("Điểm đến (mẫu)", list(presets.keys()), index=1)
    origin = presets[origin_name]
    destination = presets[dest_name]

st.markdown("### Thông tin đơn hàng")
col3, col4, col5 = st.columns(3)
with col3:
    size = st.selectbox("Kích cỡ hàng", ["Small (≤5kg)", "Medium (≤20kg)", "Large (≤200kg)", "Bulky/Over"])
with col4:
    urgency = st.selectbox("Mức khẩn cấp", ["Low", "Normal", "High", "Critical (≤2h)"])
with col5:
    distance_limit_for_drone_km = st.number_input("Giới hạn km cho drone", min_value=1, max_value=30, value=10)

# -------------------- AUTO STATUS (OUTPUT) --------------------
computed_status = None
if origin and destination:
    try:
        weather_now, flood_now, hour_local, tzname = get_weather_and_flood(*origin)
        weekday = dt.datetime.utcnow().weekday()   # xấp xỉ giờ địa phương -> đủ dùng
        traffic_now = estimate_traffic_level(hour_local, weekday, weather_now)
        computed_status = {
            "traffic": traffic_now,
            "weather": weather_now,
            "flood": flood_now,
            "hour": hour_local,
            "tz": tzname,
        }
        st.markdown(
            f"**Trạng thái tuyến (tự tính)** — "
            f"Traffic: `{traffic_now}` • Weather: `{weather_now}` • Flood: `{flood_now}` "
            f"(giờ địa phương ~ {hour_local}:00, TZ: {tzname})"
        )
    except Exception as e:
        st.warning(f"Không lấy được trạng thái tự động (sẽ dùng giả định). Lý do: {e}")

# -------------------- PERSIST & ACTION --------------------
if "calc" not in st.session_state:
    st.session_state.calc = None

pressed = st.button("Tính toán & Vẽ tuyến")

if pressed:
    if not (origin and destination):
        st.error("Vui lòng nhập/nhận tọa độ cho cả Điểm xuất phát và Điểm đến trước.")
    else:
        # Dùng OUTPUT đã tính; nếu không có thì dùng mặc định an toàn
        if computed_status:
            traffic = computed_status["traffic"]
            weather = computed_status["weather"]
            flood = computed_status["flood"]
        else:
            traffic, weather, flood = "Medium", "Clear", "None"

        dist_km = haversine_km(origin, destination)
        speed = estimate_speed_kmh(traffic, weather, flood)
        est_minutes = max(1, int((dist_km / speed) * 60))
        recs = recommend(size, urgency, traffic, weather, flood, dist_km, distance_limit_for_drone_km)

        st.session_state.calc = {
            "origin": origin, "destination": destination,
            "traffic": traffic, "weather": weather, "flood": flood,
            "size": size, "urgency": urgency, "drone_limit": distance_limit_for_drone_km,
            "dist_km": dist_km, "speed": speed, "est_minutes": est_minutes, "recs": recs,
        }

# -------------------- DISPLAY RESULT --------------------
if st.session_state.calc:
    c = st.session_state.calc
    st.success(f"Đề xuất phương tiện: {', '.join(c['recs'])}")
    st.info(f"Quãng đường ước lượng (đường thẳng) ~ {c['dist_km']:.1f} km • "
            f"Thời gian ước lượng ~ {c['est_minutes']} phút (v={c['speed']:.0f} km/h)")

    mid = ((c["origin"][0] + c["destination"][0]) / 2, (c["origin"][1] + c["destination"][1]) / 2)
    m = folium.Map(location=mid, zoom_start=12)
    folium.Marker(c["origin"], tooltip="Xuất phát").add_to(m)
    folium.Marker(c["destination"], tooltip="Điểm đến").add_to(m)
    # Tuyến đơn giản: đường thẳng (offline-friendly). Có thể thay bằng tuyến thật bằng ORS/Mapbox sau này.
    folium.PolyLine([c["origin"], c["destination"]], weight=5).add_to(m)
    status = f"Traffic: {c['traffic']} • Weather: {c['weather']} • Flood: {c['flood']}"
    folium.Marker(mid, tooltip=status, popup=status).add_to(m)
    st_folium(m, width=900, height=500)

st.caption("Gợi ý: Status auto = thời tiết (Open-Meteo) + suy luận ngập theo mưa 24h + giao thông theo giờ cao điểm & thời tiết.")
