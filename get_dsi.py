# dsi_fetcher_app.py
# ------------------------------------------------------------
# Clarivate Patents Search API（Publication number / DWPI Accession number 対応）
# ・メイン：ファイルアップロード／テキスト入力／実行ボタン／進捗／結果表示／CSVダウンロード
# ・サイドバー：API設定／取得フィールド／パラメータ／詳細ログ
# ・CSVダウンロード後も表が残るよう、結果を st.session_state に保持
# ------------------------------------------------------------
import os
import json
import time
import random
import csv
from collections import defaultdict
from datetime import datetime
from io import StringIO

import requests
import streamlit as st
import pandas as pd

# ---------------- Page Config ----------------
st.set_page_config(page_title="Derwent Strength Index Fetcher（PubNo / DWPI Accession）", layout="wide")

# ---------------- Session State Init ----------------
# ダウンロード後の再実行でも結果を維持するために初期化
if "df" not in st.session_state:
    st.session_state.df = None
if "rows" not in st.session_state:
    st.session_state.rows = None
if "csv_str" not in st.session_state:
    st.session_state.csv_str = None
if "ts" not in st.session_state:
    st.session_state.ts = None
if "log_lines" not in st.session_state:
    st.session_state.log_lines = []

# ---------------- Sidebar: 設定 ----------------
st.sidebar.title("設定")

# API設定
DEFAULT_API_URL = "https://api.clarivate.com/patents/search/"
# 代替の古い/別形態のエンドポイントがある場合は必要に応じて切替
api_url = DEFAULT_API_URL

def get_api_key() -> str:
    # Cloud/ローカルいずれでも st.secrets が最優先
    try:
        return st.secrets["IP_DATA_API"]
    except Exception:
        # ローカルで環境変数を使う場合のフォールバック
        return os.environ.get("IP_DATA_API", "")

api_key = get_api_key()
api_key = st.sidebar.text_input("X-ApiKey", value=api_key, type="password")

# タイムアウト設定
with st.sidebar.expander("タイムアウト", expanded=False):
    timeout_connect = st.number_input("接続タイムアウト(秒)", min_value=1, value=10, step=1, key="TIMEOUT_CONNECT")
    timeout_read    = st.number_input("読み取りタイムアウト(秒)", min_value=10, value=90, step=1, key="TIMEOUT_READ")

# 注意：headers は毎回最新の api_key を反映する
def build_headers(x_api_key: str) -> dict:
    return {"Accept": "application/json", "Content-Type": "application/json", "X-ApiKey": x_api_key}

# 取得フィールド（必要に応じて追加）
DEFAULT_FIELDS_BASE = [
    # "GUID", "DWPI_ACCESSION_NUMBER",  # join key は内部で必ず補完されるため UI から外してもOK
    "PUBLICATION_NUMBER",
    "DSI_STRENGTH_INDEX",
    "DSI_INVENTION_GLOBALIZATION_SCORE", "DSI_INVENTION_INFLUENCE_SCORE",
    "DSI_INVENTION_SUCCESS_SCORE", "DSI_TECHNICAL_DISTINCTIVENESS_SCORE",
    "DSI_AVERAGE_SCORE", "DSI_YEARS_REMAINING", "DSI_AGE_DISCOUNT",
]
with st.sidebar.expander("取得フィールド", expanded=False):
    selected_fields = st.multiselect("Fields", DEFAULT_FIELDS_BASE, default=DEFAULT_FIELDS_BASE)

# パラメータ（Expanderで初期は閉じる）
with st.sidebar.expander("パラメータ", expanded=False):
    ID_CHUNK     = st.number_input(
        "ID_CHUNK（IDを分割処理する単位）",
        min_value=1, value=2000, step=1, key="ID_CHUNK"
    )
    FIELD_CHUNK  = st.number_input(
        "FIELD_CHUNK（1回に要求するフィールド数）",
        min_value=1, value=14, step=1, key="FIELD_CHUNK"
    )
    MAX_RETRIES  = st.number_input(
        "最大リトライ回数",
        min_value=1, value=4, step=1, key="MAX_RETRIES"
    )
    backoff_base = st.number_input(
        "バックオフ基数",
        min_value=0.5, value=1.0, step=0.5, key="BACKOFF_BASE"
    )

# 詳細ログ（サイドバー）
log_box_sidebar = st.sidebar.empty()

# ---------------- Main: タイトル・入力・実行・進捗・結果 ----------------
st.title("Derwent Strength Index Fetcher")
st.caption("Clarivate Patents Search API を用いて、Publication number または DWPI Accession number のリストから DSI 関連フィールドを取得します。")

