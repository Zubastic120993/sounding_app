import os
import pandas as pd

RAW_DIR = "data/tanks_csv/raw"
NORM_DIR = "data/tanks_csv/normalized"

os.makedirs(NORM_DIR, exist_ok=True)

def normalize_file(file_path: str):
    name = os.path.splitext(os.path.basename(file_path))[0]

    # Read CSV with ; separator
    df = pd.read_csv(file_path, sep=";")

    # Base volume table (just sounding + VTr=0)
    base = df[["SND", "VTr=0"]].copy()
    base.columns = ["sounding_cm", "volume_m3"]
    base.insert(0, "name", name)
    base.to_csv(os.path.join(NORM_DIR, f"{name}_volume.csv"), index=False)

    # Trims
    trims = [c for c in df.columns if c.startswith("VTr=") and c != "VTr=0"]
    trim_rows = []
    for _, row in df.iterrows():
        for col in trims:
            trim_rows.append([name, row["SND"], col.replace("VTr=", ""), row[col]])
    trim_df = pd.DataFrame(trim_rows, columns=["name", "sounding_cm", "trim_code", "delta_m3"])
    trim_df.to_csv(os.path.join(NORM_DIR, f"{name}_trim.csv"), index=False)

    # Heels
    heels = [c for c in df.columns if c.startswith("HC.")]
    heel_rows = []
    for _, row in df.iterrows():
        for col in heels:
            heel_rows.append([name, row["SND"], col.replace("HC.", ""), row[col]])
    heel_df = pd.DataFrame(heel_rows, columns=["name", "sounding_cm", "heel_code", "delta_m3"])
    heel_df.to_csv(os.path.join(NORM_DIR, f"{name}_heel.csv"), index=False)

    print(f"✓ Normalized {name}")

if __name__ == "__main__":
    for f in os.listdir(RAW_DIR):
        if f.lower().endswith(".csv"):
            normalize_file(os.path.join(RAW_DIR, f))