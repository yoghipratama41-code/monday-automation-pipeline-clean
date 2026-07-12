"""
GNS Data Cleaner Automation — Streamlit version
------------------------------------------------
Mengambil data dari Google Sheets sumber (mis. SG2, MY2), membersihkannya
lewat website gns-data-cleaner-v2.vercel.app menggunakan Selenium, lalu
mengunggah hasil bersih ke sheet tujuan (mis. SG3, MY3).

Cara jalankan:
    streamlit run streamlit_app.py

Dependencies (requirements.txt):
    streamlit
    selenium
    gspread
    pandas
    webdriver-manager
"""

import os
import tempfile

import pandas as pd
import gspread
import streamlit as st
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager

st.set_page_config(page_title="GNS Data Cleaner Automation", page_icon="🧹", layout="centered")

st.title("🧹 GNS Data Cleaner Automation")
st.caption(
    "Ambil data dari Google Sheets → bersihkan lewat GNS Data Cleaner → "
    "unggah kembali ke Sheets."
)

# ------------------------------------------------------------------
# 1. Pengaturan koneksi
# ------------------------------------------------------------------
# Cek apakah service account sudah dikonfigurasi lewat st.secrets
# (format: [gcp_service_account] di secrets.toml, sama seperti biasa)
has_secret_creds = "gcp_service_account" in st.secrets

with st.expander("⚙️ Pengaturan Koneksi", expanded=True):
    if has_secret_creds:
        st.success("🔐 Credentials ditemukan di st.secrets — tidak perlu upload manual.")
        cred_file = None
    else:
        st.info("Tidak ada `gcp_service_account` di st.secrets, silakan upload credentials.json.")
        cred_file = st.file_uploader(
            "Upload credentials.json (Google Service Account)", type="json"
        )
    sheet_url = st.text_input(
        "URL Google Spreadsheet",
        value="https://docs.google.com/spreadsheets/d/1wLdsADx0W3IRcA6lGK1kjaSrDci7Z9QqCM7iSU-64TU/",
    )
    pipeline_url = st.text_input(
        "URL GNS Data Cleaner", value="https://gns-data-cleaner-v2.vercel.app/"
    )
    headless = st.checkbox(
        "Jalankan browser headless (disarankan bila di-deploy di server)", value=True
    )

# ------------------------------------------------------------------
# 2. Sesi yang mau dijalankan (source sheet -> target sheet)
# ------------------------------------------------------------------
st.subheader("Pilih Sesi yang Ingin Dijalankan")

default_sessions = [
    {"source": "SG2", "target": "SG3", "label": "SG"},
    {"source": "MY2", "target": "MY3", "label": "MY"},
]

h1, h2, h3, h4 = st.columns([0.6, 1, 1, 1])
h1.markdown("**Aktif**")
h2.markdown("**Sheet Sumber**")
h3.markdown("**Sheet Tujuan**")
h4.markdown("**Label**")

session_rows = []
for i, sess in enumerate(default_sessions):
    c1, c2, c3, c4 = st.columns([0.6, 1, 1, 1])
    active = c1.checkbox("", value=True, key=f"active_{i}", label_visibility="collapsed")
    source = c2.text_input("", value=sess["source"], key=f"source_{i}", label_visibility="collapsed")
    target = c3.text_input("", value=sess["target"], key=f"target_{i}", label_visibility="collapsed")
    label = c4.text_input("", value=sess["label"], key=f"label_{i}", label_visibility="collapsed")
    if active:
        session_rows.append((source.strip(), target.strip(), label.strip()))

st.divider()
run_btn = st.button("🚀 Jalankan Automation", type="primary", use_container_width=True)

log_container = st.container()
result_container = st.container()


def log(msg: str):
    log_container.write(msg)


def run_pipeline(driver, wait, csv_upload_path, download_dir, label, pipeline_url, log_fn):
    driver.get(pipeline_url)

    # a. Pilih "Grab only"
    grab_only_btn = wait.until(
        EC.element_to_be_clickable((By.XPATH, "//div[contains(text(), 'Grab only')]"))
    )
    grab_only_btn.click()
    log_fn(f"[{label}] Memilih 'Grab only'...")

    # b. Upload file CSV
    file_input = wait.until(EC.presence_of_element_located((By.XPATH, "//input[@type='file']")))
    file_input.send_keys(csv_upload_path)
    log_fn(f"[{label}] Mengunggah file CSV...")

    # c. Klik "Run Pipeline"
    run_btn_el = wait.until(
        EC.element_to_be_clickable((By.XPATH, "//button[contains(text(), 'Run Pipeline')]"))
    )
    run_btn_el.click()
    log_fn(f"[{label}] Menjalankan pipeline, mohon tunggu...")

    # d. Tunggu tombol download muncul
    download_anchor = wait.until(
        EC.presence_of_element_located((By.CSS_SELECTOR, "a.dl-btn[download]"))
    )
    log_fn(f"[{label}] Tombol download ditemukan!")

    # e. Ambil blob URL dan fetch isinya
    blob_url = download_anchor.get_attribute("href")
    csv_text = driver.execute_script(
        """
        const url = arguments[0];
        return await fetch(url)
            .then(r => r.text())
            .catch(e => 'ERROR: ' + e);
        """,
        blob_url,
    )

    if csv_text.startswith("ERROR"):
        raise Exception(f"[{label}] Gagal fetch blob: {csv_text}")

    # f. Simpan ke file lokal
    clean_csv_path = os.path.join(download_dir, f"gns-{label.lower()}-cleaned.csv")
    with open(clean_csv_path, "w", encoding="utf-8") as f:
        f.write(csv_text)
    log_fn(f"[{label}] File berhasil disimpan di `{clean_csv_path}`")

    return clean_csv_path