# 入力キーの選択
id_type = st.radio("検索キー（ID 種別）", ["Publication number", "DWPI accession number"], horizontal=True, index=0)
if id_type == "Publication number":
    field_name = "PUBLICATION_NUMBER"
    example_text = "例:\nWO2021243294A1\nJP07737400B2\nUS20210374460A1"
    page_hint = "Publication number（1行＝1件）"
else:
    field_name = "DWPI_ACCESSION_NUMBER"
    example_text = "例:\n2019422485\n1990099416\n2013F51294\n2008N95044"
    page_hint = "DWPI accession number（1行＝1件）"

# 入力（メイン）：ファイルアップロード＋テキスト
st.subheader("入力")
uploaded = st.file_uploader("テキストファイルをアップロード（1行＝1件、# 行はコメント）", type=["txt"])
st.text("または下のテキストボックスに貼り付け（アップロードがあればそちらを優先）")
ids_text = st.text_area(page_hint, height=160, placeholder=example_text)

# 実行／結果クリア ボタン（メイン）
col_run = st.columns(2)
with col_run[0]:
    run = st.button("実行")
with col_run[1]:
    clear = st.button("結果をクリア")

# メインの進捗表示
progress_main = st.empty()     # st.progress のプレースホルダ
status_main   = st.empty()     # ステータス文表示

# クリア操作
if clear:
    st.session_state.df = None
    st.session_state.rows = None
    st.session_state.csv_str = None
    st.session_state.ts = None
    st.session_state.log_lines = []
    status_main.write("結果をクリアしました。")

# ---------------- Helpers ----------------
def chunked(iterable, n):
    for i in range(0, len(iterable), n):
        yield iterable[i : i + n]

def _normalize_ids(raw_ids):
    """空行・コメント行を除外し、すべて 'str' に強制変換してトリム"""
    ids = []
    for x in raw_ids:
        s = str(x).strip()
        if s and not s.startswith("#"):
            ids.append(s)
    return ids

def _post_with_in_fallback(values_list, fields, field_name, headers, timeout):
    """
    Clarivate Patents Search API へ POST。
    - まず VALUE を配列（list[str]）で送る
    - 非2xxなら、DWPI_ACCESSION_NUMBER のときはカンマ区切り文字列で再送（4xx/5xxどちらでも）
    """
    payload_arr = {
        "QUERY": [{"ALG": "BASIC", "FIELD": field_name, "OP": "IN", "VALUE": values_list}],
        "LIMIT": len(values_list),
        "FIELDS": fields,
    }
    r = requests.post(api_url, headers=headers, json=payload_arr, timeout=timeout)

    # 1回目: 配列
    if 200 <= r.status_code < 300:
        return r.json()

    # 非2xx → DWPI のみ文字列フォールバックを試す
    if field_name == "DWPI_ACCESSION_NUMBER":
        values_join = ",".join(values_list)
        payload_str = {
            "QUERY": [{"ALG": "BASIC", "FIELD": field_name, "OP": "IN", "VALUE": values_join}],
            "LIMIT": len(values_list),
            "FIELDS": fields,
        }
        r2 = requests.post(api_url, headers=headers, json=payload_str, timeout=timeout)
        if 200 <= r2.status_code < 300:
            return r2.json()
        # 文字列でもダメなら詳細を投げる
        raise requests.exceptions.HTTPError(
            f"DWPI fallback also failed. First={r.status_code}:{r.text} Second={r2.status_code}:{r2.text}",
            response=r2
        )

    # PubNo は配列が正しい仕様なので、そのまま例外
    raise requests.exceptions.HTTPError(
        f"Request failed with status {r.status_code}: {r.text}", response=r
    )

# def _post_with_in_fallback(values_list, fields, field_name, headers, timeout):
#     """
#     Clarivate Patents Search API へ POST。
#     - まず VALUE を配列（list[str]）で送る
#     - エラー時、DWPI_ACCESSION_NUMBER のときはカンマ区切り文字列で再送
#     """
#     payload_arr = {
#         "QUERY": [{"ALG": "BASIC", "FIELD": field_name, "OP": "IN", "VALUE": values_list}],
#         "LIMIT": len(values_list),
#         "FIELDS": fields,
#     }
#     r = requests.post(api_url, headers=headers, json=payload_arr, timeout=timeout)
#     if r.status_code >= 500:
#         raise requests.exceptions.HTTPError(f"Server error {r.status_code}: {r.text}", response=r)
#     try:
#         r.raise_for_status()
#         return r.json()
#     except requests.HTTPError as e:
#         if field_name == "DWPI_ACCESSION_NUMBER":
#             values_join = ",".join(values_list)
#             payload_str = {
#                 "QUERY": [{"ALG": "BASIC", "FIELD": field_name, "OP": "IN", "VALUE": values_join}],
#                 "LIMIT": len(values_list),
#                 "FIELDS": fields,
#             }
#             r2 = requests.post(api_url, headers=headers, json=payload_str, timeout=timeout)
#             if r2.status_code >= 500:
#                 raise requests.exceptions.HTTPError(f"Server error {r2.status_code}: {r2.text}", response=r2)
#             r2.raise_for_status()
#             return r2.json()
#         raise e


