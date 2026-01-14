import ee
import geemap
import os

# --- 1. INISIALISASI ---
try:
    ee.Initialize()
    print("Berhasil terhubung ke Google Earth Engine.")
except Exception as e:
    ee.Authenticate()
    ee.Initialize()
    print("Berhasil terhubung setelah autentikasi.")

# --- 2. CONFIG AREA & WAKTU ---
area_geometry = ee.Geometry.Rectangle([114.368739,-8.263853,114.382801,-8.243553]) # Ganti dengan lokasi file Anda
start_date = '2024-01-01'
end_date = '2024-12-31'

# Gunakan UTM 50S (Meter) untuk akurasi luas
proj_metric = ee.Projection('EPSG:32750') # Ganti dengan lokasi file Anda

# Output
output_dir = r"path/to/your/folder" # Ganti dengan lokasi file Anda
if not os.path.exists(output_dir): os.makedirs(output_dir)

# --- 3. FUNGSI

def mask_clouds_scl(image):
    """Masking awan Sentinel-2."""
    scl = image.select('SCL')
    mask = scl.neq(3).And(scl.neq(8)).And(scl.neq(9)).And(scl.neq(10)).And(scl.neq(11))
    return image.updateMask(mask).divide(10000)

def add_ndwi(image):
    """NDWI Standard."""
    ndwi = image.normalizedDifference(['B3', 'B8']).rename('NDWI')
    return image.addBands(ndwi)

def z_score_cleaning(collection, geometry):
    """Membersihkan outlier NDWI."""
    mean = collection.select('NDWI').mean()
    std = collection.select('NDWI').reduce(ee.Reducer.stdDev())
    upper_limit = mean.add(std.multiply(2))
    max_ndwi = collection.select('NDWI').max()
    return max_ndwi.where(max_ndwi.gt(upper_limit), upper_limit).clip(geometry)

def calculate_shape_metrics(feature):
    """Hitung Luas, LSI, RPOC."""
    geom = feature.geometry()
    # maxError=1 penting agar tidak crash
    area = geom.area(1, proj=proj_metric)
    perimeter = geom.perimeter(1, proj=proj_metric)
    hull = geom.convexHull()
    perimeter_hull = hull.perimeter(1, proj=proj_metric)
    
    lsi = perimeter.multiply(0.25).divide(area.sqrt())
    rpoc = perimeter.divide(perimeter_hull)
    return feature.set({'area_m2': area, 'LSI': lsi, 'RPOC': rpoc})

def calculate_median_values(feature, ndwi_img, radar_img):
    """Hitung rata-rata NDWI dan Radar di dalam poligon."""
    # Hitung NDWI
    ndwi_val = ndwi_img.reduceRegion(
        reducer=ee.Reducer.median(), geometry=feature.geometry(), scale=10, maxPixels=1e9
    ).get('NDWI')
    
    # Hitung Radar VV
    radar_val = radar_img.reduceRegion(
        reducer=ee.Reducer.median(), geometry=feature.geometry(), scale=10, maxPixels=1e9
    ).get('VV')
    
    return feature.set({'median_ndwi': ndwi_val, 'median_vv': radar_val})

def calculate_crop_overlap(feature):
    """Cek tumpang tindih dengan lahan pertanian (ESA WorldCover)."""
    esa = ee.ImageCollection("ESA/WorldCover/v100").first()
    is_crop = esa.eq(40) 
    crop_pct = is_crop.reduceRegion(
        reducer=ee.Reducer.mean(), geometry=feature.geometry(), scale=10, maxPixels=1e9
    ).get('Map')
    return feature.set('crop_pct', crop_pct)

# --- 4. PRE-PROCESSING DATA ---

print("1. Menyiapkan Data Sentinel-2 (Optik)...")
s2_col = (ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED') 
        .filterBounds(area_geometry)
        .filterDate(start_date, end_date)
        .map(mask_clouds_scl)
        .map(add_ndwi))

# NDWI Bersih (Z-Score)
ndwi_clean = z_score_cleaning(s2_col, area_geometry)

print("2. Menyiapkan Data Sentinel-1 (Radar)...")
s1_col = (ee.ImageCollection('COPERNICUS/S1_GRD')
        .filterBounds(area_geometry)
        .filterDate(start_date, end_date)
        .filter(ee.Filter.listContains('transmitterReceiverPolarisation', 'VV'))
        .filter(ee.Filter.eq('instrumentMode', 'IW')))

# Ambil Median Tahunan & Smoothing 
s1_base = s1_col.select('VV').median().clip(area_geometry)
s1_smooth = s1_base.focal_median(15, 'circle', 'meters')

# --- 5. HYBRID SEEDING (GABUNGAN OPTIK & RADAR) ---
print("3. Membuat Masker Hybrid (Bibit Tambak)...")

# Syarat 1: Air Optik (NDWI > 0)
mask_optik = ndwi_clean.gte(0)

# Syarat 2: Air Radar (VV < -13.5 dB)
mask_radar = s1_smooth.lt(-13.5)

# Fusi: Ambil jika Optik OK -ATAU- Radar OK
# Ini menyelamatkan tambak keruh yang tidak terlihat di Optik
hybrid_water_mask = mask_optik.Or(mask_radar).selfMask().rename('WaterBinary')