# ------------------------------------------------------------------
# 3. Eksekusi
# ------------------------------------------------------------------
if run_btn:
    if not has_secret_creds and cred_file is None:
        st.error("Silakan upload file credentials.json terlebih dahulu.")
        st.stop()
    if not sheet_url.strip():
        st.error("Silakan isi URL Google Spreadsheet.")
        st.stop()
    if not session_rows:
        st.error("Pilih minimal satu sesi untuk dijalankan.")
        st.stop()

    with tempfile.TemporaryDirectory() as tmp_dir:
        # Hubungkan ke Google Sheets
        try:
            log("🔗 Menghubungkan ke Google Sheets...")
            if has_secret_creds:
                creds_dict = dict(st.secrets["gcp_service_account"])
                gc = gspread.service_account_from_dict(creds_dict)
            else:
                cred_path = os.path.join(tmp_dir, "credentials.json")
                with open(cred_path, "wb") as f:
                    f.write(cred_file.getbuffer())
                gc = gspread.service_account(filename=cred_path)
            sh = gc.open_by_url(sheet_url)
            log("✅ Berhasil terhubung ke Google Sheets.")
        except Exception as e:
            st.error(f"Gagal menghubungkan ke Google Sheets: {e}")
            st.stop()

        # Setup browser
        log("🌐 Menyiapkan browser...")
        options = webdriver.ChromeOptions()
        if headless:
            options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        prefs = {
            "download.default_directory": tmp_dir,
            "download.prompt_for_download": False,
            "directory_upgrade": True,
        }
        options.add_experimental_option("prefs", prefs)

        # Di server minimal (mis. Streamlit Community Cloud), pakai Chromium +
        # chromedriver yang di-install lewat packages.txt (apt), karena binary
        # hasil download webdriver-manager sering tidak jalan di container
        # (exit code 127 / library tidak lengkap). Kalau tidak ada, fallback
        # ke webdriver-manager untuk pengembangan lokal.
        system_chromium_paths = ["/usr/bin/chromium", "/usr/bin/chromium-browser"]
        system_driver_paths = ["/usr/bin/chromedriver"]

        chromium_binary = next((p for p in system_chromium_paths if os.path.exists(p)), None)
        chromedriver_binary = next((p for p in system_driver_paths if os.path.exists(p)), None)

        try:
            if chromium_binary and chromedriver_binary:
                log(f"🧭 Menggunakan Chromium sistem: `{chromium_binary}`")
                options.binary_location = chromium_binary
                driver = webdriver.Chrome(
                    service=Service(chromedriver_binary), options=options
                )
            else:
                log("🧭 Chromium sistem tidak ditemukan, mengunduh lewat webdriver-manager (mode lokal)...")
                driver = webdriver.Chrome(
                    service=Service(ChromeDriverManager().install()), options=options
                )
        except Exception as e:
            st.error(f"Gagal menjalankan Chrome WebDriver: {e}")
            st.stop()

        wait = WebDriverWait(driver, 120)
        cleaned_files = []
        progress = st.progress(0)
        total = len(session_rows)

        try:
            for idx, (sheet_source, sheet_target, label) in enumerate(session_rows):
                log(f"\n**[{label}]** Mengambil data dari `{sheet_source}`...")
                sheet_src = sh.worksheet(sheet_source)
                df = pd.DataFrame(sheet_src.get_all_records())

                csv_upload_path = os.path.join(tmp_dir, f"{label.lower()}_temp.csv")
                df.to_csv(csv_upload_path, index=False)
                log(f"[{label}] Data disimpan sementara ({len(df)} baris).")

                clean_csv_path = run_pipeline(
                    driver, wait, csv_upload_path, tmp_dir, label, pipeline_url, log
                )

                log(f"[{label}] Mengunggah data ke `{sheet_target}`...")
                df_clean = pd.read_csv(clean_csv_path).fillna("")
                sheet_tgt = sh.worksheet(sheet_target)
                sheet_tgt.clear()
                sheet_tgt.update([df_clean.columns.values.tolist()] + df_clean.values.tolist())
                log(f"✅ **[{label}]** Selesai! Data bersih dipindahkan ke `{sheet_target}`.")

                cleaned_files.append((label, clean_csv_path, df_clean))
                progress.progress((idx + 1) / total)

            st.success("🎉 Semua sesi selesai diproses!")

            for label, path, df_clean in cleaned_files:
                with result_container.expander(f"📄 Hasil {label} ({len(df_clean)} baris)"):
                    st.dataframe(df_clean, use_container_width=True)
                    with open(path, "rb") as f:
                        st.download_button(
                            f"⬇️ Download CSV {label}",
                            data=f.read(),
                            file_name=os.path.basename(path),
                            mime="text/csv",
                            key=f"dl_{label}",
                        )

        except Exception as e:
            st.error(f"Terjadi kesalahan saat menjalankan pipeline: {e}")
        finally:
            driver.quit()
            log("\n🔒 Browser ditutup. Semua sesi selesai.")