def fetch_fields_for_numbers(id_list, fields_list, chunk_size=14, field_name="PUBLICATION_NUMBER", headers=None, timeout=(10, 90)):
    """
    指定フィールドを Clarivate Patents Search API から取得
    - id_list: list[str]
    - fields_list: list[str]
    - chunk_size: 1回に要求するフィールド数の上限（field_name を必ず含めるため、実質 chunk_size-1）
    - field_name: "PUBLICATION_NUMBER" or "DWPI_ACCESSION_NUMBER"
    - headers: dict (X-ApiKey など)
    - timeout: (connect, read)
    """


    if not headers or not headers.get("X-ApiKey"):
        raise RuntimeError("X-ApiKey が未設定です（環境変数 IP_DATA_API も空です）。")

    merged = defaultdict(dict)

    # field_name は必ず含める（結合キー）
    base_fields = [f for f in fields_list if f != field_name]
    per_chunk = max(1, chunk_size - 1)
    for fields_chunk in chunked(base_fields, per_chunk):
        fields = [field_name] + list(fields_chunk)

        # 値は配列で渡す（DWPIのみ内部で文字列フォールバック）
        values_list = [str(v).strip() for v in id_list if str(v).strip()]
        js = _post_with_in_fallback(values_list, fields, field_name, headers, timeout)

        rows = js.get("result") if isinstance(js, dict) and "result" in js else None
        if rows is None and isinstance(js, dict):
            for k, v in js.items():
                if isinstance(v, list) and v and isinstance(v[0], dict):
                    rows = v
                    break
        if not rows:
            continue

        for rec in rows:
            key = rec.get(field_name)
            if isinstance(key, str) and key.strip():
                merged[key].update(rec)

    return list(merged.values())


