from datetime import datetime, timedelta
import os
import holidays
from geopy.geocoders import Nominatim
import openmeteo_requests
import pandas as pd
import requests_cache
from retry_requests import retry
from dotenv import load_dotenv
import streamlit as st
import streamlit.components.v1 as components

# .env ファイルから環境変数を読み込む
load_dotenv()

# ページの幅を広げる設定
st.set_page_config(page_title="お天気日記", layout="wide")

# --- 上部の余白およびタブのCSS設定 ---
st.markdown("""
    <style>
        /* 右上のメニューボタンと右下のStreamlitロゴ・フッターを非表示にする */
        #MainMenu {visibility: hidden;}
        footer {visibility: hidden;}
        header {visibility: hidden;}
        
        * --- 追加：右下のStreamlitバッジやステータスウィジェットを非表示にする --- */
        .stDeployButton {display: none;}
        [data-testid="stStatusWidget"] {visibility: hidden;}
        footer {visibility: hidden !important;}
        #is-managed-hosting-badge {display: none !important;}
        
        /* Streamlitのデフォルト上部余白を削減 */
        .block-container {
            padding-top: 1.5rem !important;
        }
        /* タブの背景とスタイルを調整（選ぶ前から白くする） */
        .stTabs [data-baseweb="tab-list"] {
            gap: 8px;
            background-color: transparent;
        }
        .stTabs [data-baseweb="tab"] {
            background-color: #ffffff !important;
            border-radius: 6px 6px 0px 0px;
            padding: 10px 20px;
            color: #333333 !important;
            border: 1px solid #ced4da;
            border-bottom: none;
            opacity: 1 !important;
        　　font-size: 1.2em !important; /* ← ここを追加（お好みで 1.2em〜1.3em に調整可能） */
        }
        .stTabs [aria-selected="true"] {
            background-color: #ffffff !important;
            border-top: 3px solid #007bff !important;
            font-weight: bold;
        }
        .stTabs [aria-selected="false"] {
            background-color: #ffffff !important;
            color: #666666 !important;
        }
        /* タブコンテンツ全体の背景を白に統一 */
        .stTabs [data-baseweb="tab-panel"] {
            background-color: #ffffff;
            padding: 15px;
            border-radius: 0px 8px 8px 8px;
            border: 1px solid #ced4da;
            border-top: none;
        }
    </style>
""", unsafe_allow_html=True)

# --- 1. 地域設定（住所または緯度経度）から座標を取得する関数 ---
@st.cache_data
def get_lat_lon(location_str):
    location_str = location_str.strip()
    if "," in location_str:
        try:
            parts = location_str.split(",")
            lat = float(parts[0].strip())
            lon = float(parts[1].strip())
            return lat, lon
        except ValueError:
            pass
    
    geolocator = Nominatim(user_agent="weather_diary_app")
    try:
        location = geolocator.geocode(location_str)
        if location:
            return location.latitude, location.longitude
    except Exception:
        pass
    
    return 35.6895, 139.6917

# --- 2. 絵文字変換・ヘルパー関数 ---
def get_weather_emoji(weather_code, precipitation=0):
    if weather_code in [95, 96, 99]:
        return "🌩️" if precipitation < 10 else "⛈️"
    elif weather_code in [71, 73, 75, 77, 85, 86]:
        return "❄️"
    elif weather_code in [51, 53, 55, 56, 57, 61, 63, 80, 81]:
        return "🌧️" if precipitation < 10 else "☔"
    elif weather_code in [65, 82]:
        return "☔"
    elif weather_code == 0:
        return "☀️"
    elif weather_code == 1:
        return "🌤️"
    elif weather_code == 2:
        return "🌥️"
    elif weather_code == 3:
        return "☁️"
    else:
        return "☁️"

