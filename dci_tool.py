import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from datetime import datetime

# --- 1. 基礎設定 ---
st.set_page_config(page_title="雙元貨幣 (DCI) 戰情室", layout="wide")
st.title("💱 雙元貨幣 (DCI) 歷史勝率與情境回測")
st.markdown("回測區間：**2010/01/01 至今**。支援直覺輸入貨幣對，並獨家計算「**被轉換後立刻對作**」的解套勝率。")
st.divider()

# --- 2. 側邊欄：參數設定 ---
st.sidebar.header("1️⃣ 貨幣對設定")
fx_input = st.sidebar.text_input("輸入外匯標的 (例如: USD/JPY, EUR/USD, USD/TWD)", value="USD/JPY")

clean_fx = fx_input.upper().replace("/", "").replace(" ", "")
ticker = f"{clean_fx}=X"
st.sidebar.caption(f"🔍 系統底層對應 yFinance 代碼: `{ticker}`")

st.sidebar.divider()
st.sidebar.header("2️⃣ DCI 結構條件")
strike_pct = st.sidebar.number_input("主打履約價 (Strike %)", min_value=80.0, max_value=105.0, value=98.5, step=0.5, format="%.1f", help="以進場日匯率為 100% 基準")

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
def get_fx_data(ticker_symbol):
    try:
        df = yf.download(ticker_symbol, start="2010-01-01", progress=False)
        if df.empty: return None, "找不到資料，請確認輸入的貨幣對是否正確 (例: USD/JPY)"
        
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

def run_dci_backtest(df, target_strike_pct, t_days):
    bt = df[['Date', 'Close']].copy()
    bt.columns = ['Start_Date', 'Start_Price']
    
    # 本期期末
    bt['End_Date'] = bt['Start_Date'].shift(-t_days)
    bt['Final_Price'] = bt['Start_Price'].shift(-t_days)
    
    # [關鍵新增] 下一期的期末 (用來計算連續對作)
    bt['Next_Final_Price'] = bt['Start_Price'].shift(-2 * t_days)
    
    bt = bt.dropna(subset=['End_Date', 'Final_Price'])
    
    if bt.empty: return None, None
    
    # 判斷本期是否被轉換
    bt['Strike_Price'] = bt['Start_Price'] * (target_strike_pct / 100)
    bt['Converted'] = bt['Final_Price'] < bt['Strike_Price']
    
    total_trades = len(bt)
    converted_count = bt['Converted'].sum()
    safe_count = total_trades - converted_count
    win_rate = (safe_count / total_trades) * 100
    
    # --- 1. 計算解套天數 ---
    loss_indices = bt[bt['Converted'] == True].index
    recovery_counts = []
    stuck_count = 0
    
    for idx in loss_indices:
        row = bt.loc[idx]
        target_price = row['Strike_Price']
        end_date = row['End_Date']
        
        future_data = df[(df['Date'] > end_date) & (df['Close'] >= target_price)]
        if not future_data.empty:
            days_needed = (future_data.iloc[0]['Date'] - end_date).days
            recovery_counts.append(days_needed)
        else:
            stuck_count += 1

    avg_recovery = np.mean(recovery_counts) if recovery_counts else 0
    
    # --- 2. 計算「連續對作」勝率 ---
    # 篩選出被轉換，且下一期還有歷史數據的樣本
    valid_next_bt = bt[bt['Converted'] & bt['Next_Final_Price'].notna()].copy()
    valid_converted_count = len(valid_next_bt)
    
    if valid_converted_count > 0:
        # A. 原價解套 (下一期匯率 >= 原本的履約價)
        orig_recovery_count = (valid_next_bt['Next_Final_Price'] >= valid_next_bt['Strike_Price']).sum()
        reverse_orig_rate = (orig_recovery_count / valid_converted_count) * 100
        
        # B. 市價對稱對作 (例如原本設定跌 1.5%，現在依新市價設定漲 1.5% 換回)
        reverse_pct = 200 - target_strike_pct
        valid_next_bt['Symmetric_Strike'] = valid_next_bt['Final_Price'] * (reverse_pct / 100)
        symm_recovery_count = (valid_next_bt['Next_Final_Price'] >= valid_next_bt['Symmetric_Strike']).sum()
        reverse_symm_rate = (symm_recovery_count / valid_converted_count) * 100
    else:
        reverse_orig_rate = 0
        reverse_symm_rate = 0
    
    return bt, {
        'total': total_trades,
        'safe': safe_count,
        'converted': converted_count,
        'win_rate': win_rate,
        'avg_recovery_days': avg_recovery,
        'stuck_count': stuck_count,
        'reverse_orig_rate': reverse_orig_rate,
        'reverse_symm_rate': reverse_symm_rate
    }

