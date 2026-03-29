import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from datetime import datetime

# --- 1. 基礎設定 ---
st.set_page_config(page_title="雙元貨幣 (DCI) 戰情室", layout="wide")
st.title("💱 雙元貨幣 (DCI) 歷史勝率與情境回測")
st.markdown("針對外匯 DCI 設計的專屬回測模組。檢視不同天期、履約價設定下的「勝率」與「解套時間」。")
st.divider()

# --- 2. 側邊欄：參數設定 ---
st.sidebar.header("1️⃣ 貨幣對設定")
fx_pair = st.sidebar.selectbox(
    "選擇外匯標的 (Base/Alternate)", 
    ["USDTWD=X (美元/台幣)", "JPY=X (美元/日圓)", "EURUSD=X (歐元/美元)", "AUDUSD=X (澳幣/美元)", "GBPUSD=X (英鎊/美元)"]
)
ticker = fx_pair.split(" ")[0]

st.sidebar.divider()
st.sidebar.header("2️⃣ DCI 結構條件")
strike_pct = st.sidebar.number_input("履約價 (Strike %)", min_value=80.0, max_value=105.0, value=98.5, step=0.5, format="%.1f", help="以進場日匯率為 100% 基準")

tenor_options = {"7 天 (一週)": 5, "14 天 (兩週)": 10, "30 天 (一個月)": 22, "60 天 (兩個月)": 44, "90 天 (三個月)": 66}
tenor_label = st.sidebar.selectbox("合約天期 (Tenor)", list(tenor_options.keys()))
trading_days = tenor_options[tenor_label]

st.sidebar.divider()
st.sidebar.header("3️⃣ 投資設定")
principal = st.sidebar.number_input("投資本金", value=100000, step=10000)
coupon_pa = st.sidebar.number_input("DCI 年化收益率 (%)", value=10.0, step=0.5)

run_btn = st.sidebar.button("🚀 開始回測", type="primary")

# --- 3. 核心函數 ---
@st.cache_data(ttl=3600)
def get_fx_data(ticker):
    try:
        df = yf.download(ticker, start="2010-01-01", progress=False)
        if df.empty: return None, "找不到資料"
        
        df = df.reset_index()
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df = df.loc[:, ~df.columns.duplicated()]
        
        df['Date'] = pd.to_datetime(df['Date'])
        df['Close'] = pd.to_numeric(df['Close'], errors='coerce')
        df = df.dropna(subset=['Close'])
        return df, None
    except Exception as e:
        return None, str(e)

def run_dci_backtest(df, strike_pct, t_days):
    bt = df[['Date', 'Close']].copy()
    bt.columns = ['Start_Date', 'Start_Price']
    
    bt['End_Date'] = bt['Start_Date'].shift(-t_days)
    bt['Final_Price'] = bt['Start_Price'].shift(-t_days)
    bt = bt.dropna()
    
    if bt.empty: return None, None
    
    bt['Strike_Price'] = bt['Start_Price'] * (strike_pct / 100)
    bt['Converted'] = bt['Final_Price'] < bt['Strike_Price']
    
    # 統計勝率
    total_trades = len(bt)
    converted_count = bt['Converted'].sum()
    safe_count = total_trades - converted_count
    win_rate = (safe_count / total_trades) * 100
    
    # --- 計算解套時間 (Recovery Time) ---
    loss_indices = bt[bt['Converted'] == True].index
    recovery_counts = []
    stuck_count = 0
    
    for idx in loss_indices:
        row = bt.loc[idx]
        target_price = row['Strike_Price']
        end_date = row['End_Date']
        
        # 尋找期末日之後，匯率重新站回履約價的日期
        future_data = df[(df['Date'] > end_date) & (df['Close'] >= target_price)]
        
        if not future_data.empty:
            days_needed = (future_data.iloc[0]['Date'] - end_date).days
            recovery_counts.append(days_needed)
        else:
            stuck_count += 1

    avg_recovery = np.mean(recovery_counts) if recovery_counts else 0
    
    stats = {
        'total': total_trades,
        'safe': safe_count,
        'converted': converted_count,
        'win_rate': win_rate,
        'avg_recovery_days': avg_recovery,
        'stuck_count': stuck_count
    }
    
    return bt, stats

