# app.py — Route Status → Vehicle Recommendation (address input + ORS routing + auto status)
import os
import math
import datetime as dt
import requests
import streamlit as st
import folium
from streamlit_folium import st_folium

# -------------------- OPTIONAL GEOCODER (safe on cloud) --------------------
# Thử nạp geopy; nếu không khả dụng (hoặc bị chặn), sẽ tắt tính năng "Nhập địa chỉ"
try:
    from geopy.geocoders import Nominatim
    from geopy.extra.rate_limiter import RateLimiter
    _geolocator = Nominatim(user_agent="route-delivery-sim-ngokhai3205-art", timeout=10)
    _geocode = RateLimiter(_geolocator.geocode, min_delay_seconds=1, swallow_exceptions=True)
except Exception:
    _geolocator, _geocode = None, None

# -------------------- ORS (đường thật) --------------------
import openrouteservice as ors

def ors_client():
    key = (getattr(st, "secrets", {}) or {}).get("ORS_API_KEY") or os.getenv("ORS_API_KEY")
    if not key:
        return None
    try:
        return ors.Client(key=key, base_url="https://api.openrouteservice.org", timeout=20)
    except Exception:
        return None

def get_ors_route(client, origin, destination, profile="driving-car"):
    # ORS dùng (lon, lat)
    coords = [(origin[1], origin[0]), (destination[1], destination[0])]
    res = client.directions(coordinates=coords, profile=profile, format="geojson")
    line = res["features"][0]["geometry"]["coordinates"]  # list [lon, lat]
    path_latlon = [(pt[1], pt[0]) for pt in line]
    summary = res["features"][0]["properties"]["summary"]
    return path_latlon, summary["distance"]/1000.0, summary["duration"]/60.0

# -------------------- CONFIG --------------------
st.set_page_config(page_title="Route Status & Vehicle Recommender", layout="wide")
st.title("🚚 Route Status → Vehicle Recommendation")