# --- 3. データ取得関数 ---
@st.cache_data
def fetch_month_weather(lat, lon, year, month):
    if month == 12:
        next_month_date = datetime(year + 1, 1, 1)
    else:
        next_month_date = datetime(year, month + 1, 1)
    
    start_date = datetime(year, month, 1).date()
    end_date = (next_month_date - timedelta(days=1)).date()
    
    today = datetime.now().date()
    if end_date >= today:
        end_date = today - timedelta(days=1)
    if start_date > end_date:
        return None, None

    cache_session = requests_cache.CachedSession(".cache", expire_after=3600)
    retry_session = retry(cache_session, retries=3, backoff_factor=0.2)
    openmeteo = openmeteo_requests.Client(session=retry_session)

    url = "https://archive-api.open-meteo.com/v1/archive"
    params = {
        "latitude": lat,
        "longitude": lon,
        "start_date": start_date.strftime("%Y-%m-%d"),
        "end_date": end_date.strftime("%Y-%m-%d"),
        "daily": ["temperature_2m_max", "temperature_2m_min", "precipitation_sum", "weather_code"],
        "hourly": ["temperature_2m", "precipitation", "wind_speed_10m", "weather_code"],
        "wind_speed_unit": "ms",  # ← 風速の単位を m/s に指定
        "timezone": "Asia/Tokyo"
    }

    try:
        responses = openmeteo.weather_api(url, params=params)
        response = responses[0]
        
        daily = response.Daily()
        daily_time = pd.date_range(
            start=pd.to_datetime(daily.Time(), unit="s", utc=True),
            end=pd.to_datetime(daily.TimeEnd(), unit="s", utc=True),
            freq=pd.Timedelta(seconds=daily.Interval()),
            inclusive="left"
        ).tz_convert("Asia/Tokyo")
        
        df_daily = pd.DataFrame({
            "date": daily_time.date,
            "max_temp": daily.Variables(0).ValuesAsNumpy(),
            "min_temp": daily.Variables(1).ValuesAsNumpy(),
            "precipitation": daily.Variables(2).ValuesAsNumpy(),
            "weather_code": daily.Variables(3).ValuesAsNumpy()
        })
        
        hourly = response.Hourly()
        hourly_time = pd.date_range(
            start=pd.to_datetime(hourly.Time(), unit="s", utc=True),
            end=pd.to_datetime(hourly.TimeEnd(), unit="s", utc=True),
            freq=pd.Timedelta(seconds=hourly.Interval()),
            inclusive="left"
        ).tz_convert("Asia/Tokyo")
        
        df_hourly = pd.DataFrame({
            "datetime": hourly_time,
            "date": hourly_time.date,
            "hour": hourly_time.hour,
            "temperature": hourly.Variables(0).ValuesAsNumpy(),
            "precipitation": hourly.Variables(1).ValuesAsNumpy(),
            "wind_speed": hourly.Variables(2).ValuesAsNumpy(),
            "weather_code": hourly.Variables(3).ValuesAsNumpy()
        })
        
        return df_daily, df_hourly
    except Exception as e:
        st.error(f"API取得エラー詳細: {e}")
        return None, None

# --- 4. UI構築 ---
yesterday = datetime.now().date() - timedelta(days=1)
start_available_date = datetime(2020, 1, 1).date()

if "selected_date" not in st.session_state:
    st.session_state.selected_date = yesterday

# タイトルと日付選択（カレンダー入力）を横並びにする
col_title, col_select, _ = st.columns([1.5, 2, 2.5])

with col_title:
    st.markdown("## ☀️ お天気日記")

with col_select:
    selected_date_box = st.date_input(
        "日付選択",
        value=st.session_state.selected_date,
        min_value=start_available_date,
        max_value=yesterday,
        label_visibility="collapsed"
    )

if selected_date_box != st.session_state.selected_date:
    st.session_state.selected_date = selected_date_box
    st.rerun()

# 選択された日付から年と月を決定
selected_year = st.session_state.selected_date.year
selected_month = st.session_state.selected_date.month