# --- 4. 執行與畫面呈現 ---
if run_btn:
    st.markdown(f"### 📌 標的：{fx_input.upper()}")
    
    with st.spinner("抓取 2010 年至今歷史匯率並計算中..."):
        df, err = get_fx_data(ticker)
        
    if err:
        st.error(err)
    else:
        current_spot = df['Close'].iloc[-1]
        current_strike = current_spot * (strike_pct / 100)
        
        bt_data, stats = run_dci_backtest(df, strike_pct, trading_days)
        
        if not bt_data is None:
            stuck_ratio = (stats['stuck_count'] / stats['converted']) * 100 if stats['converted'] > 0 else 0
                
            # --- 重點數據 ---
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("最新匯率 (Spot)", f"{current_spot:.4f}")
            c2.metric(f"設定履約價 ({strike_pct}%)", f"{current_strike:.4f}")
            c3.metric(f"歷史勝率 (不轉換)", f"{stats['win_rate']:.1f}%")
            if stats['converted'] > 0:
                c4.metric("平均解套等待", f"{stats['avg_recovery_days']:.0f} 天", help=f"有 {stuck_ratio:.1f}% 至今未解套", delta_color="inverse")
            else:
                c4.metric("平均解套等待", "0 天", help="歷史上未曾跌破履約價")
            
            st.divider()
            
            # --- 圖表並排呈現 ---
            col_chart1, col_chart2 = st.columns([7, 3])
            
            with col_chart1:
                plot_df = df.tail(500)
                fig_spot = go.Figure()
                fig_spot.add_trace(go.Scatter(x=plot_df['Date'], y=plot_df['Close'], name="匯率走勢", line=dict(color="#2980b9")))
                fig_spot.add_hline(y=current_strike, line_dash="dash", line_color="red", annotation_text=f"本期履約價 {current_strike:.4f}")
                fig_spot.update_layout(title=f"{fx_input.upper()} 走勢 (圖表擷取近兩年便於觀察履約距離)", height=350, margin=dict(l=0, r=20, t=40, b=0))
                st.plotly_chart(fig_spot, use_container_width=True)
                
            with col_chart2:
                fig_pie = go.Figure(data=[go.Pie(
                    labels=['安全 (不轉換)', '被轉換'],
                    values=[stats['safe'], stats['converted']],
                    hole=.4,
                    marker_colors=['#2ecc71', '#e74c3c']
                )])
                fig_pie.update_layout(title=f"自 2010 至今勝率分佈", height=350, margin=dict(l=0, r=0, t=40, b=0))
                st.plotly_chart(fig_pie, use_container_width=True)
            
            # --- AI 解讀與銷售話術 ---
            st.info(f"""
            **💡 破解「被套牢」疑慮的銷售話術 (Sales Talk)：**
            
            「很多客戶會問：『如果真的不幸遇到那 {(100 - stats['win_rate']):.1f}% 被換過去了怎麼辦？』」
            「歷史數據告訴我們，您有兩套解套策略：
            
            👉 **策略 1 (順勢收息)**：如果我們依據新的市價，設定對稱的履約價立刻對作，下一期就有高達 **{stats['reverse_symm_rate']:.1f}%** 的機率成功換回原本的幣別，而且還能多賺一期高息！
            👉 **策略 2 (保本防守)**：如果您堅持掛回『原本的履約價』，就算匯率跌深，歷史上也有 **{stats['reverse_orig_rate']:.1f}%** 的機率能在一期內完全不虧匯差解套。
            
            所以 DCI 是一個進可攻、退可守的靈活工具！」
            """)

            # --- 多重履約價戰情表 ---
            st.markdown("### 📊 不同履約價情境比較表 (數據基準：2010 至今)")
            st.caption(f"針對 **{tenor_label}** 天期，為您試算不同防守深度與對作解套機率：")
            
            compare_strikes = [99.5, 99.0, 98.5, 98.0, 95.0]
            if strike_pct not in compare_strikes:
                compare_strikes.append(strike_pct)
                compare_strikes.sort(reverse=True)
                
            compare_results = []
            for s_pct in compare_strikes:
                _, s_stats = run_dci_backtest(df, s_pct, trading_days)
                if s_stats:
                    compare_results.append({
                        "履約價設定": f"{s_pct}%",
                        "對應匯率價位": f"{(current_spot * (s_pct/100)):.4f}",
                        "期初不轉換機率": f"{s_stats['win_rate']:.1f}%",
                        "不幸被換機率": f"{(100 - s_stats['win_rate']):.1f}%",
                        "下期市價對作換回率": f"{s_stats['reverse_symm_rate']:.1f}%" if s_stats['converted'] > 0 else "-",
                        "下期掛原價解套率": f"{s_stats['reverse_orig_rate']:.1f}%" if s_stats['converted'] > 0 else "-"
                    })
            
            df_compare = pd.DataFrame(compare_results)
            st.dataframe(df_compare, use_container_width=True, hide_index=True)
            
else:
    st.info("👈 請在左側輸入直覺的外匯代碼 (例: USD/JPY) ，按下「開始回測」。")
