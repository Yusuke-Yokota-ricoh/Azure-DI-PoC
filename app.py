import base64
import io as _io
from concurrent.futures import ThreadPoolExecutor

import fitz  # pymupdf
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from PIL import Image

from ocr.analyzer import (
    DISPLAY_FIELDS,
    analyze_layout_regex,
    analyze_layout_gpt,
    analyze_gpt_vision,
)

# ─── 定数 ────────────────────────────────────────────────
DPI = 100  # PDF ラスタライズ解像度（Plotly 用）

_COLOR_RGB: dict[str, tuple[int, int, int]] = {
    "green":  ( 34, 197,  94),
    "blue":   ( 59, 130, 246),
    "purple": (168,  85, 247),
    "orange": (249, 115,  22),
    "red":    (239,  68,  68),
    "layout": (107, 114, 128),
}


# ─── Plotly 描画 ──────────────────────────────────────────
def _render_page_plotly(pdf_bytes: bytes, page_num: int, boxes: list[dict]) -> go.Figure:
    """
    PDF の 1 ページをラスタライズして Plotly Figure に変換する。
    各バウンディングボックスは Scatter トレースとして重ね、
    ホバー時にラベルと読み取りテキストをポップアップ表示する。
    """
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    page = doc[page_num - 1]
    pix = page.get_pixmap(matrix=fitz.Matrix(DPI / 72, DPI / 72))
    img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
    doc.close()

    W, H = img.width, img.height

    # PIL Image → base64 PNG
    buf = _io.BytesIO()
    img.save(buf, format="PNG")
    img_b64 = base64.b64encode(buf.getvalue()).decode()

    fig = go.Figure()

    # 背景画像（座標系: 左上原点、Y 下向き → yaxis を反転）
    fig.add_layout_image(
        source=f"data:image/png;base64,{img_b64}",
        xref="x", yref="y",
        x=0, y=0,
        sizex=W, sizey=H,
        sizing="stretch",
        layer="below",
    )

    # バウンディングボックスをポリゴントレースとして追加
    for box in boxes:
        if box.get("page") != page_num or not box.get("polygon"):
            continue

        poly = box["polygon"]
        # インチ座標 → ピクセル座標（ポリゴンを閉じる）
        xs = [poly[i]     * DPI for i in range(0, len(poly), 2)] + [poly[0] * DPI]
        ys = [poly[i + 1] * DPI for i in range(0, len(poly), 2)] + [poly[1] * DPI]

        rgb   = _COLOR_RGB.get(box.get("color", "layout"), (107, 114, 128))
        style = box.get("style", "filled")

        fill_color = (
            f"rgba({rgb[0]},{rgb[1]},{rgb[2]},0.25)"
            if style == "filled"
            else "rgba(0,0,0,0)"
        )
        line_color = f"rgba({rgb[0]},{rgb[1]},{rgb[2]},0.9)"

        label = box.get("label", "")
        value = box.get("value", "")
        hover = f"<b>{label}</b><br>{value}" if label else value

        fig.add_trace(go.Scatter(
            x=xs, y=ys,
            fill="toself",
            fillcolor=fill_color,
            line=dict(color=line_color, width=2),
            mode="lines",
            text=hover,
            hovertemplate="%{text}<extra></extra>",
            showlegend=False,
        ))

    fig.update_layout(
        margin=dict(l=0, r=0, t=0, b=0),
        xaxis=dict(
            range=[0, W], showgrid=False, zeroline=False,
            showticklabels=False, fixedrange=False,
        ),
        yaxis=dict(
            range=[H, 0],  # 上下反転して画像座標系と一致させる
            showgrid=False, zeroline=False,
            showticklabels=False, scaleanchor="x", fixedrange=False,
        ),
        height=650,
        dragmode="pan",
        plot_bgcolor="white",
    )

    return fig


def _page_count(pdf_bytes: bytes) -> int:
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    n = doc.page_count
    doc.close()
    return n


def _show_pdf_column(pdf_bytes: bytes, boxes: list[dict], key_prefix: str = "pdf"):
    """左カラム: Plotly インタラクティブ図として各ページを表示。ホバーでテキスト表示。"""
    n = _page_count(pdf_bytes)
    for page_num in range(1, n + 1):
        if n > 1:
            st.caption(f"ページ {page_num}")
        fig = _render_page_plotly(pdf_bytes, page_num, boxes)
        st.plotly_chart(
            fig,
            use_container_width=True,
            key=f"{key_prefix}_p{page_num}",
            config={"scrollZoom": True, "displaylogo": False, "modeBarButtonsToRemove": ["select2d", "lasso2d"]},
        )