# -------------------- HELPERS -------------------
def haversine_km(a, b):
    R = 6371.0
    lat1, lon1 = math.radians(a[0]), math.radians(a[1])
    lat2, lon2 = math.radians(b[0]), math.radians(b[1])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    x = math.sin(dlat/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin(dlon/2)**2
    return 2 * R * math.asin(math.sqrt(x))

def estimate_speed_kmh(traffic, weather, flood):
    v = 35.0
    if traffic == "Medium": v *= 0.8
    elif traffic == "High": v *= 0.6
    if weather == "Rain": v *= 0.85
    elif weather == "Storm": v *= 0.6
    if flood == "Local": v *= 0.85
    elif flood == "Widespread": v *= 0.5
    return max(8.0, v)

def recommend(size, urgency, traffic, weather, flood, dist_km, drone_limit_km):
    s = size.split()[0].lower()
    u = urgency.lower()
    allow_drone = (weather in ["Clear","Rain"]) and (flood!="Widespread") and (dist_km is not None and dist_km<=drone_limit_km)

    if s == "small":
        if u.startswith("critical"):
            return ["Drone","E-bike/Motorbike"] if allow_drone and traffic=="High" else ["E-bike/Motorbike"]
        return (["E-bike/Motorbike"] + (["Drone"] if allow_drone else [])) if (traffic=="High" and weather!="Storm") \
               else (["Motorbike","E-van (short range)"] + (["Drone"] if allow_drone else []))
    if s == "medium":
        if u in ["high","critical (≤2h)"]:
            return ["Motorbike (weatherproof)","Van"] if (weather=="Storm" or flood!="None") \
                   else (["Motorbike","Van"] + (["Drone"] if allow_drone else []))
        return ["Van","Motorbike"] + (["Drone"] if allow_drone else [])
    if s == "large":
        return ["Truck (high clearance)","Van (gầm cao)"] if flood=="Widespread" else ["Van","Truck"]
    return ["Truck","Specialized vehicle"]

# ---------- Auto status (weather/flood/traffic) ----------
def _weather_from_code(code, wind):
    if code in [95,96,99] or wind >= 50: return "Storm"
    if (51 <= code <= 67) or (80 <= code <= 82) or (61 <= code <= 65): return "Rain"
    return "Clear"

def get_weather_and_flood(lat, lon):
    url = "https://api.open-meteo.com/v1/forecast"
    params = {"latitude":lat, "longitude":lon, "current_weather":True,
              "hourly":"precipitation", "past_days":1, "timezone":"auto"}
    r = requests.get(url, params=params, timeout=10); r.raise_for_status()
    js = r.json()
    cw = js["current_weather"]; code = int(cw["weathercode"]); wind = float(cw["windspeed"])
    weather = _weather_from_code(code, wind)
    precip = js.get("hourly",{}).get("precipitation",[])
    precip_sum = sum(p for p in precip if isinstance(p,(int,float)))
    flood = "Widespread" if precip_sum>=100 else ("Local" if precip_sum>=30 else "None")
    hour_local = int(cw["time"][11:13]) if "time" in cw else dt.datetime.now().hour
    tzname = js.get("timezone","local")
    return weather, flood, hour_local, tzname

def estimate_traffic_level(hour_local, weekday, weather):
    if weekday<5 and (7<=hour_local<=9 or 17<=hour_local<=19): level="High"
    elif weekday<5: level="Medium"
    else: level="Low"
    bump = {"Clear":0,"Rain":1,"Storm":2}.get(weather,0)
    order=["Low","Medium","High"]
    return order[min(2, order.index(level)+bump)]

# -------------------- INPUTS --------------------
mode = st.radio("Chọn cách nhập điểm:",
                ["Nhập địa chỉ","Nhập tọa độ (lat, lon)","Chọn địa chỉ mẫu (có sẵn)"],
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

    if _geolocator is None:
        st.warning("⚠️ Tra cứu tọa độ từ địa chỉ có thể bị tắt trên Cloud. Bạn vẫn có thể dùng 'Nhập tọa độ' hoặc 'Địa chỉ mẫu'.")
    else:
        if st.button("📍 Lấy tọa độ từ địa chỉ"):
            with st.spinner("Đang tìm tọa độ..."):
                loc1 = _geocode(start_addr) if start_addr else None
                loc2 = _geocode(dest_addr)  if dest_addr  else None
            if loc1 and loc2:
                st.session_state.geo["origin"] = (loc1.latitude, loc1.longitude)
                st.session_state.geo["destination"] = (loc2.latitude, loc2.longitude)
                st.success("✅ Đã xác định được tọa độ!")
            else:
                st.error("❌ Không tìm thấy. Hãy nhập cụ thể hơn (số nhà, phường/quận, TP).")

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
    origin = (o_lat, o_lon); destination = (d_lat, d_lon)

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
    origin = presets[origin_name]; destination = presets[dest_name]

st.markdown("### Thông tin đơn hàng")
col3, col4, col5 = st.columns(3)
with col3:
    size = st.selectbox("Kích cỡ hàng", ["Small (≤5kg)","Medium (≤20kg)","Large (≤200kg)","Bulky/Over"])
with col4:
    urgency = st.selectbox("Mức khẩn cấp", ["Low","Normal","High","Critical (≤2h)"])
with col5:
    drone_limit = st.number_input("Giới hạn km cho drone", min_value=1, max_value=30, value=10)

# -------------------- AUTO STATUS (OUTPUT) --------------------
computed_status = None
if origin and destination:
    try:
        weather_now, flood_now, hour_local, tzname = get_weather_and_flood(*origin)
        weekday = dt.datetime.utcnow().weekday()
        traffic_now = estimate_traffic_level(hour_local, weekday, weather_now)
        computed_status = {"traffic":traffic_now,"weather":weather_now,"flood":flood_now,
                           "hour":hour_local,"tz":tzname}
        st.markdown(
            f"**Trạng thái tuyến (tự tính)** — "
            f"Traffic: `{traffic_now}` • Weather: `{weather_now}` • Flood: `{flood_now}` "
            f"(giờ địa phương ~ {hour_local}:00, TZ: {tzname})"
        )
    except Exception as e:
        st.warning(f"Không lấy được trạng thái tự động (sẽ dùng mặc định). Lý do: {e}")

# -------------------- PERSIST & ACTION --------------------
if "calc" not in st.session_state:
    st.session_state.calc = None

pressed = st.button("Tính toán & Vẽ tuyến")

if pressed:
    if not (origin and destination):
        st.error("Vui lòng nhập/nhận tọa độ cho cả Điểm xuất phát và Điểm đến trước.")
    else:
        if computed_status:
            traffic, weather, flood = (computed_status["traffic"],
                                       computed_status["weather"],
                                       computed_status["flood"])
        else:
            traffic, weather, flood = "Medium","Clear","None"

        dist_km = haversine_km(origin, destination)
        speed = estimate_speed_kmh(traffic, weather, flood)
        est_minutes = max(1, int((dist_km / speed) * 60))
        recs = recommend(size, urgency, traffic, weather, flood, dist_km, drone_limit)

        st.session_state.calc = {
            "origin":origin, "destination":destination,
            "traffic":traffic, "weather":weather, "flood":flood,
            "size":size, "urgency":urgency, "drone_limit":drone_limit,
            "dist_km":dist_km, "speed":speed, "est_minutes":est_minutes, "recs":recs,
        }

# -------------------- DISPLAY RESULT --------------------
if st.session_state.calc:
    c = st.session_state.calc
    st.success(f"Đề xuất phương tiện: {', '.join(c['recs'])}")
    st.info(f"Quãng đường ước lượng (đường thẳng) ~ {c['dist_km']:.1f} km • "
            f"Thời gian ước lượng ~ {c['est_minutes']} phút (v={c['speed']:.0f} km/h)")

    use_ors = st.checkbox("Dùng tuyến đường thật (OpenRouteService)", value=True)
    profile_label = st.selectbox("Hồ sơ tuyến",
                                 ["Van/Car (driving-car)",
                                  "Motorbike approx (cycling-electric)",
                                  "Truck (driving-hgv)"], index=0)
    profile_map = {
        "Van/Car (driving-car)": "driving-car",
        "Motorbike approx (cycling-electric)": "cycling-electric",
        "Truck (driving-hgv)": "driving-hgv",
    }
    profile = profile_map[profile_label]

    mid = ((c["origin"][0]+c["destination"][0])/2, (c["origin"][1]+c["destination"][1])/2)
    m = folium.Map(location=mid, zoom_start=12)
    folium.Marker(c["origin"], tooltip="Xuất phát").add_to(m)
    folium.Marker(c["destination"], tooltip="Điểm đến").add_to(m)

    drawn_straight = False
    if use_ors:
        client = ors_client()
        if client:
            try:
                path, dist_real_km, time_real_min = get_ors_route(client, c["origin"], c["destination"], profile=profile)
                folium.PolyLine(path, weight=5, tooltip=f"ORS {profile}").add_to(m)
                st.info(f"**Tuyến ORS** ~ {dist_real_km:.1f} km • ~ {int(time_real_min)} phút ({profile})")
            except Exception as e:
                st.warning(f"Không lấy được tuyến ORS (sẽ vẽ đường thẳng). Lý do: {e}")
                drawn_straight = True
        else:
            st.warning("Chưa thiết lập ORS_API_KEY trong Secrets hoặc key bị giới hạn. Đang dùng đường thẳng.")
            drawn_straight = True
    else:
        drawn_straight = True

    if drawn_straight:
        folium.PolyLine([c["origin"], c["destination"]], weight=5, tooltip="Straight line").add_to(m)

    status = f"Traffic: {c['traffic']} • Weather: {c['weather']} • Flood: {c['flood']}"
    mid_marker = ((c["origin"][0]+c["destination"][0])/2, (c["origin"][1]+c["destination"][1])/2)
    folium.Marker(mid_marker, tooltip=status, popup=status).add_to(m)
    st_folium(m, width=920, height=520)

st.caption("Status auto: thời tiết (Open-Meteo) + suy luận ngập theo mưa 24h + giao thông theo giờ cao điểm & thời tiết. ORS vẽ tuyến đường thật (nếu có ORS_API_KEY).")