def write_rows_to_csv_string(rows, key_field="PUBLICATION_NUMBER", field_order=None):
    """UTF-8 BOM付きCSV文字列（ダウンロードボタン用）。
    先頭列は key_field を最優先に配置。
    """
    if not rows:
        return ""
    all_keys = set()
    for r in rows:
        all_keys.update(r.keys())

    # 先頭は key_field（存在すれば）
    desired = [key_field]
    # UI 指定順を尊重（key_field は二重追加しない）
    if field_order:
        for f in field_order:
            if f != key_field:
                desired.append(f)

    ordered = [f for f in desired if f in all_keys]
    ordered += sorted(k for k in all_keys if k not in set(ordered))
    fields = ordered

    sio = StringIO()
    writer = csv.DictWriter(sio, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for r in rows:
        row = {}
        for k in fields:
            v = r.get(k, "")
            if isinstance(v, (dict, list)):
                row[k] = json.dumps(v, ensure_ascii=False)
            else:
                row[k] = v
        writer.writerow(row)

    bom = "\ufeff"  # UTF-8 BOM（Excel対策）
    return bom + sio.getvalue()

# ---------------- Run (取得実行) ----------------
if run:
    # 入力の組み立て：アップロードがあれば優先、なければテキストエリア
    if uploaded is not None:
        text = uploaded.read().decode("utf-8")
        input_ids = _normalize_ids(text.splitlines())
    else:
        input_ids = _normalize_ids(ids_text.splitlines())

    if not input_ids:
        st.warning(f"{'Publication number' if field_name=='PUBLICATION_NUMBER' else 'DWPI accession number'} が入力されていません。")
        st.stop()
    if not api_key:
        st.error("X-ApiKey が未設定です。")
        st.stop()

    # ログ初期化（サイドバー）
    st.session_state.log_lines = []
    prog = progress_main.progress(0, text="開始")
    status_main.write("処理を開始しました…")

    combined = defaultdict(dict)
    chunks = list(chunked(input_ids, int(ID_CHUNK)))
    total_chunks = len(chunks)

    for idx, id_chunk in enumerate(chunks, start=1):
        # ログ（サイドバー）
        st.session_state.log_lines.append(f"Processing id chunk {idx} ({len(id_chunk)} items)...")
        log_box_sidebar.text("\n".join(st.session_state.log_lines))

        # メインのステータス更新
        status_main.write(f"Chunk {idx}/{total_chunks} を処理中…（{len(id_chunk)}件）")

        ids_to_fetch = list(id_chunk)

        for attempt in range(1, int(MAX_RETRIES) + 1):
            if not ids_to_fetch:
                break

            # フィールドチャンクを縮小
            if attempt == 1:
                field_chunk_size = int(FIELD_CHUNK)
            elif attempt == 2:
                field_chunk_size = max(1, int(FIELD_CHUNK) // 2)
            elif attempt == 3:
                field_chunk_size = 4
            else:
                field_chunk_size = 2

            st.session_state.log_lines.append(
                f"  Attempt {attempt}: field={field_name}, field_chunk={field_chunk_size}, ids={len(ids_to_fetch)}"
            )
            log_box_sidebar.text("\n".join(st.session_state.log_lines))

            
            # 取得用フィールドの確定（DWPI時は PUBLICATION_NUMBER を除外）
            if field_name == "DWPI_ACCESSION_NUMBER":
                effective_fields = [f for f in selected_fields if f != "PUBLICATION_NUMBER"]
            else:
                effective_fields = selected_fields

            try:
                rows_chunk = fetch_fields_for_numbers(
                    ids_to_fetch,
                    effective_fields, # selected_fields,
                    chunk_size=field_chunk_size,
                    field_name=field_name,
                    headers=build_headers(api_key),
                    timeout=(timeout_connect, timeout_read)
                )
            except Exception as e:
                st.session_state.log_lines.append(f"  Error fetching (attempt {attempt}): {e}")
                log_box_sidebar.text("\n".join(st.session_state.log_lines))
                # 指数バックオフ＋ジッタ（キャップ推奨：最大15秒）
                jitter = random.uniform(-0.15, 0.15)
                sleep_s = min(15, float(backoff_base) * (2 ** attempt) * (1 + jitter))
                time.sleep(sleep_s)
                continue

            # 今回問い合わせたフィールドのみで差し引き（型一致）
            returned_ids = {
                rec[field_name].strip()
                for rec in rows_chunk
                if isinstance(rec.get(field_name), str) and rec[field_name].strip()
            }

            # マージは選択キーを統一
            for rec in rows_chunk:
                key = rec.get(field_name)
                if not key:
                    continue
                combined[key].update(rec)

            # 差し引き
            ids_to_fetch = [i for i in ids_to_fetch if i not in returned_ids]
            if ids_to_fetch:
                st.session_state.log_lines.append(
                    f"  Missing after attempt {attempt}: {len(ids_to_fetch)} items; will retry."
                )
                log_box_sidebar.text("\n".join(st.session_state.log_lines))
                time.sleep(0.8)
            else:
                st.session_state.log_lines.append(f"  All items fetched for chunk {idx}.")
                log_box_sidebar.text("\n".join(st.session_state.log_lines))
                break

        if ids_to_fetch:
            st.session_state.log_lines.append(
                f"Warning: {len(ids_to_fetch)} items not retrieved after {MAX_RETRIES} attempts (chunk {idx})."
            )
            log_box_sidebar.text("\n".join(st.session_state.log_lines))

        prog.progress(idx / total_chunks, text=f"Chunk {idx}/{total_chunks} 完了")

    # 取得結果の保存（session_state）
    rows = list(defaultdict(dict, combined).values())
    if rows:
        df = pd.DataFrame(rows)
        st.session_state.rows = rows
        st.session_state.df = df
        st.session_state.ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        st.session_state.csv_str = write_rows_to_csv_string(
            rows,
            key_field=field_name,
            field_order=effective_fields # selected_fields
        )
        status_main.write("全チャンクの処理が完了しました。")
    else:
        st.session_state.rows = None
        st.session_state.df = None
        st.session_state.csv_str = None
        st.session_state.ts = None
        status_main.write("レコードが返りませんでした。")
        st.warning("レコードが返りませんでした。")

# ---------------- Persistent display (常時表示) ----------------
# ダウンロードクリックで rerun されても、session_state に保存した結果で再描画
if st.session_state.df is not None:
    st.success(f"取得済み：{len(st.session_state.df)} レコード")
    st.dataframe(st.session_state.df, use_container_width=True)
    st.download_button(
        label=f"CSVをダウンロード ({st.session_state.ts}_dsi_output.csv)",
        data=st.session_state.csv_str,
        file_name=f"{st.session_state.ts}_dsi_output.csv",
        mime="text/csv",
        key="download_csv",
    )
