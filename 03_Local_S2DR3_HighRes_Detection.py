import rasterio
import cv2
import numpy as np
import geopandas as gpd
from shapely.geometry import shape
import rasterio.features
import os
import math

# ==========================================
# 1. KONFIGURASI FILE
# ==========================================
input_tif = r"T49LHL-55846db01-20251212T080403Z-3-001\T49LHL-55846db01\S2L2Ax10_T49LHL-55846db01-20240718_MS.tif"
output_dir = r"path/to/your/folder" # Ganti dengan lokasi file Anda
output_shp_name = f"S2DR3_Tambak.shp" # Nama File Baru

# ==========================================
# 2. KONFIGURASI PARAMETER (EXTREME LOOSE)
# ==========================================
BAND_GREEN_IDX = 2
BAND_RED_IDX   = 3
BAND_NIR_IDX   = 4 

# [PERUBAHAN 1] NDWI SANGAT LONGGAR
# Nilai -0.1 akan menangkap air yang sangat keruh/berlumpur.
# Risiko: Sawah basah akan masuk (tapi nanti kita cek di Precision).
THRESH_NDWI = -0.10 

# [PERUBAHAN 2] NDVI LONGGAR
# Naikkan ke 0.35. Banyak tambak produktif itu hijau pekat (full algae).
# Jika diset 0.15 atau 0.25, tambak produktif ini dianggap tanaman.
MAX_NDVI = 0.35     

# [PERUBAHAN 3] NIR LONGGAR
# Naikkan ke 3500. Tambak yang sedang persiapan (tanah basah/dangkal) itu terang.
MAX_NIR_VALUE = 3500 

# [PERUBAHAN 4] GEOMETRI SANGAT LONGGAR
MIN_LUAS = 300      
MAX_LUAS = 150000   # Naikkan dikit siapa tahu ada tambak raksasa
# LSI 2.5 mengizinkan bentuk yang sangat kasar/bergerigi masuk.
MAX_LSI  = 2.5      
MAX_RPOC = 1.8      

# ==========================================
# 3. FUNGSI BANTUAN
# ==========================================
def calculate_lsi(geometry):
    area = geometry.area
    perimeter = geometry.length
    if area <= 0: return 999
    return perimeter / (4 * np.sqrt(area))

def calculate_rpoc(geometry):
    perimeter_asli = geometry.length
    hull = geometry.convex_hull
    perimeter_hull = hull.length
    if perimeter_hull <= 0: return 999
    return perimeter_asli / perimeter_hull

# ==========================================
# 4. EKSEKUSI
# ==========================================
if not os.path.exists(output_dir): os.makedirs(output_dir)
output_path = os.path.join(output_dir, output_shp_name)

print(f"--- MULAI DETEKSI V3 (Target: RECALL NAIK) ---")

try:
    with rasterio.open(input_tif) as src:
        transform = src.transform
        crs = src.crs
        
        green = src.read(BAND_GREEN_IDX).astype(float)
        red   = src.read(BAND_RED_IDX).astype(float)
        nir   = src.read(BAND_NIR_IDX).astype(float)
        
        # Cek Tipe Data
        max_val_img = np.max(nir)
        if max_val_img <= 1.0:
            # Float 0-1
            current_max_nir = 0.35
            print("Mode: Reflectance (0-1)")
        else:
            # Integer
            current_max_nir = MAX_NIR_VALUE
            print(f"Mode: Digital Number (Max NIR Filter: {current_max_nir})")

        # --- INDEXING ---
        print("2. Menghitung Index...")
        denom_ndwi = green + nir
        denom_ndwi[denom_ndwi == 0] = 0.001 
        ndwi = (green - nir) / denom_ndwi
        
        denom_ndvi = nir + red
        denom_ndvi[denom_ndvi == 0] = 0.001
        ndvi = (nir - red) / denom_ndvi
        
        # --- FILTERING ---
        print(f"3. Applying Loose Filters (NDWI>{THRESH_NDWI}, NDVI<{MAX_NDVI})...")
        
        mask_air = (ndwi > THRESH_NDWI)
        mask_non_veg = (ndvi < MAX_NDVI)
        mask_non_bright = (nir < current_max_nir)
        
        final_mask_bool = mask_air & mask_non_veg & mask_non_bright
        mask_uint8 = final_mask_bool.astype(np.uint8)

        # --- CLEANING ---
        print("4. Cleaning Noise...")
        kernel = np.ones((3,3), np.uint8)
        clean_mask = cv2.morphologyEx(mask_uint8, cv2.MORPH_OPEN, kernel)
        clean_mask = cv2.morphologyEx(clean_mask, cv2.MORPH_CLOSE, kernel)

        # --- VEKTORISASI & SMOOTHING ---
        print("5. Vektorisasi & Smoothing...")
        shapes = rasterio.features.shapes(clean_mask, transform=transform)
        
        polygons = []
        count_total = 0
        count_lolos = 0
        
        for geom, value in shapes:
            if value == 1: 
                raw_poly = shape(geom)
                
                # Buffer Smoothing Tetap Dipakai (Wajib untuk LSI)
                # +2m lalu -2m
                buffered_poly = raw_poly.buffer(2.0, join_style=1) 
                final_poly = buffered_poly.buffer(-2.0, join_style=1).simplify(0.5)
                
                area = final_poly.area
                count_total += 1
                
                if MIN_LUAS <= area <= MAX_LUAS:
                    lsi_val = calculate_lsi(final_poly)
                    rpoc_val = calculate_rpoc(final_poly)
                    
                    # Filter Geometri (LONGGAR)
                    if lsi_val <= MAX_LSI and rpoc_val <= MAX_RPOC:
                        polygons.append({
                            'geometry': final_poly,
                            'area_m2': area,
                            'LSI': round(lsi_val, 3),
                            'RPOC': round(rpoc_val, 3),
                            'Ket': 'Tambak'
                        })
                        count_lolos += 1

    # --- SIMPAN ---
    print(f"   Total Kandidat Awal: {count_total}")
    print(f"   Lolos Final: {count_lolos}")

    if len(polygons) > 0:
        gdf = gpd.GeoDataFrame(polygons, crs=crs)
        gdf.to_file(output_path)
        print(f"\n[SUKSES] File V3 tersimpan di: {output_path}")
    else:
        print("\n[INFO] Tidak ada objek yang lolos filter.")

except Exception as e:
    print(f"\n[ERROR] Terjadi kesalahan: {e}")