target_location = os.getenv("LOCATION", "長崎県西彼杵郡長与町")
lat, lon = get_lat_lon(target_location)

# データのロード
df_daily, df_hourly = fetch_month_weather(lat, lon, selected_year, selected_month)

if df_daily is not None and not df_daily.empty:
    jp_holidays = holidays.JP(years=selected_year)

    st.markdown("---")
    
    # 年月日の直下にタブを配置
    tab_calendar, tab_hourly = st.tabs([f" 　 🗓 {selected_month}月カレンダー　  ", " 　 🕒 1時間別の詳細 　 "])
    
    days_of_week = ["日", "月", "火", "水", "木", "金", "土"]
    
    # --- タブ1: 月間カレンダー ---
    with tab_calendar:
        month_start_date = datetime(selected_year, selected_month, 1).date()
        if selected_month == 12:
            next_month_start = datetime(selected_year + 1, 1, 1).date()
        else:
            next_month_start = datetime(selected_year, selected_month + 1, 1).date()
        total_days = (next_month_start - month_start_date).days

        daily_cards = ""
        for d in range(total_days):
            current_date = month_start_date + timedelta(days=d)
            w_idx = current_date.weekday()
            dow_idx = (w_idx + 1) % 7
            dow_str = days_of_week[dow_idx]
            
            is_sunday = (dow_idx == 0)
            is_saturday = (dow_idx == 6)
            is_holiday = current_date in jp_holidays
            
            color = "#333333"
            if is_sunday or is_holiday:
                color = "#ff4d4d"
            elif is_saturday:
                color = "#4d79ff"
                
            day_data = df_daily[df_daily["date"] == current_date]
            if not day_data.empty:
                row = day_data.iloc[0]
                max_t = f"{row['max_temp']:.1f}<span style='font-size: 0.75em;'>℃</span>"
                min_t = f"{row['min_temp']:.1f}<span style='font-size: 0.75em;'>℃</span>"
                
                # 時間別データから午前・午後それぞれの絵文字を算出
                day_h = df_hourly[df_hourly["date"] == current_date]
                if not day_h.empty:
                    am_h = day_h[(day_h["hour"] >= 6) & (day_h["hour"] <= 11)]
                    pm_h = day_h[(day_h["hour"] >= 12) & (day_h["hour"] <= 17)]
                    
                    if not am_h.empty:
                        am_code = int(am_h["weather_code"].mode()[0] if not am_h["weather_code"].mode().empty else am_h.iloc[0]["weather_code"])
                        am_precip = am_h["precipitation"].max()
                        emoji_am = get_weather_emoji(am_code, am_precip)
                    else:
                        emoji_am = get_weather_emoji(row['weather_code'], row['precipitation'])
                        
                    if not pm_h.empty:
                        pm_code = int(pm_h["weather_code"].mode()[0] if not pm_h["weather_code"].mode().empty else pm_h.iloc[0]["weather_code"])
                        pm_precip = pm_h["precipitation"].max()
                        emoji_pm = get_weather_emoji(pm_code, pm_precip)
                    else:
                        emoji_pm = get_weather_emoji(row['weather_code'], row['precipitation'])
                else:
                    emoji_am = get_weather_emoji(row['weather_code'], row['precipitation'])
                    emoji_pm = emoji_am
            else:
                max_t, min_t, emoji_am, emoji_pm = "", "", "", ""

            border_style = "2px solid #007bff" if st.session_state.selected_date == current_date else "1px solid #ced4da"
            bg_color = "#f0f8ff" if st.session_state.selected_date == current_date else "#ffffff"

            # 各カードに一意のIDを付与
            card_id = f"day-{current_date.strftime('%Y%m%d')}"

            daily_cards += f"""
            <div id="{card_id}" style="background-color: {bg_color}; border: {border_style}; border-radius: 8px; padding: 12px 20px; margin-bottom: 10px; display: flex; justify-content: space-between; align-items: center; font-family: sans-serif; box-shadow: 0 1px 3px rgba(0,0,0,0.05);">
                <div style="font-weight: bold; color: {color}; font-size: 1.15em; width: 90px; text-align: center; line-height: 1.2;">
                    <div style="font-size: 1.2em;">{current_date.day}</div>
                    <div style="font-size: 0.85em; opacity: 0.85;">({dow_str})</div>
                </div>
                <div style="display: flex; align-items: center; justify-content: center; gap: 8px; flex-grow: 1;">
                    <span style="font-size: 2em;" title="午前">{emoji_am}</span>
                    <span style="font-size: 1.3em; color: #6c757d; font-weight: bold;">／</span>
                    <span style="font-size: 2em;" title="午後">{emoji_pm}</span>
                </div>
                <div style="display: flex; justify-content: flex-end; gap: 20px; width: 220px; align-items: center;">
                    <div style="font-size: 1.2em; font-weight: bold; color: #d9534f; text-align: right;">{max_t}</div>
                    <div style="font-size: 1.2em; font-weight: bold; color: #5bc0de; text-align: right;">{min_t}</div>
                </div>
            </div>
            """

        selected_id = f"day-{st.session_state.selected_date.strftime('%Y%m%d')}"

        calendar_html = f"""
        <div id="calendar-container" style="display: flex; flex-direction: column; max-height: 550px; overflow-y: auto; padding: 4px;">
            {daily_cards}
        </div>
        <script>
            window.addEventListener('DOMContentLoaded', () => {{
                const el = document.getElementById("{selected_id}");
                const container = document.getElementById("calendar-container");
                if (el && container) {{
                    container.scrollTop = el.offsetTop - container.offsetTop;
                }}
            }});
        </script>
        """
        components.html(calendar_html, height=580)

    # --- タブ2: 1時間別詳細 ---
    with tab_hourly:
        sel_date = st.session_state.selected_date
        st.markdown(f"#### 🕒 {sel_date} の詳細")
        
        day_hourly = df_hourly[df_hourly["date"] == sel_date]
        
        if not day_hourly.empty:
            hourly_cards = ""
            for _, h_row in day_hourly.iterrows():
                h_time = f"{int(h_row['hour'])}時"
                h_emoji = get_weather_emoji(h_row['weather_code'], h_row['precipitation'])
                h_temp = f"{h_row['temperature']:.1f}<span style='font-size: 0.75em;'>℃</span>"
                h_rain = f"{h_row['precipitation']:.1f} mm"
                h_wind = f"🍃 {h_row['wind_speed']:.1f} m/s"
                
                hourly_cards += f"""
                <div style="background-color: #ffffff; border: 1px solid #ced4da; border-radius: 6px; padding: 12px 20px; margin-bottom: 8px; display: flex; justify-content: space-between; align-items: center; font-family: sans-serif;">
                    <div style="font-weight: bold; font-size: 1.1em; color: #333; width: 60px;">{h_time}</div>
                    <div style="font-size: 1.8em;">{h_emoji}</div>
                    <div style="font-size: 1.2em; font-weight: bold; color: #d9534f; width: 80px; text-align: right;">{h_temp}</div>
                    <div style="font-size: 0.95em; color: #6c757d; width: 100px; text-align: right;">💧{h_rain}</div>
                    <div style="font-size: 0.95em; color: #6c757d; width: 100px; text-align: right;">{h_wind}</div>
                </div>
                """
            
            hourly_html = f"""
            <div style="display: flex; flex-direction: column; max-height: 520px; overflow-y: auto; padding: 4px;">
                {hourly_cards}
            </div>
            """
            components.html(hourly_html, height=550)
        else:
            st.info("選択された日の時間別データがありません。")

else:
    st.info("指定された期間のデータが取得できませんでした。別の日付を選択してお試しください。")