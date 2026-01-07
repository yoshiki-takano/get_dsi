
# post_patent_search_app_main_progress.py
import os, json, time, random, csv
from collections import defaultdict
from datetime import datetime
from io import StringIO
import requests
import streamlit as st
import pandas as pd

# ---------------- Page Config ----------------
st.set_page_config(page_title="DSI Fetcher", layout="wide")

# ---------------- Sidebar: 設定 ----------------
st.sidebar.title("設定")

# API設定
DEFAULT_API_URL = "https://api.clarivate.com/patents/search/"
ALT_API_URL = "https://api.clarivate.com/search/patents/document/json/"  # 必要に応じて切替
api_url = st.sidebar.selectbox("API Endpoint", [DEFAULT_API_URL, ALT_API_URL], index=0)

api_key_env = os.environ.get("IP_DATA_API", "")
api_key = st.sidebar.text_input("X-ApiKey（未入力なら環境変数 IP_DATA_API を使用）", value=api_key_env, type="password")

timeout_connect = st.sidebar.number_input("接続タイムアウト(秒)", min_value=1, value=10)
timeout_read    = st.sidebar.number_input("読み取りタイムアウト(秒)", min_value=10, value=90)

HEADERS = {"Accept": "application/json", "Content-Type": "application/json", "X-ApiKey": api_key}

# 取得フィールド
DEFAULT_FIELDS = [
    "GUID", "DWPI_ACCESSION_NUMBER", "PUBLICATION_NUMBER",
    "DSI_STRENGTH_INDEX",
    "DSI_INVENTION_GLOBALIZATION_SCORE", "DSI_INVENTION_SUCCESS_SCORE", 
    "DSI_INVENTION_INFLUENCE_SCORE", "DSI_TECHNICAL_DISTINCTIVENESS_SCORE",
    "DSI_AVERAGE_SCORE", "DSI_YEARS_REMAINING", "DSI_AGE_DISCOUNT",
]
with st.sidebar.expander("取得フィールドの選択", expanded=True):
    selected_fields = st.multiselect("Fields", DEFAULT_FIELDS, default=DEFAULT_FIELDS)

# パラメータ
st.sidebar.subheader("パラメータ")
ID_CHUNK     = st.sidebar.number_input("ID_CHUNK（IDを分割処理する単位）", min_value=1, value=2000)
FIELD_CHUNK  = st.sidebar.number_input("FIELD_CHUNK（1回に要求するフィールド数）", min_value=1, value=14)
MAX_RETRIES  = st.sidebar.number_input("最大リトライ回数", min_value=1, value=3)
backoff_base = st.sidebar.number_input("バックオフ基数", min_value=1, value=1)

# 進捗・詳細ログ（サイドバー）
log_box_sidebar  = st.sidebar.empty()
st.sidebar.info("Publication number 前提。リトライ時は FIELD_CHUNK を段階的に縮小（例：14→7→4）。")

# ---------------- Main: タイトル・入力・実行・進捗・結果 ----------------
st.title("Derwent Strength Index Fetcher (公報番号のみ対応)")
st.caption("Clarivate Patents Search API を用いて、Publication numberリストから指定フィールドを取得します。")

# 入力（メイン）：ファイルアップロード＋テキスト
st.subheader("入力")
uploaded = st.file_uploader("テキストファイルをアップロード（1行＝1件、# 行はコメント）", type=["txt"])
st.text("または下のテキストボックスに貼り付け（アップロードがあればそちらを優先）")
pubs_text = st.text_area("Publication number（1行＝1件）", height=160,
                         placeholder="例:\nWO2021243294A1\nJP07737400B2\nUS20210374460A1")

# 実行ボタン（メイン）
run = st.button("実行")

# メインの進捗表示（新設）
progress_main = st.empty()     # st.progress を差し込むプレースホルダ
status_main   = st.empty()     # テキストで現在のステータス文を出す

# ---------------- Helpers ----------------
def chunked(iterable, n):
    for i in range(0, len(iterable), n):
        yield iterable[i : i + n]

def fetch_fields_for_numbers(pub_numbers, fields_list, chunk_size=14, field_name="PUBLICATION_NUMBER"):
    """
    Publication number を前提に Clarivate Patents Search API から指定フィールドを取得
    - pub_numbers: list[str]
    - fields_list: list[str]
    - chunk_size: fields を分割する単位（APIのフィールド上限対策）
    - field_name: "PUBLICATION_NUMBER"
    """
    if not api_key:
        raise RuntimeError("X-ApiKey が未設定です（環境変数 IP_DATA_API も空です）。")

    merged = defaultdict(dict)  # key -> record
    for fields_chunk in chunked(fields_list, chunk_size):
        payload = {
            "QUERY": [{"ALG": "BASIC", "FIELD": field_name, "OP": "IN", "VALUE": pub_numbers}],
            "LIMIT": len(pub_numbers),
            "FIELDS": list(fields_chunk),
        }
        r = requests.post(api_url, headers=HEADERS, json=payload, timeout=(timeout_connect, timeout_read))
        if r.status_code >= 500:
            raise requests.exceptions.HTTPError(f"Server error {r.status_code}: {r.text}", response=r)
        r.raise_for_status()
        js = r.json()

        # "result" または配列を持つキーを探索（元ロジック準拠）
        rows = js.get("result") if isinstance(js, dict) and "result" in js else None
        if rows is None:
            for k, v in js.items():
                if isinstance(v, list) and v and isinstance(v[0], dict):
                    rows = v
                    break
        if not rows:
            continue

        for rec in rows:
            key = rec.get("GUID") or rec.get("PUBLICATION_NUMBER")
            if not key:
                continue
            merged[key].update(rec)

    return list(merged.values())