# --- 4. 執行與畫面呈現 ---
if run_btn:
    st.markdown(f"### 📌 標的：{fx_pair.split(' ')[1].replace('(','').replace(')','')} ({ticker})")
    
    with st.spinner("抓取歷史匯率並計算中..."):
        df, err = get_fx_data(ticker)
        
    if err:
        st.error(f"資料讀取失敗: {err}")
    else:
        current_spot = df['Close'].iloc[-1]
        current_strike = current_spot * (strike_pct / 100)
        
        bt_data, stats = run_dci_backtest(df, strike_pct, trading_days)
        
        if not bt_data is None:
            # 計算未解套比例
            stuck_ratio = 0
            if stats['converted'] > 0:
                stuck_ratio = (stats['stuck_count'] / stats['converted']) * 100
                
            # --- 重點數據 ---
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("最新匯率 (Spot)", f"{current_spot:.4f}")
            c2.metric(f"設定履約價 ({strike_pct}%)", f"{current_strike:.4f}")
            c3.metric(f"歷史勝率 (不轉換)", f"{stats['win_rate']:.1f}%")
            
            # 顯示解套天數
            if stats['converted'] > 0:
                c4.metric("平均解套等待天數", f"{stats['avg_recovery_days']:.0f} 天", help=f"被轉換後，匯率回到履約價所需的平均日曆天數 (有 {stuck_ratio:.1f}% 至今未解套)", delta_color="inverse")
            else:
                c4.metric("平均解套等待天數", "0 天", help="歷史上未曾跌破履約價")
            
            # --- 收益試算 ---
            st.markdown("#### 💰 單期收益試算")
            actual_days = int(tenor_label.split(' ')[0])
            period_yield = (coupon_pa / 100) * (actual_days / 365)
            period_income = principal * period_yield
            
            m1, m2 = st.columns(2)
            m1.metric(f"若【未跌破】(機率 {stats['win_rate']:.1f}%)", f"領回本金 + 息收 ${period_income:,.0f}")
            m2.metric(f"若【跌破】(機率 {(100 - stats['win_rate']):.1f}%)", "轉換為弱勢貨幣 + 領息", help="將面臨匯差風險")
            st.divider()
            
            # --- 圖表並排呈現 ---
            col_chart1, col_chart2 = st.columns([7, 3])
            
            with col_chart1:
                plot_df = df.tail(500)
                fig_spot = go.Figure()
                fig_spot.add_trace(go.Scatter(x=plot_df['Date'], y=plot_df['Close'], name="匯率走勢", line=dict(color="#2980b9")))
                fig_spot.add_hline(y=current_strike, line_dash="dash", line_color="red", annotation_text=f"本期履約價 {current_strike:.4f}")
                fig_spot.update_layout(title=f"{ticker} 近兩年走勢與本次履約價位置", height=350, margin=dict(l=0, r=20, t=40, b=0))
                st.plotly_chart(fig_spot, use_container_width=True)
                
            with col_chart2:
                fig_pie = go.Figure(data=[go.Pie(
                    labels=['安全 (不轉換)', '被轉換'],
                    values=[stats['safe'], stats['converted']],
                    hole=.4,
                    marker_colors=['#2ecc71', '#e74c3c']
                )])
                fig_pie.update_layout(title="歷史滾動勝率分佈", height=350, margin=dict(l=0, r=0, t=40, b=0))
                st.plotly_chart(fig_pie, use_container_width=True)
            
            # --- AI 解讀 (含解套時間) ---
            st.info(f"""
            **💡 教育訓練/銷售話術 (Sales Talk)：**
            我們回測了自 2010 年以來的每日歷史數據。如果您承作 **{tenor_label}** 的 DCI，並將履約價設定在期初匯率的 **{strike_pct}%**：
            1. **高防禦力**：在過去 **{stats['total']}** 次的歷史情境中，勝率高達 **{stats['win_rate']:.1f}%**，能安穩拿回本金與利息。
            2. **解套時間短**：就算不幸遇到極端行情被轉換（機率 {(100 - stats['win_rate']):.1f}%），根據歷史經驗，平均只要等待 **{stats['avg_recovery_days']:.0f} 天**，匯率就能重新站回您的履約成本價，解套機率非常高。
            *(註：被轉換的案例中，約有 {stuck_ratio:.1f}% 遇到長期趨勢反轉，截至目前尚未解套)*
            """)
else:
    st.info("👈 請在左側設定 DCI 參數，按下「開始回測」。")