# ─── UI ヘルパー ──────────────────────────────────────────
def _show_layout_panel(result: dict):
    """右パネル: OCR 抽出テキスト（全文）のみ表示。"""
    st.markdown("**抽出テキスト（Azure Document Intelligence）**")
    st.divider()
    full_text = (result.get("raw_fields") or {}).get("full_text", "")
    st.text(full_text[:5000] + ("..." if len(full_text) > 5000 else ""))


def _show_gpt_panel(result: dict, source_label: str):
    """右パネル: GPT 全文OCR結果（gpt_items）またはフィールド一覧 + 生レスポンス。"""
    st.markdown(f"**抽出結果（{source_label}）**")
    st.divider()

    gpt_items = result.get("gpt_items") or []
    if gpt_items:
        st.dataframe(
            pd.DataFrame(gpt_items).rename(columns={"label": "ラベル", "value": "テキスト"}),
            use_container_width=True,
            hide_index=True,
        )
    else:
        for key, label in DISPLAY_FIELDS:
            field = result.get(key) or {}
            value = field.get("value") or "—"
            st.markdown(f"**{label}**: {value}")

    raw = (result.get("raw_fields") or {}).get("gpt_response", "")
    if raw:
        with st.expander("生データ（GPT レスポンス JSON）"):
            st.text(raw)


def _show_comparison_table(lay_gpt: dict, gpt_vision: dict):
    """全モデルのフィールド値を横並び比較テーブルで表示。"""
    rows = []
    for key, label in DISPLAY_FIELDS:
        rows.append({
            "フィールド":       label,
            "OCR":             "—",
            "OCR + gpt-4o":   lay_gpt.get(key, {}).get("value") or "—",
            "gpt-4o（Vision）": gpt_vision.get(key, {}).get("value") or "—",
        })

    st.dataframe(
        pd.DataFrame(rows),
        use_container_width=True,
        hide_index=True,
    )


# ─── メイン ───────────────────────────────────────────────
st.set_page_config(page_title="請求書 OCR デモ", layout="wide")
st.title("請求書 OCR デモ")
st.caption("Azure Document Intelligence — モデル比較デモ（ボックスにカーソルを当てると読み取りテキストを表示）")

uploaded = st.file_uploader("PDF ファイルをアップロードしてください", type=["pdf"])

if uploaded:
    pdf_bytes = uploaded.read()

    with st.spinner("Azure Document Intelligence / Azure OpenAI で分析中（並列実行）..."):
        try:
            with ThreadPoolExecutor(max_workers=2) as executor:
                f_lay = executor.submit(analyze_layout_regex, pdf_bytes)
                f_vis = executor.submit(analyze_gpt_vision,   pdf_bytes)
                lay        = f_lay.result()
                gpt_vision = f_vis.result()
            lay_gpt = analyze_layout_gpt(lay, gpt_vision)
        except Exception as e:
            st.error(f"分析に失敗しました: {e}")
            st.stop()

    tab1, tab2, tab3, tab4 = st.tabs([
        "OCR（Azure Document Intelligence）",
        "gpt-4o（Azure OpenAI）",
        "OCR + gpt-4o",
        "モデル比較",
    ])

    with tab1:
        st.caption("prebuilt-layout — 行単位ボックス（グレー枠）＋ 正規表現抽出")
        img_col, panel_col = st.columns([3, 2])
        with img_col:
            _show_pdf_column(pdf_bytes, lay["bounding_boxes"], key_prefix="tab1")
        with panel_col:
            _show_layout_panel(lay)

    with tab2:
        st.caption("PDF 画像をそのまま gpt-4o Vision に渡してフィールド抽出")
        img_col, panel_col = st.columns([3, 2])
        with img_col:
            _show_pdf_column(pdf_bytes, [], key_prefix="tab2")
        with panel_col:
            _show_gpt_panel(gpt_vision, "gpt-4o（Azure OpenAI）")

    with tab3:
        st.caption("prebuilt-layout のボックスリスト（テキスト＋座標）を gpt-4o に渡して直接フィールド分類")
        img_col, panel_col = st.columns([3, 2])
        with img_col:
            _show_pdf_column(pdf_bytes, lay_gpt["bounding_boxes"], key_prefix="tab3")
        with panel_col:
            _show_gpt_panel(lay_gpt, "OCR + gpt-4o")

    with tab4:
        st.caption("3つのアプローチの抽出結果を横並びで比較")
        _show_comparison_table(lay_gpt, gpt_vision)