def write_rows_to_csv_string(rows, field_order=None):
    """UTF-8 BOM付きCSV文字列（ダウンロードボタン用）"""
    if not rows:
        return ""
    all_keys = set()
    for r in rows:
        all_keys.update(r.keys())
    if field_order:
        ordered = [f for f in field_order if f in all_keys]
        ordered += sorted(k for k in all_keys if k not in (field_order or []))
        fields = ordered
    else:
        fields = sorted(all_keys)

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

    bom = "\ufeff"  # UTF-8 BOM
    return bom + sio.getvalue()

# ---------------- Run ----------------
if run:
    # 入力の組み立て：アップロードがあれば優先、なければテキストエリア
    if uploaded is not None:
        text = uploaded.read().decode("utf-8")
        pubs = [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.strip().startswith("#")]
    else:
        pubs = [ln.strip() for ln in pubs_text.splitlines() if ln.strip()]

    if not pubs:
        st.warning("Publication number が入力されていません。")
        st.stop()
    if not api_key:
        st.error("X-ApiKey が未設定です。")
        st.stop()

    # 詳細ログ（サイドバー）
    log_lines = []

    # メインの進捗バーを初期化
    prog = progress_main.progress(0, text="開始")
    status_main.write("処理を開始しました…")

    combined = defaultdict(dict)
    chunks = list(chunked(pubs, int(ID_CHUNK)))
    total_chunks = len(chunks)

    for idx, id_chunk in enumerate(chunks, start=1):
        # ログ（サイドバー）
        log_lines.append(f"Processing id chunk {idx} ({len(id_chunk)} items)...")
        log_box_sidebar.text("\n".join(log_lines))

        # メインのステータス更新
        status_main.write(f"Chunk {idx}/{total_chunks} を処理中…（{len(id_chunk)}件）")

        ids_to_fetch = list(id_chunk)

        for attempt in range(1, int(MAX_RETRIES) + 1):
            if not ids_to_fetch:
                break

            # Publication number前提でフィールドチャンクを縮小
            field_name = "PUBLICATION_NUMBER"
            if attempt == 1:
                field_chunk_size = int(FIELD_CHUNK)
            elif attempt == 2:
                field_chunk_size = max(1, int(FIELD_CHUNK) // 2)
            else:
                field_chunk_size = max(1, int(FIELD_CHUNK) // 4)

            # サイドバーのログ
            log_lines.append(
                f"  Attempt {attempt}: field={field_name}, field_chunk={field_chunk_size}, ids={len(ids_to_fetch)}"
            )
            log_box_sidebar.text("\n".join(log_lines))

            try:
                rows_chunk = fetch_fields_for_numbers(
                    ids_to_fetch, selected_fields, chunk_size=field_chunk_size, field_name=field_name
                )
            except Exception as e:
                log_lines.append(f"  Error fetching (attempt {attempt}): {e}")
                log_box_sidebar.text("\n".join(log_lines))
                # 指数バックオフ＋ジッタ
                sleep_s = float(backoff_base) * (2 ** attempt) * (1 + random.uniform(-0.15, 0.15))
                time.sleep(sleep_s)
                continue

            # 今回問い合わせたフィールドのみで差し引き（型一致）
            returned_ids = {
                rec[field_name].strip()
                for rec in rows_chunk
                if isinstance(rec.get(field_name), str) and rec[field_name].strip()
            }

            # 結合（GUID優先→PUB）
            for rec in rows_chunk:
                key = rec.get("GUID") or rec.get("PUBLICATION_NUMBER")
                if not key:
                    continue
                combined[key].update(rec)

            # 差し引き
            ids_to_fetch = [i for i in ids_to_fetch if i not in returned_ids]
            if ids_to_fetch:
                log_lines.append(f"  Missing after attempt {attempt}: {len(ids_to_fetch)} items; will retry.")
                log_box_sidebar.text("\n".join(log_lines))
                time.sleep(0.8)
            else:
                log_lines.append(f"  All items fetched for chunk {idx}.")
                log_box_sidebar.text("\n".join(log_lines))
                break

        if ids_to_fetch:
            log_lines.append(
                f"Warning: {len(ids_to_fetch)} items not retrieved after {MAX_RETRIES} attempts (chunk {idx})."
            )
            log_box_sidebar.text("\n".join(log_lines))

        # メイン進捗バー更新
        prog.progress(idx / total_chunks, text=f"Chunk {idx}/{total_chunks} 完了")

    rows = list(defaultdict(dict, combined).values())
    if rows:
        df = pd.DataFrame(rows)
        status_main.write("全チャンクの処理が完了しました。")
        st.success(f"取得完了：{len(rows)} レコード")
        st.dataframe(df, use_container_width=True)

        # CSVダウンロード（メイン）
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        csv_str = write_rows_to_csv_string(rows, field_order=selected_fields)
        st.download_button(
            label=f"CSVをダウンロード ({ts}_patents.csv)",
            data=csv_str,
            file_name=f"{ts}_patents.csv",
            mime="text/csv",
        )
    else:
        status_main.write("レコードが返りませんでした。")
        st.warning("レコードが返りませんでした。")