# Bersihkan noise kecil (< 10 pixel)
hybrid_water_mask = hybrid_water_mask.updateMask(hybrid_water_mask.connectedPixelCount(10, True).gte(10))

# --- 6. SEGMENTASI ITERATIF (SOAP METHOD) ---
print("4. Segmentasi Iteratif (SOAP)...")

# Kita gunakan NDWI Optik sebagai panduan "pisau pemotong" (Edges) 
# karena resolusi spasial optik lebih tajam daripada radar.
current_image = ndwi_clean
current_mask = hybrid_water_mask
accumulated_edges = ee.Image.constant(0)
valid_ponds_list = []

for i in range(3):
    print(f"   Iterasi ke-{i+1}...")
    kernel_radius = 1.5 + (i * 0.5) 
    
    # Morfologi untuk memperjelas batas
    morph_image = current_image.focal_min(radius=kernel_radius, kernelType='square', units='pixels')
    
    # Canny Edge Detector (Threshold 0.1 agar sensitif)
    canny = ee.Algorithms.CannyEdgeDetector(image=morph_image, threshold=0.1, sigma=1)
    accumulated_edges = accumulated_edges.max(canny)
    
    # Potong Masker Hybrid dengan Garis Tepi Optik
    segments = current_mask.where(accumulated_edges.eq(1), 0).selfMask()
    
    # Vektorisasi
    vectors = segments.reduceToVectors(
        geometry=area_geometry, scale=10, maxPixels=1e9, eightConnected=False, crs=proj_metric
    )
    
    # Hitung Metrik Bentuk
    processed_vec = vectors.map(calculate_shape_metrics)
    
    # Filter Bentuk (Dilonggarkan sedikit agar tambak lonjong masuk)
    good_ponds = processed_vec.filter(ee.Filter.And(
        ee.Filter.lte('LSI', 3.0), # Naik dari 2.5
        ee.Filter.lte('RPOC', 1.8) # Naik dari 1.5
    ))
    valid_ponds_list.append(good_ponds)

# --- 7. FILTERING LANJUTAN (DECISION TREE) ---
print("5. Filtering Cerdas (Hybrid Validation)...")
obj = ee.FeatureCollection(valid_ponds_list).flatten()

# A. Filter Luas (300 m2 - 50 Ha)
obj = obj.filter(ee.Filter.lte('area_m2', 500000)).filter(ee.Filter.gte('area_m2', 300))

# B. Filter Nilai (Median NDWI & Radar) - INI KUNCINYA!
obj = obj.map(lambda f: calculate_median_values(f, ndwi_clean, s1_smooth))

# LOGIKA BARU: 
# Lolos jika (NDWI Jernih > 0.05) ATAU (Radar Gelap < -13)
# Jadi kalau airnya keruh (NDWI jelek), dia tetap lolos asalkan Radarnya bagus.
obj = obj.filter(ee.Filter.Or(
    ee.Filter.gte('median_ndwi', 0.05),
    ee.Filter.lte('median_vv', -13)
))

# C. Filter Cropland (Buang Sawah)
obj = obj.map(calculate_crop_overlap)
obj = obj.filter(ee.Filter.lt('crop_pct', 0.5))

# D. Filter Tetangga (Minimal 1 Teman)
spatial_filter = ee.Filter.withinDistance(100, '.geo', None, '.geo')
join = ee.Join.saveAll('neighbors')
obj_with_neighbors = join.apply(obj, obj, spatial_filter)
final_result = obj_with_neighbors.map(lambda f: f.set('near_num', ee.List(f.get('neighbors')).size())).filter(ee.Filter.gte('near_num', 2)) # Min 2 (diri sendiri + 1 teman)

# Buffer
final_result = final_result.map(lambda f: f.buffer(2))

print(f"SELESAI. Total Tambak Valid: {final_result.size().getInfo()}")

# --- 8. VISUALISASI ---
Map = geemap.Map()
Map.centerObject(area_geometry, 14)

# Visual RGB
s2_vis = s2_col.median().clip(area_geometry).visualize(min=0, max=0.3, bands=['B4','B3','B2'])
Map.addLayer(s2_vis, {}, 'Sentinel-2 RGB')

# Visual Radar
Map.addLayer(s1_smooth, {'min':-25, 'max':0}, 'Sentinel-1 Smooth')

# Visual Binary Mask (Hybrid)
Map.addLayer(hybrid_water_mask, {'palette':['cyan']}, 'Hybrid Water Mask')

# Visual Hasil
Map.addLayer(final_result, {'color': 'red', 'width': 2}, 'Deteksi Tambak (SOAP + Radar)')

# --- 9. EKSPOR ---
print("Mengekspor...")
try:
    out_shp = os.path.join(output_dir, 'nama_output_file.shp') # Ganti nama file Anda
    cols = ['area_m2', 'LSI', 'RPOC', 'median_ndwi', 'median_vv']
    geemap.ee_to_shp(final_result.select(cols), filename=out_shp)
    print(f"SHP Tersimpan: {out_shp}")
except Exception as e:
    print(f"Gagal Ekspor: {e}")

Map